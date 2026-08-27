"""
@file: history.py
@version: 1.0.0
@date: 2026-08-27
@description: History Blueprint fuer Concretum Dashboard.
              Ermoeglicht Abfrage historischer Messreihen, Peak-Events und dynamischen CSV-Export.
@author: Patrick Staehli
"""

import io
import csv
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone
import zoneinfo
from flask import Blueprint, render_template, request, jsonify, Response, current_app

history_bp = Blueprint("history", __name__, url_prefix="/history")


def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=current_app.config.get("DB_HOST", "timescaledb"),
            port=int(current_app.config.get("DB_PORT", 5432)),
            dbname=current_app.config.get("DB_NAME", "telemetry_db"),
            user=current_app.config.get("DB_USER", "postgres"),
            password=current_app.config.get("DB_PASS", ""),
            connect_timeout=3
        )
        return conn
    except Exception as e:
        print(f"[History DB Error] {e}", flush=True)
        return None


@history_bp.route("/")
def history_page():
    from app import get_parsed_config, ALL_TIMEZONES
    cfg = get_parsed_config()
    return render_template(
        "history.html",
        boxes=cfg["boxes"],
        server=cfg["server"],
        all_timezones=ALL_TIMEZONES
    )


@history_bp.route("/api/data", methods=["POST"])
def get_history_data():
    req_data = request.get_json() or {}
    device_ids = req_data.get("device_ids", [])
    channels = req_data.get("channels", [])
    start_time_iso = req_data.get("start_time")
    end_time_iso = req_data.get("end_time")

    if not device_ids or not channels or not start_time_iso or not end_time_iso:
        return jsonify({"series": [], "events": []})

    conn = get_db_connection()
    if not conn:
        return jsonify({"series": [], "events": []}), 500

    from app import load_yaml_raw
    raw_yaml = load_yaml_raw()
    server_tz_name = raw_yaml.get("Server", {}).get("timezone", "Europe/Zurich")
    try:
        target_tz = zoneinfo.ZoneInfo(server_tz_name)
    except Exception:
        target_tz = zoneinfo.ZoneInfo("Europe/Zurich")

    series_map = {}
    events = []

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Telemetrie-Rohdaten abfragen (kein Zwangs-Aggregate noetig bei 20s Takt)
            cur.execute("""
                SELECT time, device_id, channel, ROUND(temperature::numeric, 2) AS temp
                FROM telemetry_data
                WHERE device_id = ANY(%s)
                  AND channel = ANY(%s)
                  AND time >= %s AND time <= %s
                ORDER BY time ASC;
            """, (device_ids, channels, start_time_iso, end_time_iso))
            rows = cur.fetchall()

            for r in rows:
                t_dt = r["time"].replace(tzinfo=timezone.utc) if r["time"].tzinfo is None else r["time"]
                t_local = t_dt.astimezone(target_tz).strftime("%Y-%m-%d %H:%M:%S")
                dev_ch_key = f"{r['device_id']}_ch{r['channel']}"

                if t_local not in series_map:
                    series_map[t_local] = {"time": t_local}
                series_map[t_local][dev_ch_key] = float(r["temp"])

            # 2. Peaks und Events abfragen
            cur.execute("""
                SELECT event_time, device_id, channel, event_type, title, message
                FROM alerts_history
                WHERE device_id = ANY(%s)
                  AND event_time >= %s AND event_time <= %s
                ORDER BY event_time ASC;
            """, (device_ids, start_time_iso, end_time_iso))
            for ev in cur.fetchall():
                ev_dt = ev["event_time"].replace(tzinfo=timezone.utc) if ev["event_time"].tzinfo is None else ev["event_time"]
                ev_local = ev_dt.astimezone(target_tz).strftime("%Y-%m-%d %H:%M:%S")
                events.append({
                    "time": ev_local,
                    "device_id": ev["device_id"],
                    "channel": ev["channel"],
                    "type": ev["event_type"],
                    "title": ev["title"],
                    "message": ev["message"]
                })

        return jsonify({
            "series": list(series_map.values()),
            "events": events
        })
    except Exception as e:
        print(f"[History Query Error] {e}", flush=True)
        return jsonify({"series": [], "events": [], "error": str(e)}), 500
    finally:
        conn.close()


@history_bp.route("/api/export-csv", methods=["POST"])
def export_history_csv():
    req_data = request.get_json() or {}
    device_ids = req_data.get("device_ids", [])
    channels = req_data.get("channels", [])
    start_time_iso = req_data.get("start_time")
    end_time_iso = req_data.get("end_time")

    conn = get_db_connection()
    if not conn:
        return Response("Keine Datenbankverbindung", status=500)

    from app import load_yaml_raw
    raw_yaml = load_yaml_raw()
    server_tz_name = raw_yaml.get("Server", {}).get("timezone", "Europe/Zurich")
    try:
        target_tz = zoneinfo.ZoneInfo(server_tz_name)
    except Exception:
        target_tz = zoneinfo.ZoneInfo("Europe/Zurich")

    # Mapping fuer lesbare Spalten-Header erstellen
    headers = ["Zeitpunkt (Lokal)"]
    col_keys = []
    for d_id in device_ids:
        box_data = next((v for k, v in raw_yaml.items() if k != "Server" and isinstance(v, dict) and (v.get("device_id", "").lower() == d_id.lower() or k.lower() == d_id.lower())), {})
        box_name = box_data.get("name", d_id)
        custom_labels = box_data.get("channel_labels", {})
        for ch in channels:
            ch_num = int(ch)
            if ch_num == 100:
                ch_label = "Umgebung"
            elif ch_num == 101:
                ch_label = "Luftfeuchte"
            else:
                ch_label = custom_labels.get(f"temp{ch_num}", f"Kanal {ch_num + 1}")
            headers.append(f"{box_name} - {ch_label} (°C)")
            col_keys.append(f"{d_id}_ch{ch_num}")

    time_rows = {}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT time, device_id, channel, ROUND(temperature::numeric, 2) AS temp
                FROM telemetry_data
                WHERE device_id = ANY(%s)
                  AND channel = ANY(%s)
                  AND time >= %s AND time <= %s
                ORDER BY time ASC;
            """, (device_ids, channels, start_time_iso, end_time_iso))
            for r in cur.fetchall():
                t_dt = r["time"].replace(tzinfo=timezone.utc) if r["time"].tzinfo is None else r["time"]
                t_str = t_dt.astimezone(target_tz).strftime("%d.%m.%Y %H:%M:%S")
                if t_str not in time_rows:
                    time_rows[t_str] = {}
                time_rows[t_str][f"{r['device_id']}_ch{r['channel']}"] = r["temp"]

        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(headers)

        for t_str, vals in time_rows.items():
            row = [t_str]
            for col in col_keys:
                row.append(str(vals.get(col, "")).replace(".", ","))
            writer.writerow(row)

        output.seek(0)
        filename = f"Concretum_Export_{datetime.now(target_tz).strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            output.getvalue().encode("utf-8-sig"),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        print(f"[CSV Export Error] {e}", flush=True)
        return Response(f"Export-Fehler: {e}", status=500)
    finally:
        conn.close()