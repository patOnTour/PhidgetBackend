import os
import psycopg2
from psycopg2.extras import execute_values
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="Telemetry Ingest API")

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
API_TOKEN = os.getenv("API_TOKEN", "DeinGeheimerApiToken456!")


def get_db_conn():
  return psycopg2.connect(
      host=DB_HOST,
      port=DB_PORT,
      dbname=DB_NAME,
      user=DB_USER,
      password=DB_PASSWORD,
  )


def verify_token(authorization: Optional[str] = Header(None)):
  if not authorization:
    raise HTTPException(status_code=401, detail="Missing Authorization Header")
  scheme, _, token = authorization.partition(" ")
  if scheme.lower() != "bearer" or token != API_TOKEN:
    raise HTTPException(status_code=403, detail="Invalid API Token")
  return token


class Record(BaseModel):
  timestamp: str
  channel: int
  temperature: float
  job_id: Optional[str] = None


class IngestPayload(BaseModel):
  device_id: str
  records: List[Record]


class StartChannelRequest(BaseModel):
  device_id: str
  channel: int


@app.on_event("startup")
def init_db():
  with get_db_conn() as conn:
    with conn.cursor() as cur:
      cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
      cur.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_data (
                    time TIMESTAMPTZ NOT NULL,
                    device_id TEXT NOT NULL,
                    channel INT NOT NULL,
                    temperature DOUBLE PRECISION NOT NULL,
                    job_id TEXT,
                    PRIMARY KEY (time, device_id, channel)
                );
            """)
      cur.execute("""
                SELECT create_hypertable('telemetry_data', 'time', if_not_exists => TRUE);
            """)
      cur.execute("""
                CREATE TABLE IF NOT EXISTS analyzer_state (
                    device_id TEXT NOT NULL,
                    channel INT NOT NULL,
                    job_id TEXT NOT NULL,
                    started_at TIMESTAMPTZ,
                    turnaround_sent BOOLEAN DEFAULT FALSE,
                    trigger_sent BOOLEAN DEFAULT FALSE,
                    export_30_sent BOOLEAN DEFAULT FALSE,
                    export_120_sent BOOLEAN DEFAULT FALSE,
                    force_export BOOLEAN DEFAULT FALSE,
                    t_min_temp DOUBLE PRECISION,
                    t_min_time TIMESTAMPTZ,
                    t_ab_temp DOUBLE PRECISION,
                    t_ab_time TIMESTAMPTZ,
                    last_evaluated TIMESTAMPTZ,
                    PRIMARY KEY (device_id, channel, job_id)
                );
            """)
      cur.execute("""
                CREATE TABLE IF NOT EXISTS alerts_history (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    device_id TEXT NOT NULL,
                    channel INT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_time TIMESTAMPTZ NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    acknowledged BOOLEAN DEFAULT FALSE
                );
            """)
      conn.commit()


@app.post("/api/v1/telemetry/ingest", dependencies=[Depends(verify_token)])
def ingest_telemetry(payload: IngestPayload):
  if not payload.records:
    return {"status": "ok", "inserted": 0, "commands": []}

  unique_map = {}
  for r in payload.records:
    key = (r.timestamp, payload.device_id, r.channel)
    unique_map[key] = (
        r.timestamp,
        payload.device_id,
        r.channel,
        r.temperature,
        r.job_id,
    )

  values = list(unique_map.values())

  query = """
        INSERT INTO telemetry_data (time, device_id, channel, temperature, job_id)
        VALUES %s
        ON CONFLICT (time, device_id, channel) DO UPDATE 
        SET temperature = EXCLUDED.temperature, job_id = EXCLUDED.job_id;
    """

  with get_db_conn() as conn:
    with conn.cursor() as cur:
      execute_values(cur, query, values)
      conn.commit()

  return {"status": "ok", "inserted": len(values)}


@app.post("/api/v1/control/start-channel")
def control_start_channel(payload: StartChannelRequest):
  job_id = f"ch{payload.channel}"
  with get_db_conn() as conn:
    with conn.cursor() as cur:
      cur.execute(
          """
                INSERT INTO analyzer_state (
                    device_id, channel, job_id, started_at, 
                    turnaround_sent, trigger_sent, export_30_sent, export_120_sent, force_export
                )
                VALUES (%s, %s, %s, NOW(), false, false, false, false, false)
                ON CONFLICT (device_id, channel, job_id)
                DO UPDATE SET 
                    started_at = NOW(), 
                    turnaround_sent = false, 
                    trigger_sent = false, 
                    export_30_sent = false, 
                    export_120_sent = false,
                    force_export = false,
                    t_min_temp = NULL,
                    t_min_time = NULL,
                    t_ab_temp = NULL,
                    t_ab_time = NULL,
                    last_evaluated = NOW();
            """,
          (payload.device_id, payload.channel, job_id),
      )
      conn.commit()

  return {
      "status": "success",
      "device_id": payload.device_id,
      "channel": payload.channel,
  }