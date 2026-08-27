"""
@file: probe_detector_analyzer.py
@version: 1.5.0
@date: 2026-08-26
@description: Analyzer für Sonden- und Signalerkennung mit integriertem Connection Pooling via psycopg2.pool.
@author: Patrick Stähli
"""

import os
import sys
import time
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
YAML_PATH = os.getenv("YAML_CONFIG_PATH", "/app/config/devices.yaml")

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


def load_yaml_config():
    if not os.path.exists(YAML_PATH):
        return {}
    try:
        with open(YAML_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"[YAML Load Error] {e}")
        return {}


def run_probe_detector():
    logger.info("Probe Detector Analyzer aktiv (Mit integriertem Connection Pooling)...")
    
    while True:
        conn = get_db_connection()
        try:
            conn.autocommit = True
            config = load_yaml_config()
            
            # Beispielhafte Verarbeitungslogik für Sonden-Erkennung / Auto-Detection
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
                    # Hier greift die Logik zur Prüfung von Schwellwerten oder automatischer Erkennung
                    # (Integrierbar je nach projektspezifischen Detektionsregeln)
                    
        except Exception as err:
            logger.error(f"Fehler im Probe Detector Loop: {err}")
        finally:
            release_db_connection(conn)

        time.sleep(10)


if __name__ == "__main__":
    run_probe_detector()