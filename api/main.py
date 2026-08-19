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
DB_PASSWORD = os.getenv("DB_PASSWORD", "DeinSicheresPasswort123!")
API_TOKEN = os.getenv("API_TOKEN", "DeinGeheimerApiToken456!")

def get_db_conn():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )

def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization Header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid API Token")
    return token

class Record(BaseModel):
    id: Optional[int] = None
    timestamp: str
    channel: int
    temperature: float
    job_id: Optional[str] = None

class IngestPayload(BaseModel):
    device_id: str
    records: List[Record]

@app.on_event("startup")
def init_db():
    conn = get_db_conn()
    cur = conn.cursor()
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
    conn.commit()
    cur.close()
    conn.close()

@app.post("/api/v1/telemetry/ingest", dependencies=[Depends(verify_token)])
def ingest_telemetry(payload: IngestPayload):
    if not payload.records:
        return {"status": "ok", "inserted": 0, "commands": []}
    
    conn = get_db_conn()
    cur = conn.cursor()
    
    query = """
        INSERT INTO telemetry_data (time, device_id, channel, temperature, job_id)
        VALUES %s
        ON CONFLICT (time, device_id, channel) DO UPDATE 
        SET temperature = EXCLUDED.temperature, job_id = EXCLUDED.job_id;
    """
    
    values = [
        (r.timestamp, payload.device_id, r.channel, r.temperature, r.job_id)
        for r in payload.records
    ]
    
    execute_values(cur, query, values)
    conn.commit()
    cur.close()
    conn.close()
    
    return {
        "status": "ok",
        "inserted": len(values),
        "commands": []
    }
