#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import io
import time
import base64
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import yaml
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from math_engine import ConcreteAnalyzer

LOCAL_TZ = ZoneInfo("Europe/Zurich")

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
NTFY_URL = os.getenv("NTFY_URL", "http://ntfy:80")
ARCHIVE_DIR = os.getenv("ARCHIVE_DIR", "/app/archive")
YAML_PATH = os.getenv("YAML_CONFIG_PATH", "/app/config/devices.yaml")
ADMIN_TOPIC = "Concretum"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [ANALYZER] %(message)s")
logger = logging.getLogger("ServerAnalyzer")

def get_db():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )
    conn.autocommit = True
    return conn

def sanitize_filename(name):
    replacements = {'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'Ä': 'Ae', 'Ö': 'Oe', 'Ü': 'Ue', 'ß': 'ss', ' ': '_'}
    for k, v in replacements.items():
        name = name.replace(k, v)
    return "".join(c for c in name if c.isalnum() or c in ('_', '-', '.'))

def encode_rfc2047(text):
    if not text:
        return ""
    if all(ord(c) < 128 for c in text):
        return text
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"=?utf-8?B?{encoded}?="

def to_local_str(dt):
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ).strftime("%H:%M:%S")

def get_box_metadata(box_id, ch_num):
    topic = ADMIN_TOPIC
    box_name = str(box_id)
    fname = f"Kanal {ch_num + 1}"
    
    if not os.path.exists(YAML_PATH):
        return topic, box_name, fname
    try:
        with open(YAML_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            for k, val in cfg.items():
                if k == "Server" or not isinstance(val, dict):
                    continue
                if val.get("device_id", "").lower() == str(box_id).lower() or k.lower() == str(box_id).lower():
                    topic = val.get("ntfy_channel", ADMIN_TOPIC)
                    box_name = val.get("name", box_id)
                    custom_labels = val.get("channel_labels", {})
                    ch_key = f"temp{ch_num}"
                    if ch_key in custom_labels and custom_labels[ch_key]:
                        fname = custom_labels[ch_key]
                    break
    except Exception as e:
        logger.error(f"Fehler beim Laden von devices.yaml: {e}")
    return topic, box_name, fname

def send_ntfy(topic, message, title=None, priority=3, tags=None, attach_buf=None, filename=None):
    url = f"{NTFY_URL.rstrip('/')}/{topic}"
    headers = {"Priority": str(priority)}
    
    if title:
        headers["Title"] = encode_rfc2047(title)
    if tags:
        headers["Tags"] = ",".join(tags)
    if filename:
        headers["Filename"] = sanitize_filename(filename)
    if message and attach_buf:
        headers["Message"] = encode_rfc2047(message)

    try:
        if attach_buf:
            attach_buf.seek(0)
            res = requests.post(url, data=attach_buf, headers=headers, timeout=10)
        else:
            headers["Content-Type"] = "text/plain; charset=utf-8"
            res = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
        if res.status_code == 200:
            logger.info(f"[NTFY OK] Topic: {topic} | Title: {title}")
        else:
            logger.error(f"[NTFY ERR] Status {res.status_code}: {res.text}")
    except Exception as e:
        logger.error(f"[NTFY EXCEPTION] {e}")

def record_alert(conn, device_id, channel, event_type, event_time, title, message):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO alerts_history (device_id, channel, event_type, event_time, title, message)
                VALUES (%s, %s, %s, %s, %s, %s);
            """, (device_id, channel, event_type, event_time, title, message))
    except Exception as e:
        logger.error(f"[DB ALERT ERR] {e}")

def save_to_archive(box_id, base_key, img_buf, csv_bytes=None):
    try:
        target_dir = os.path.join(ARCHIVE_DIR, box_id)
        os.makedirs(target_dir, exist_ok=True)
        if img_buf:
            img_buf.seek(0)
            png_file = os.path.join(target_dir, f"Graph_{base_key}.png")
            with open(png_file, "wb") as f:
                f.write(img_buf.read())
            img_buf.seek(0)
        if csv_bytes:
            csv_file = os.path.join(target_dir, f"Daten_{base_key}.csv")
            with open(csv_file, "wb") as f:
                f.write(csv_bytes)
        logger.info(f"[ARCHIVE SAVED] {box_id}: Graph_{base_key}.png / CSV abgelegt.")
    except Exception as e:
        logger.error(f"[ARCHIVE ERR] {e}")

def process_active_streams():
    analyzer = ConcreteAnalyzer()
    while True:
        try:
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT s.device_id, s.channel, s.job_id, s.started_at,
                               s.turnaround_sent, s.trigger_sent, s.export_30_sent, s.export_120_sent,
                               s.t_ab_time, s.force_export
                        FROM analyzer_state s
                        WHERE s.started_at IS NOT NULL
                          AND (s.export_120_sent = FALSE OR s.force_export = TRUE);
                    """)
                    active_runs = cur.fetchall()

                    for state in active_runs:
                        dev = state['device_id']
                        ch = state['channel']
                        started_at = state['started_at']
                        topic, box_name, fname = get_box_metadata(dev, ch)
                        display_name = f"{box_name} - {fname}"

                        cur.execute("""
                            SELECT time_bucket('10s', time) AS bucket,
                                   ROUND(AVG(temperature)::numeric, 2) AS temp
                            FROM telemetry_data
                            WHERE device_id = %s AND channel = %s AND time >= %s
                            GROUP BY bucket ORDER BY bucket ASC;
                        """, (dev, ch, started_at))
                        data = cur.fetchall()
                        if len(data) < 3:
                            continue

                        times = [r['bucket'] for r in data]
                        temps = [float(r['temp']) for r in data]

                        cur.execute("""
                            SELECT time_bucket('10s', time) AS bucket,
                                   ROUND(AVG(temperature)::numeric, 2) AS ambient
                            FROM telemetry_data
                            WHERE device_id = %s AND channel = 100 AND time >= %s
                            GROUP BY bucket ORDER BY bucket ASC;
                        """, (dev, started_at))
                        amb_data = {r['bucket']: float(r['ambient']) for r in cur.fetchall()}
                        ambs = [amb_data.get(t, 20.0) for t in times]

                        # 1. Sofort-Export (EXP)
                        if state.get('force_export'):
                            logger.info(f"[EXP] Manueller Export fuer {display_name}...")
                            buf, _, _ = analyzer.generate_plot(times, temps, ambs, display_name)
                            csv_bytes = analyzer.generate_csv(times, temps, ambs, display_name)
                            ts_key = datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M")
                            file_tag = sanitize_filename(f"temp{ch}_{fname}_{ts_key}")
                            save_to_archive(dev, file_tag, buf, csv_bytes)

                            title = f"Export: {display_name}"
                            msg = f"Manueller Export für {display_name} (Aktuell: {temps[-1]:.2f} °C)."
                            send_ntfy(topic, msg, title=title, priority=3, tags=["bar_chart"], attach_buf=buf, filename=f"manual_{box_name}_{fname}.png")
                            record_alert(conn, dev, ch, "MANUAL_EXPORT", datetime.now(timezone.utc), title, msg)

                            cur.execute("UPDATE analyzer_state SET force_export = FALSE WHERE device_id = %s AND channel = %s;", (dev, ch))

                        now_utc = datetime.now(started_at.tzinfo or timezone.utc)
                        if (now_utc - started_at).total_seconds() < 180:
                            continue

                        # 2. Turnaround
                        if not state['turnaround_sent']:
                            is_turn, t_min, c_delta, r_delta = analyzer.check_turnaround(temps)
                            if is_turn:
                                now_turn = datetime.now(timezone.utc)
                                cur.execute("""
                                    UPDATE analyzer_state 
                                    SET turnaround_sent = TRUE, t_min_temp = %s, t_min_time = %s
                                    WHERE device_id = %s AND channel = %s;
                                """, (t_min, now_turn, dev, ch))

                                t_str = to_local_str(now_turn)
                                title = f"Info: Wendepunkt {display_name}"
                                msg = f"Tiefstwert um {t_str} bei {t_min:.2f} °C durchschritten (Abkühlung: {c_delta:.2f} °C, Anstieg: +{r_delta:.2f} °C)."
                                send_ntfy(topic, msg, title=title, priority=3, tags=["chart_with_downwards_trend", "arrow_up"])
                                record_alert(conn, dev, ch, "TURNAROUND", now_turn, title, msg)

                        # 3. Abbindebeginn Trigger
                        if not state['trigger_sent']:
                            trig_type, accel, slope = analyzer.evaluate_triggers(times, temps)
                            if trig_type:
                                buf, t_ab_dt, temp_ab = analyzer.generate_plot(times, temps, ambs, display_name)
                                t_event = t_ab_dt if t_ab_dt else datetime.now(timezone.utc)
                                cur.execute("""
                                    UPDATE analyzer_state 
                                    SET trigger_sent = TRUE, t_ab_time = %s, t_ab_temp = %s
                                    WHERE device_id = %s AND channel = %s;
                                """, (t_event, temp_ab, dev, ch))

                                t_str = to_local_str(t_event)
                                title = f"🚨 Beton-Alarm: {display_name} bindet ab!"
                                msg = f"Abbindebeginn erkannt um {t_str} (Aktuell: {temps[-1]:.2f} °C)."
                                send_ntfy(topic, msg, title=title, priority=4, tags=["rotating_light", "warning"], attach_buf=buf, filename=f"setting_{box_name}_{fname}.png")
                                record_alert(conn, dev, ch, "TRIGGER_SETTING", t_event, title, msg)

                        # 4. Automatischer Export (+30 Min & +120 Min)
                        if state['trigger_sent'] and state['t_ab_time']:
                            t_ref = state['t_ab_time']
                            elapsed = (datetime.now(t_ref.tzinfo or timezone.utc) - t_ref).total_seconds()

                            if not state['export_30_sent'] and elapsed >= 1800:
                                buf, _, _ = analyzer.generate_plot(times, temps, ambs, display_name)
                                csv_bytes = analyzer.generate_csv(times, temps, ambs, display_name)
                                ts_key = datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M")
                                file_tag = sanitize_filename(f"temp{ch}_{fname}_{ts_key}_30m")
                                save_to_archive(dev, file_tag, buf, csv_bytes)

                                title = f"Report (30min): {display_name}"
                                msg = "30-Minuten-Fortschrittsbericht nach Abbindebeginn."
                                send_ntfy(topic, msg, title=title, priority=3, tags=["bar_chart"], attach_buf=buf, filename=f"report_30m_{box_name}_{fname}.png")
                                record_alert(conn, dev, ch, "REPORT_30M", datetime.now(timezone.utc), title, msg)

                                cur.execute("UPDATE analyzer_state SET export_30_sent = TRUE WHERE device_id = %s AND channel = %s;", (dev, ch))

                            if not state['export_120_sent'] and elapsed >= 7200:
                                buf, _, _ = analyzer.generate_plot(times, temps, ambs, display_name)
                                csv_bytes = analyzer.generate_csv(times, temps, ambs, display_name)
                                ts_key = datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M")
                                file_tag = sanitize_filename(f"temp{ch}_{fname}_{ts_key}_120m")
                                save_to_archive(dev, file_tag, buf, csv_bytes)

                                title = f"Abschlussbericht (120min): {display_name}"
                                msg = "120-Minuten-Abschlussbericht."
                                send_ntfy(topic, msg, title=title, priority=3, tags=["chart"], attach_buf=buf, filename=f"report_120m_{box_name}_{fname}.png")
                                send_ntfy(topic, "Messdaten CSV-Export", title=f"CSV: {display_name}", priority=2, tags=["file_folder"], attach_buf=io.BytesIO(csv_bytes), filename=f"data_{box_name}_{fname}.csv")
                                record_alert(conn, dev, ch, "REPORT_120M", datetime.now(timezone.utc), title, msg)

                                cur.execute("UPDATE analyzer_state SET export_120_sent = TRUE WHERE device_id = %s AND channel = %s;", (dev, ch))

        except Exception as err:
            logger.error(f"Fehler im Analyzer-Loop: {err}")

        time.sleep(5)

if __name__ == "__main__":
    logger.info("Server Analyzer aktiv (Dynamisches Kanalmapping)...")
    process_active_streams()
