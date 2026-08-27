"""
@file: export_night_csv.py
@version: 1.1.0
@date: 2026-08-26
@description: Exportiert die Nachtschicht-Daten (26.08.2026, 00:00 - 05:00 Uhr) fuer ccssite02 als CSV ins Verzeichnis /app.
@author: Patrick Staehli
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from export_generator import ExportGenerator

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

OUTPUT_DIR = "/app"
DEVICE_ID = "ccssite02"

def export_night_data():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT time, channel, temperature
            FROM telemetry_raw_benchmark
            WHERE device_id = %s
              AND time >= '2026-08-26 00:00:00+02'
              AND time < '2026-08-26 05:00:00+02'
            ORDER BY time ASC;
        """, (DEVICE_ID,))
        rows = cur.fetchall()
    conn.close()

    if not rows:
        print(f"Keine Daten fuer {DEVICE_ID} im angegebenen Zeitraum gefunden.")
        return

    # Nach Kanaelen trennen
    channel_data = {}
    for r in rows:
        ch = r["channel"]
        channel_data.setdefault(ch, {"times": [], "temps": []})
        channel_data[ch]["times"].append(r["time"])
        channel_data[ch]["temps"].append(float(r["temperature"]))

    # Wir nutzen Kanal 0 als Referenz
    primary_channel = 0 if 0 in channel_data else list(channel_data.keys())[0]
    times = channel_data[primary_channel]["times"]
    temps = channel_data[primary_channel]["temps"]

    # Umgebung (Kanal 100) auf exakt dieselbe Länge bringen (Fallback auf 20.0 falls nicht vorhanden)
    if 100 in channel_data and len(channel_data[100]["temps"]) == len(times):
        ambs = channel_data[100]["temps"]
    else:
        ambs = [20.0] * len(times)

    # ExportGenerator aufrufen
    generator = ExportGenerator()
    csv_bytes = generator.generate_csv(
        times=times,
        temps=temps,
        ambs=ambs,
        display_label=f"{DEVICE_ID}_Nacht",
        tz_str="Europe/Zurich"
    )

    out_filename = f"export_night_{DEVICE_ID}_00_05.csv"
    out_path = os.path.join(OUTPUT_DIR, out_filename)

    with open(out_path, "wb") as f:
        f.write(csv_bytes)
    
    print(f"CSV-Export erfolgreich gespeichert unter: {out_path}")

if __name__ == "__main__":
    export_night_data()