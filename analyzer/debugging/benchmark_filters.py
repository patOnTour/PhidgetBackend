"""
@file: benchmark_filters.py
@version: 1.1.0
@date: 2026-08-24
@description: Benchmark-Skript fuer DEV-06: Vergleicht Savitzky-Golay mit reinem Polynomfilter 2. Ordnung fuer frei definierbare Zeitfenster.
@author: Patrick Staehli
"""

import os
import sys
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from filters import savitzky_golay

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
LOCAL_TZ = ZoneInfo("Europe/Zurich")


def get_db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )


def simulate_trigger_sg(times, temps, w_size=25, accel_min=0.0000025, slope_min=0.0005):
    """Simuliert den Echtzeit-Stream mit Savitzky-Golay."""
    sec = np.array([(t - times[0]).total_seconds() for t in times])
    for i in range(30, len(temps)):
        sub_times = times[:i]
        sub_temps = temps[:i]
        sub_sec = sec[:i]

        smooth = savitzky_golay(np.array(sub_temps, dtype=float), window_size=w_size, order=2)
        mask_lookback = sub_sec >= (sub_sec[-1] - 120)
        t_sub = sub_sec[mask_lookback]
        temp_sub = smooth[mask_lookback]

        if len(t_sub) >= 5:
            try:
                poly = np.polyfit(t_sub - t_sub[0], temp_sub, 2)
                accel = float(2.0 * poly[0])
                slope = float(2.0 * poly[0] * (t_sub[-1] - t_sub[0]) + poly[1])
                if accel >= accel_min and slope > slope_min:
                    return sub_times[-1], sub_temps[-1], i, accel, slope
            except Exception:
                pass
    return None, None, None, 0.0, 0.0


def simulate_trigger_poly(times, temps, accel_min=0.0000025, slope_min=0.0005):
    """Simuliert den Echtzeit-Stream mit reinem Polynomfilter 2. Ordnung."""
    sec = np.array([(t - times[0]).total_seconds() for t in times])
    for i in range(30, len(temps)):
        sub_times = times[:i]
        sub_temps = temps[:i]
        sub_sec = sec[:i]

        mask_lookback = sub_sec >= (sub_sec[-1] - 120)
        t_sub = sub_sec[mask_lookback]
        temp_raw_sub = np.array(sub_temps)[mask_lookback]

        if len(t_sub) >= 5:
            try:
                poly = np.polyfit(t_sub - t_sub[0], temp_raw_sub, 2)
                accel = float(2.0 * poly[0])
                slope = float(2.0 * poly[0] * (t_sub[-1] - t_sub[0]) + poly[1])
                if accel >= accel_min and slope > slope_min:
                    return sub_times[-1], sub_temps[-1], i, accel, slope
            except Exception:
                pass
    return None, None, None, 0.0, 0.0


def run_benchmark(device_id, channel, start_str, end_str):
    print("=" * 75)
    print(f"BENCHMARK DEV-06: {device_id} - Kanal {channel}")
    print(f"Zeitfenster (Lokal): {start_str} bis {end_str}")
    print("=" * 75)

    dt_start_loc = datetime.strptime(start_str, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
    dt_end_loc = datetime.strptime(end_str, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)

    dt_start_utc = dt_start_loc.astimezone(timezone.utc)
    dt_end_utc = dt_end_loc.astimezone(timezone.utc)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT time_bucket('10s', time) AS bucket,
                       ROUND(AVG(temperature)::numeric, 2) AS temp
                FROM telemetry_data
                WHERE device_id = %s AND channel = %s 
                  AND time >= %s AND time <= %s
                GROUP BY bucket ORDER BY bucket ASC;
            """, (device_id, channel, dt_start_utc, dt_end_utc))
            rows = cur.fetchall()

    if len(rows) < 60:
        print(f"Zu wenige Messpunkte gefunden ({len(rows)} Punkte). Pruefe Zeitraum und Box-ID.")
        return

    times = [r["bucket"] for r in rows]
    temps = [float(r["temp"]) for r in rows]

    print(f"Messreihe geladen: {len(temps)} Datenpunkte (~{len(temps)*10/60:.1f} Minuten)")
    print(f"Temperaturbereich: Min {min(temps):.2f} °C | Max {max(temps):.2f} °C\n")

    # 1. Savitzky-Golay
    t_sg, temp_sg, idx_sg, a_sg, s_sg = simulate_trigger_sg(times, temps)

    # 2. Reiner Polynomfilter 2. Ordnung
    t_poly, temp_poly, idx_poly, a_poly, s_poly = simulate_trigger_poly(times, temps)

    # Auswertung
    print("-" * 75)
    print(f"{'Metrik':<30} | {'Savitzky-Golay':<20} | {'Reiner Poly 2. Ord.':<20}")
    print("-" * 75)

    sg_time_str = t_sg.astimezone(LOCAL_TZ).strftime("%H:%M:%S") if t_sg else "KEIN TRIGGER"
    poly_time_str = t_poly.astimezone(LOCAL_TZ).strftime("%H:%M:%S") if t_poly else "KEIN TRIGGER"
    print(f"{'Trigger-Zeitpunkt (Lokal)':<30} | {sg_time_str:<20} | {poly_time_str:<20}")

    sg_temp_str = f"{temp_sg:.2f} °C" if temp_sg is not None else "-"
    poly_temp_str = f"{temp_poly:.2f} °C" if temp_poly is not None else "-"
    print(f"{'Trigger-Temperatur':<30} | {sg_temp_str:<20} | {poly_temp_str:<20}")

    if t_sg and t_poly:
        diff_sec = (t_poly - t_sg).total_seconds()
        diff_str = f"{diff_sec:+.1f} s"
        print(f"{'Zeitdifferenz (Poly - SG)':<30} | {diff_str:<20} | {diff_str:<20}")

    print("-" * 75)


if __name__ == "__main__":
    dev = sys.argv[1] if len(sys.argv) > 1 else "ccssite01"
    ch = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    start = sys.argv[3] if len(sys.argv) > 3 else "2026-08-23 02:00"
    end = sys.argv[4] if len(sys.argv) > 4 else "2026-08-23 04:30"
    run_benchmark(dev, ch, start, end)
