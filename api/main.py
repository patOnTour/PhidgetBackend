"""
@file: main.py
@version: 1.3.0
@date: 2026-08-29
@description: Ingest-API ohne DDL-Startup-Logik (Schema as Code via db/init/01_schema.sql), timing-resistentem Token-Vergleich und modularer YAML-Unterstuetzung.
@author: Patrick Staehli
"""

import os
import glob
import secrets
import yaml
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="Telemetry Ingest API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://telemetry.concretum-setting.com",
        "http://parzival.lan:8080",
        "http://localhost:8000",
        "http://localhost:8080"
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")

DB_PASSWORD = os.getenv("DB_PASSWORD")
if not DB_PASSWORD:
    raise RuntimeError("Umgebungsvariable DB_PASSWORD ist nicht gesetzt!")

API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError("Umgebungsvariable API_TOKEN ist nicht gesetzt!")

CONFIG_DIR = os.getenv("YAML_CONFIG_PATH", "/app/config")
DEVICES_DIR = os.path.join(CONFIG_DIR, "devices") if os.path.isdir(os.path.join(CONFIG_DIR, "devices")) else CONFIG_DIR

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
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, API_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid API Token")
    return token

def is_channel_recording_allowed(device_id: str) -> bool:
    clean_id = str(device_id).strip().lower()
    try:
        direct_file = os.path.join(DEVICES_DIR, f"{device_id}.yaml")
        if os.path.exists(direct_file):
            with open(direct_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                for _, val in data.items():
                    if isinstance(val, dict):
                        return bool(val.get("channel_recording_enabled", True))

        for fpath in glob.glob(os.path.join(DEVICES_DIR, "*.yaml")):
            if os.path.basename(fpath).lower() == "server.yaml":
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                if not isinstance(data, dict):
                    continue
                for k, val in data.items():
                    if k == "Server" or not isinstance(val, dict):
                        continue
                    if str(val.get("device_id", "")).strip().lower() == clean_id or k.strip().lower() == clean_id:
                        return bool(val.get("channel_recording_enabled", True))
    except Exception:
        pass
    return True

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
def check_db_ready():
    """Prueft nur die DB-Konnektivitaet beim Booten. Schemadefinition liegt exklusiv bei db/init/."""
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")

@app.get("/api/v1/control/device-status/{device_id}", dependencies=[Depends(verify_token)])
def get_device_measurement_status(device_id: str):
    with get_db_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT COUNT(*) AS active_count
                FROM analyzer_state
                WHERE device_id = %s
                  AND started_at IS NOT NULL
                  AND export_120_sent = FALSE;
            """, (device_id,))
            row = cur.fetchone()
            active_count = row["active_count"] if row else 0

    return {
        "device_id": device_id,
        "is_measuring": active_count > 0,
        "active_channels": active_count
    }

@app.post("/api/v1/telemetry/ingest", dependencies=[Depends(verify_token)])
def ingest_telemetry(
    payload: IngestPayload,
    x_pending_count: Optional[int] = Header(default=0, alias="X-Pending-Count")
):
    if not payload.records:
        return {"status": "ok", "inserted": 0, "pending_count": x_pending_count}

    allow_channels = is_channel_recording_allowed(payload.device_id)

    unique_map = {}
    for r in payload.records:
        if r.channel >= 100 or allow_channels:
            key = (r.timestamp, payload.device_id, r.channel)
            unique_map[key] = (
                r.timestamp,
                payload.device_id,
                r.channel,
                r.temperature,
                r.job_id,
            )

    query_status = """
        INSERT INTO device_status (device_id, last_seen, pending_count)
        VALUES (%s, NOW(), %s)
        ON CONFLICT (device_id) DO UPDATE 
        SET last_seen = NOW(), pending_count = EXCLUDED.pending_count;
    """

    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query_status, (payload.device_id, x_pending_count))

            if unique_map:
                values = list(unique_map.values())
                query_telemetry = """
                    INSERT INTO telemetry_data (time, device_id, channel, temperature, job_id)
                    VALUES %s
                    ON CONFLICT (time, device_id, channel) DO UPDATE 
                    SET temperature = EXCLUDED.temperature, job_id = EXCLUDED.job_id;
                """
                execute_values(cur, query_telemetry, values)

            conn.commit()

    return {"status": "ok", "inserted": len(unique_map), "pending_count": x_pending_count}

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