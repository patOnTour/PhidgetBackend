"""
@file: raw_recorder.py
@version: 2.1.0
@date: 2026-08-26
@description: Zeichnet rohe 1-Hz-Telemetriedaten auf und generiert um 05:00 Uhr automatisch pro Kanal einen Einzelplot (PNG) sowie einen CSV-Export für die Nachtschicht (inkl. angepasstem Debugging-Pfad) mit Connection Pooling.
@author: Patrick Stähli
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor

# Pfad zum debugging-Ordner hinzufügen, damit export_generator gefunden wird
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUGGING_DIR = os.path.join(CURRENT_DIR, "debugging")
if DEBUGGING_DIR not in sys.path:
    sys.path.append(DEBUGGING_DIR)

from export_generator import ExportGenerator

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

LOCAL_TZ = ZoneInfo("Europe/Zurich")
TARGET_BOXES = ["ccssite01", "ccssite02"]
EXPORT_DIR = os.path.join(CURRENT_DIR, "night_exports")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [RECORDER] %(message)s")
logger = logging.getLogger("RawRecorder")

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

def get_db():
    pool = init_db_pool()
    try:
        conn = pool.getconn()
        conn.autocommit = True
        return conn
    except Exception:
        global db_pool
        db_pool = None
        conn = init_db_pool().getconn()
        conn.autocommit = True
        return conn

def release_db(conn):
    if db_pool and conn:
        try:
            db_pool.putconn(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


def init_db(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_raw_benchmark (
                time TIMESTAMPTZ NOT NULL,
                device_id VARCHAR(50) NOT NULL,
                channel INT NOT NULL,
                temperature DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (time, device_id, channel)
            );
        """)
    logger.info("Tabelle telemetry_raw_benchmark bereitgestellt.")


def generate_night_exports(date_str):
    """Generiert um 05:00 Uhr die Plots und CSVs pro Kanal für die vergangene Nacht (00:00 - 05:00)."""
    logger.info(f"Starte automatischen Nacht-Export für Datum: {date_str}...")
    os.makedirs(EXPORT_DIR, exist_ok=True)
    exporter = ExportGenerator()

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT time, device_id, channel, temperature
                FROM telemetry_raw_benchmark
                WHERE time >= %s AND time < %s
                ORDER BY device_id, channel, time ASC;
            """, (f"{date_str} 00:00:00+02", f"{date_str} 05:00:00+02"))
            rows = cur.fetchall()
    except Exception as ex:
        logger.error(f"Fehler beim Abrufen der Nacht-Daten: {ex}")
        return
    finally:
        release_db(conn)

    if not rows:
        logger.warning(f"Keine Rohdaten für den Export am {date_str} gefunden.")
        return

    data_tree = {}
    for r in rows:
        dev = r["device_id"]
        ch = r["channel"]
        data_tree.setdefault(dev, {}).setdefault(ch, {"times": [], "temps": []})
        data_tree[dev][ch]["times"].append(r["time"])
        data_tree[dev][ch]["temps"].append(float(r["temperature"]))

    for dev_id, channels in data_tree.items():
        amb_data = channels.get(100, {}).get("temps", None)

        for ch, series in channels.items():
            if ch >= 100:
                continue

            times = series["times"]
            temps = series["temps"]
            ambs = amb_data if amb_data and len(amb_data) == len(times) else [20.0] * len(times)

            display_label = f"{dev_id}_Ch{ch}"
            file_prefix = f"Nacht_{date_str}_{dev_id}_Kanal_{ch}"

            # 1. PNG Plot erzeugen
            buf, _, _ = exporter.generate_plot(times, temps, ambs, display_label, tz_str="Europe/Zurich")
            png_path = os.path.join(EXPORT_DIR, f"{file_prefix}.png")
            with open(png_path, "wb") as f:
                f.write(buf.read())

            # 2. CSV Export erzeugen
            csv_bytes = exporter.generate_csv(times, temps, ambs, display_label, tz_str="Europe/Zurich")
            csv_path = os.path.join(EXPORT_DIR, f"{file_prefix}.csv")
            with open(csv_path, "wb") as f:
                f.write(csv_bytes)

            logger.info(f"Exportiert: {file_prefix}.png/.csv")

    logger.info("Nacht-Export erfolgreich abgeschlossen.")


def record_loop():
    conn = get_db()
    try:
        init_db(conn)
    finally:
        release_db(conn)

    last_recorded_time = {}
    last_export_date = None
    logger.info("Raw-Recorder gestartet. Überwacht Zeitfenster & steuert automatischen Export um 05:00 Uhr...")

    while True:
        try:
            now_local = datetime.now(LOCAL_TZ)
            today_str = now_local.strftime("%Y-%m-%d")

            if now_local.hour == 5 and last_export_date != today_str:
                generate_night_exports(today_str)
                last_export_date = today_str

            if 0 <= now_local.hour < 5:
                conn = get_db()
                try:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("""
                            SELECT time, device_id, channel, temperature 
                            FROM telemetry_data
                            WHERE device_id = ANY(%s)
                              AND time >= NOW() - INTERVAL '15 seconds'
                            ORDER BY time ASC;
                        """, (TARGET_BOXES,))
                        rows = cur.fetchall()

                        new_inserts = []
                        for r in rows:
                            key = (r["device_id"], r["channel"])
                            last_t = last_recorded_time.get(key)
                            if last_t is None or r["time"] > last_t:
                                new_inserts.append((r["time"], r["device_id"], r["channel"], float(r["temperature"])))
                                last_recorded_time[key] = r["time"]

                        if new_inserts:
                            cur.executemany("""
                                INSERT INTO telemetry_raw_benchmark (time, device_id, channel, temperature)
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT DO NOTHING;
                            """, new_inserts)
                finally:
                    release_db(conn)
            else:
                if now_local.minute % 15 == 0 and now_local.second < 5:
                    logger.info(f"Ausserhalb des Aufzeichnungsfensters (Aktuell: {now_local.strftime('%H:%M:%S')} MESZ).")

        except Exception as e:
            logger.error(f"Fehler im Recorder-Loop: {e}")

        time.sleep(2)


if __name__ == "__main__":
    record_loop()