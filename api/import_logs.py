import os
import re
import glob
from datetime import datetime
import psycopg2
from psycopg2.extras import execute_values

DB_HOST = "timescale_db"
DB_PORT = 5432
DB_NAME = "telemetry_db"
DB_USER = "postgres"
DB_PASS = "DeinSicheresPasswort123!"

LOG_DIR = "/app/import_logs"
BATCH_SIZE = 5000

FILENAME_REGEX = re.compile(r"^([a-zA-Z0-9_-]+)_telemetry\.log")
LINE_REGEX = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3})\s+\[INFO\].*?\[Messung\]\s+(.*)$"
)
METRIC_PAIR_REGEX = re.compile(r"([^|:]+?)\s*\(([^)]+)\):\s*([\d\.-]+)")

def parse_channel_info(raw_key: str):
    k = raw_key.strip().lower()
    if k == "ambient":
        return "METRIC_AMBIENT", 100
    if k == "humidity":
        return "METRIC_HUMIDITY", 101
    if k.startswith("temp"):
        ch_num = int(k.replace("temp", ""))
        return f"CHANNEL_TEMP{ch_num}", ch_num
    
    digits = re.findall(r"\d+", k)
    ch_num = int(digits[0]) if digits else 99
    return f"CHANNEL_{k.upper()}", ch_num

def parse_log_file(filepath):
    filename = os.path.basename(filepath)
    match_file = FILENAME_REGEX.match(filename)
    if not match_file:
        return [], {}

    device_id = match_file.group(1)
    records = []
    metadata_map = {}

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            match_line = LINE_REGEX.match(line.strip())
            if not match_line:
                continue

            raw_time, payload = match_line.groups()
            dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S,%f")

            entries = METRIC_PAIR_REGEX.findall(payload)
            for friendly_raw, tech_key_raw, val_raw in entries:
                job_id, channel_int = parse_channel_info(tech_key_raw)
                friendly_name = friendly_raw.strip()

                try:
                    val = float(val_raw)
                    records.append((dt, device_id, channel_int, val, job_id))
                    metadata_map[(device_id, job_id)] = friendly_name
                except ValueError:
                    continue

    return records, metadata_map

def main():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )
    cursor = conn.cursor()

    files = sorted(glob.glob(os.path.join(LOG_DIR, "*_telemetry.log*")))
    print(f"{len(files)} Log-Dateien gefunden.")

    insert_telemetry = """
        INSERT INTO telemetry_data (time, device_id, channel, temperature, job_id)
        VALUES %s
        ON CONFLICT (time, device_id, channel) DO NOTHING;
    """

    upsert_metadata = """
        INSERT INTO device_channel_metadata (device_id, job_id, friendly_name, last_updated)
        VALUES %s
        ON CONFLICT (device_id, job_id) 
        DO UPDATE SET 
            friendly_name = EXCLUDED.friendly_name,
            last_updated = EXCLUDED.last_updated;
    """

    for filepath in files:
        fname = os.path.basename(filepath)
        print(f"Verarbeite: {fname}...")
        records, meta = parse_log_file(filepath)
        if not records:
            continue

        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            execute_values(cursor, insert_telemetry, batch, page_size=BATCH_SIZE)
            conn.commit()

        if meta:
            meta_batch = [(dev, job, f_name, datetime.utcnow()) for (dev, job), f_name in meta.items()]
            execute_values(cursor, upsert_metadata, meta_batch)
            conn.commit()

        print(f"-> {len(records)} Messwerte verarbeitet.")

    cursor.close()
    conn.close()
    print("Import erfolgreich abgeschlossen.")

if __name__ == "__main__":
    main()
