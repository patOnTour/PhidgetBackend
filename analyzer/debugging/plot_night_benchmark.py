"""
@file: plot_night_benchmark.py
@version: 1.2.0
@date: 2026-08-25
@description: Plottet alle aufgezeichneten Kanaele fuer ccssite01 und ccssite02 aus telemetry_raw_benchmark als PNG ins Container-Verzeichnis /app (Host: ./analyzer).
@author: Patrick Staehli
"""

import os
from zoneinfo import ZoneInfo
import psycopg2
from psycopg2.extras import RealDictCursor
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
LOCAL_TZ = ZoneInfo("Europe/Zurich")

OUTPUT_DIR = "/app"

def get_db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )

def plot_device_curves(device_id, out_filename):
    conn = get_db()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT time, channel, temperature
            FROM telemetry_raw_benchmark
            WHERE device_id = %s
              AND time >= '2026-08-26 00:00:00+02'
              AND time < '2026-08-26 05:00:00+02'
            ORDER BY time ASC;
        """, (device_id,))
        rows = cur.fetchall()
    conn.close()

    if not rows:
        print(f"Keine Daten fuer {device_id} in telemetry_raw_benchmark gefunden.")
        return

    channel_data = {}
    for r in rows:
        ch = r["channel"]
        dt_local = r["time"].astimezone(LOCAL_TZ)
        channel_data.setdefault(ch, {"times": [], "temps": []})
        channel_data[ch]["times"].append(dt_local)
        channel_data[ch]["temps"].append(float(r["temperature"]))

    fig, ax = plt.subplots(figsize=(13, 6))

    colors_palette = {
        0: "#2563eb",   # Blau
        1: "#16a34a",   # Gruen
        2: "#ea580c",   # Orange
        3: "#9333ea",   # Violett
        100: "#dc2626", # Rot (Umgebung)
        101: "#0891b2"  # Cyan (Feuchte)
    }

    for ch, data in sorted(channel_data.items()):
        if ch == 101:
            continue  # Feuchte ausblenden
        
        lbl = f"Kanal {ch}" if ch < 100 else "Umgebung (Ch 100)"
        c = colors_palette.get(ch, "#64748b")
        ls = "--" if ch == 100 else "-"
        lw = 2.0 if ch < 100 else 1.2
        alpha = 0.9 if ch < 100 else 0.6
        
        ax.plot(data["times"], data["temps"], label=lbl, color=c, linestyle=ls, linewidth=lw, alpha=alpha)

    ax.set_title(f"1-Hz Temperaturverlauf: {device_id} (Nachtschicht)", fontsize=13, fontweight="bold")
    ax.set_xlabel("Uhrzeit (MESZ / Lokalzeit)", fontsize=11)
    ax.set_ylabel("Temperatur (°C)", fontsize=11)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=LOCAL_TZ))
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper left", framealpha=0.9)
    plt.tight_layout()

    out_path = os.path.join(OUTPUT_DIR, out_filename)
    plt.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Plot erfolgreich gespeichert: {out_path}")

if __name__ == "__main__":
    plot_device_curves("ccssite02", "plot_night_ccssite02.png")
    plot_device_curves("ccssite01", "plot_night_ccssite01.png")