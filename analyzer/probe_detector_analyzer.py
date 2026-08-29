"""
@file: probe_detector_analyzer.py
@version: 2.0.0
@date: 2026-08-28
@description: Analyzer für Sonden- und Signalerkennung mit modularen Geräte-YAMLs und integriertem Connection Pooling.
@author: Patrick Stähli
"""

import os
import sys
import time
import glob
import logging
from datetime import datetime, timezone
import yaml
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor

# Sicherstellen, dass das aktuelle Verzeichnis im Pfad liegt
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

CONFIG_PATH_ENV = os.getenv("YAML_CONFIG_PATH", "/app/config")
if os.path.isfile(CONFIG_PATH_ENV):
    DEVICES_DIR = os.path.join(os.path.dirname(CONFIG_PATH_ENV), "devices")
else:
    DEVICES_DIR = os.path.join(CONFIG_PATH_ENV, "devices")

DEFAULT_PROBE_THRESHOLDS = {
    "delta_t_min": 0.80,
    "slope_min": 0.015,
    "rot_peak_min": 0.035
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [PROBE_DETECTOR] %(message)s")
logger = logging.getLogger("ProbeDetectorAnalyzer")

# --- CONNECTION POOLING SETUP ---
db_pool = None

def init_db_pool():
    global db_pool
    if db_pool is None:
        db_pool = SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS
        )
    return db_pool

def get_db_connection():
    pool = init_db_pool()
    try:
        return pool.getconn()
    except Exception:
        global db_pool
        db_pool = None
        return init_db_pool().getconn()

def release_db_connection(conn):
    if db_pool and conn:
        try:
            db_pool.putconn(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


def load_modular_yamls():
    combined = {"Server": {}}
    if not os.path.exists(DEVICES_DIR):
        return combined

    server_file = os.path.join(DEVICES_DIR, "Server.yaml")
    if os.path.exists(server_file):
        try:
            with open(server_file, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f) or {}
                combined["Server"] = content.get("Server", content)
        except Exception as e:
            logger.error(f"[YAML Load Error Server.yaml] {e}")

    for file_path in glob.glob(os.path.join(DEVICES_DIR, "*.yaml")):
        if os.path.basename(file_path).lower() == "server.yaml":
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                dev_data = yaml.safe_load(f) or {}
                if isinstance(dev_data, dict):
                    combined.update(dev_data)
        except Exception as e:
            logger.error(f"[YAML Load Error {os.path.basename(file_path)}] {e}")

    return combined


def get_probe_thresholds(device_id: str, config: dict) -> dict:
    server_cfg = config.get("Server", {})
    th = dict(DEFAULT_PROBE_THRESHOLDS)
    th.update(server_cfg.get("probe_detection", {}))

    for k, val in config.items():
        if k == "Server" or not isinstance(val, dict):
            continue
        if val.get("device_id", "").lower() == str(device_id).lower() or k.lower() == str(device_id).lower():
            th.update(val.get("probe_detection", {}))
            break
    return th


def run_probe_detector():
    logger.info("Probe Detector Analyzer aktiv (Modular YAMLs & Dynamic Thresholds)...")
    
    while True:
        conn = get_db_connection()
        try:
            conn.autocommit = True
            config = load_modular_yamls()
            
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT device_id, MAX(time) as last_time
                    FROM telemetry_data
                    WHERE time >= NOW() - INTERVAL '5 minutes'
                    GROUP BY device_id;
                """)
                active_devices = cur.fetchall()
                
                for dev in active_devices:
                    dev_id = dev["device_id"]
                    th = get_probe_thresholds(dev_id, config)
                    # Hier greift die projektspezifische Schwellwert-Prüfung
                    
        except Exception as err:
            logger.error(f"Fehler im Probe Detector Loop: {err}")
        finally:
            release_db_connection(conn)

        time.sleep(10)


if __name__ == "__main__":
    run_probe_detector()