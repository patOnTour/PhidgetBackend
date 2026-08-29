"""
@file: server_analyzer.py
@version: 2.3.1
@date: 2026-08-28
@description: Backend-Dienst fuer Telemetrie-Ueberwachung, Offline-Erkennung, Abbinde-Auswertung
              und automatische Reports mit stabilem Connection-Pooling und YAML-Parametern.
@author: Patrick Staehli
"""

import os
import sys
import io
import time
import glob
import base64
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import yaml
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
import requests

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)

from setting_detector import SettingDetector
from export_generator import ExportGenerator

DEFAULT_TZ_NAME = "Europe/Zurich"

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
NTFY_URL = os.getenv("NTFY_URL", "http://ntfy:80")
ARCHIVE_DIR = os.getenv("ARCHIVE_DIR", "/app/archive")

CONFIG_PATH_ENV = os.getenv("YAML_CONFIG_PATH", "/app/config")
if os.path.isfile(CONFIG_PATH_ENV):
    DEVICES_DIR = os.path.join(os.path.dirname(CONFIG_PATH_ENV), "devices")
else:
    DEVICES_DIR = os.path.join(CONFIG_PATH_ENV, "devices")

ADMIN_TOPIC = "Concretum"

DEFAULT_TURNAROUND_THRESHOLDS = {
    "sg_window": 31,
    "cooling_slope_min": -0.0003,
    "reheating_slope_min": 0.0003,
    "min_cooling_delta": 0.20
}

DEFAULT_SETTING_THRESHOLDS = {
    "sg_window": 21,
    "poly_order": 2,
    "lookback_sec": 120,
    "min_samples": 15,
    "accel_min": 0.000010,     # 10 µ°C/s²
    "slope_min": 0.0002,       # 0.0002 °C/s
    "fallback_samples": 5,
    "fallback_step_min": 0.015
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [ANALYZER] %(message)s")
logger = logging.getLogger("ServerAnalyzer")

offline_alert_state = {}

# --- CONNECTION POOLING ---
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
    global db_pool
    try:
        pool = init_db_pool()
        conn = pool.getconn()
        # Validitäts-Check
        if conn.closed != 0:
            pool.putconn(conn, close=True)
            conn = pool.getconn()
        return conn
    except Exception:
        db_pool = None
        pool = init_db_pool()
        return pool.getconn()

def release_db_connection(conn):
    global db_pool
    if db_pool and conn:
        try:
            if conn.closed == 0:
                db_pool.putconn(conn)
            else:
                db_pool.putconn(conn, close=True)
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


def check_and_disable_box_logging(conn, box_id):
    """Schaltet channel_recording_enabled auf False, wenn alle Kanaele fertig sind."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM analyzer_state 
                WHERE device_id = %s AND started_at IS NOT NULL AND export_120_sent = FALSE;
            """, (box_id,))
            active_count = cur.fetchone()[0]

        if active_count == 0:
            for file_path in glob.glob(os.path.join(DEVICES_DIR, "*.yaml")):
                if os.path.basename(file_path).lower() == "server.yaml":
                    continue
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        cfg = yaml.safe_load(f) or {}
                    target_key = next((k for k, v in cfg.items() if isinstance(v, dict) and (v.get("device_id", "").lower() == str(box_id).lower() or k.lower() == str(box_id).lower())), None)
                    if target_key and cfg[target_key].get("channel_recording_enabled", True):
                        cfg[target_key]["channel_recording_enabled"] = False
                        with open(file_path, "w", encoding="utf-8") as f:
                            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                        logger.info(f"[AUTO-HOUSEKEEPING] Alle Kanaele fuer {box_id} abgeschlossen. Logging deaktiviert.")
                        break
                except Exception:
                    pass
    except Exception as ex:
        logger.error(f"[AUTO-HOUSEKEEPING ERR] {ex}")


def sanitize_filename(name):
    replacements = {"ae": "ae", "oe": "oe", "ue": "ue", "Ae": "Ae", "Oe": "Oe", "Ue": "Ue", "ss": "ss", " ": "_"}
    for k, v in replacements.items():
        name = name.replace(k, v)
    return "".join(c for c in name if c.isalnum() or c in ("_", "-", "."))


def encode_rfc2047(text):
    if not text:
        return ""
    if all(ord(c) < 128 for c in text):
        return text
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"=?utf-8?B?{encoded}?="


def to_local_str(dt, tz_name=DEFAULT_TZ_NAME):
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TZ_NAME)
    return dt.astimezone(tz).strftime("%H:%M:%S")


def get_box_metadata(box_id, ch_num=0):
    topic = ADMIN_TOPIC
    box_name = str(box_id)
    fname = f"Kanal {ch_num + 1}"
    tz_name = DEFAULT_TZ_NAME
    turnaround_enabled = True
    ntfy_enabled = True
    turnaround_th = dict(DEFAULT_TURNAROUND_THRESHOLDS)
    setting_th = dict(DEFAULT_SETTING_THRESHOLDS)
    auto_reset_30m = True

    cfg = load_modular_yamls()
    server_cfg = cfg.get("Server", {})
    tz_name = server_cfg.get("timezone", DEFAULT_TZ_NAME)

    server_td = server_cfg.get("turnaround_detection", {})
    turnaround_th.update(server_td)

    server_sd = server_cfg.get("setting_detection", {})
    setting_th.update(server_sd)

    for k, val in cfg.items():
        if k == "Server" or not isinstance(val, dict):
            continue
        if val.get("device_id", "").lower() == str(box_id).lower() or k.lower() == str(box_id).lower():
            topic = val.get("ntfy_channel", ADMIN_TOPIC)
            box_name = val.get("name", box_id)
            tz_name = val.get("timezone", tz_name)
            turnaround_enabled = val.get("turnaround_detection_enabled", True)
            ntfy_enabled = val.get("ntfy_enabled", True)
            auto_reset_30m = val.get("auto_reset_after_30m", True)

            custom_labels = val.get("channel_labels", {})
            ch_key = f"temp{ch_num}"
            if ch_key in custom_labels and custom_labels[ch_key]:
                fname = custom_labels[ch_key]

            box_td = val.get("turnaround_detection", {})
            turnaround_th.update(box_td)

            box_sd = val.get("setting_detection", {})
            setting_th.update(box_sd)
            break

    return topic, box_name, fname, tz_name, turnaround_enabled, turnaround_th, setting_th, ntfy_enabled, auto_reset_30m


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


def check_heartbeats(conn):
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT a.device_id, d.last_seen 
                FROM analyzer_state a
                LEFT JOIN device_status d ON a.device_id = d.device_id
                WHERE a.started_at IS NOT NULL AND a.export_120_sent = FALSE;
            """)
            active_devices = cur.fetchall()
            now = datetime.now(timezone.utc)

            for dev in active_devices:
                dev_id = dev["device_id"]
                last_seen = dev["last_seen"]

                is_offline = False
                if not last_seen:
                    is_offline = True
                else:
                    if last_seen.tzinfo is None:
                        last_seen = last_seen.replace(tzinfo=timezone.utc)
                    if (now - last_seen).total_seconds() > 60:
                        is_offline = True

                if is_offline and not offline_alert_state.get(dev_id):
                    offline_alert_state[dev_id] = True
                    topic, box_name, _, tz_name, _, _, _, ntfy_enabled, _ = get_box_metadata(dev_id, 0)
                    title = f"OFFLINE-ALARM: {box_name}"
                    msg = f"Achtung: {box_name} ({dev_id}) liefert seit >60s keine Messwerte mehr an den Server, obwohl eine Messung laeuft!"

                    if ntfy_enabled:
                        send_ntfy("Admin", msg, title=title, priority=4, tags=["warning", "satellite_antenna"])
                        if topic and topic != "Admin":
                            send_ntfy(topic, msg, title=title, priority=4, tags=["warning", "satellite_antenna"])
                    record_alert(conn, dev_id, 99, "HEARTBEAT_LOST", now, title, msg)

                elif not is_offline and offline_alert_state.get(dev_id):
                    offline_alert_state[dev_id] = False
                    topic, box_name, _, tz_name, _, _, _, ntfy_enabled, _ = get_box_metadata(dev_id, 0)
                    title = f"ONLINE: {box_name}"
                    msg = f"{box_name} ({dev_id}) liefert wieder Messwerte."

                    if ntfy_enabled:
                        send_ntfy("Admin", msg, title=title, priority=3, tags=["white_check_mark", "satellite_antenna"])
                        if topic and topic != "Admin":
                            send_ntfy(topic, msg, title=title, priority=3, tags=["white_check_mark", "satellite_antenna"])
                    record_alert(conn, dev_id, 99, "HEARTBEAT_RESTORED", now, title, msg)
    except Exception as e:
        logger.error(f"[HEARTBEAT ERR] {e}")


def process_active_streams():
    detector = SettingDetector()
    exporter = ExportGenerator()

    while True:
        conn = None
        try:
            conn = get_db_connection()
            conn.autocommit = True
            check_heartbeats(conn)

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT s.device_id, s.channel, s.job_id, s.started_at,
                           s.turnaround_sent, s.trigger_sent, s.export_30_sent, s.export_120_sent,
                           s.t_ab_time, s.t_ab_temp, s.force_export
                    FROM analyzer_state s
                    WHERE s.started_at IS NOT NULL
                      AND (s.export_120_sent = FALSE OR s.force_export = TRUE);
                """)
                active_runs = cur.fetchall()

            if not active_runs:
                release_db_connection(conn)
                conn = None
                time.sleep(10)
                continue

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                for state in active_runs:
                    dev = state["device_id"]
                    ch = state["channel"]
                    started_at = state["started_at"]
                    t_ab_temp_stored = state.get("t_ab_temp")
                    topic, box_name, fname, tz_name, td_enabled, td_th, sd_th, ntfy_enabled, auto_reset_30m = get_box_metadata(dev, ch)
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

                    times = [r["bucket"] for r in data]
                    temps = [float(r["temp"]) for r in data]

                    cur.execute("""
                        SELECT time_bucket('10s', time) AS bucket,
                               ROUND(AVG(temperature)::numeric, 2) AS ambient
                        FROM telemetry_data
                        WHERE device_id = %s AND channel = 100 AND time >= %s
                        GROUP BY bucket ORDER BY bucket ASC;
                    """, (dev, started_at))
                    amb_data = {r["bucket"]: float(r["ambient"]) for r in cur.fetchall()}
                    ambs = [amb_data.get(t, 20.0) for t in times]

                    # 1. Sofort-Export (EXP)
                    if state.get("force_export"):
                        logger.info(f"[EXP] Manueller Export fuer {display_name}...")
                        buf, t_ab_calc, temp_ab_calc = exporter.generate_plot(times, temps, ambs, display_name, tz_str=tz_name)
                        cur_trig_temp = t_ab_temp_stored if t_ab_temp_stored is not None else temp_ab_calc
                        csv_bytes = exporter.generate_csv(times, temps, ambs, display_name, tz_str=tz_name, t_ab_dt=state.get("t_ab_time"), t_ab_temp=cur_trig_temp)
                        try:
                            tz = ZoneInfo(tz_name)
                        except Exception:
                            tz = ZoneInfo(DEFAULT_TZ_NAME)
                        ts_key = datetime.now(tz).strftime("%Y%m%d_%H%M")
                        file_tag = sanitize_filename(f"temp{ch}_{fname}_{ts_key}")
                        save_to_archive(dev, file_tag, buf, csv_bytes)

                        title = f"Export: {display_name}"
                        msg = f"Manueller Export fuer {display_name} (Aktuell: {temps[-1]:.2f} °C)."
                        if ntfy_enabled:
                            send_ntfy(topic, msg, title=title, priority=3, tags=["bar_chart"], attach_buf=buf, filename=f"manual_{box_name}_{fname}.png")
                        record_alert(conn, dev, ch, "MANUAL_EXPORT", datetime.now(timezone.utc), title, msg)

                        cur.execute("UPDATE analyzer_state SET force_export = FALSE WHERE device_id = %s AND channel = %s;", (dev, ch))

                    # 3-Minuten-Sperrzeit nach Start
                    now_utc = datetime.now(started_at.tzinfo or timezone.utc)
                    if (now_utc - started_at).total_seconds() < 180:
                        continue

                    # 2. Turnaround / Wendepunkt
                    if not state["turnaround_sent"] and td_enabled:
                        is_turn, t_min, c_delta, r_delta = detector.check_turnaround(temps, thresholds=td_th)
                        if is_turn:
                            now_turn = datetime.now(timezone.utc)
                            cur.execute("""
                                UPDATE analyzer_state 
                                SET turnaround_sent = TRUE, t_min_temp = %s, t_min_time = %s
                                WHERE device_id = %s AND channel = %s;
                            """, (t_min, now_turn, dev, ch))

                            t_str = to_local_str(now_turn, tz_name)
                            title = f"Info: Wendepunkt {display_name}"
                            msg = f"Tiefstwert um {t_str} bei {t_min:.2f} °C durchschritten (Abkuehlung: {c_delta:.2f} °C, Anstieg: +{r_delta:.2f} °C)."
                            if ntfy_enabled:
                                send_ntfy(topic, msg, title=title, priority=3, tags=["chart_with_downwards_trend", "arrow_up"])
                            record_alert(conn, dev, ch, "TURNAROUND", now_turn, title, msg)

                    # 3. Abbindebeginn Trigger (Erfordert erkannten Turnaround)
                    if state.get("turnaround_sent") and not state["trigger_sent"]:
                        trig_type, accel, slope = detector.evaluate_triggers(times, temps, thresholds=sd_th)
                        if trig_type:
                            buf, t_ab_dt, temp_ab = exporter.generate_plot(times, temps, ambs, display_name, tz_str=tz_name)
                            t_event = t_ab_dt if t_ab_dt else datetime.now(timezone.utc)
                            cur.execute("""
                                UPDATE analyzer_state 
                                SET trigger_sent = TRUE, t_ab_time = %s, t_ab_temp = %s
                                WHERE device_id = %s AND channel = %s;
                            """, (t_event, temp_ab, dev, ch))
                            t_ab_temp_stored = temp_ab

                            t_str = to_local_str(t_event, tz_name)
                            title = f"Beton-Alarm: {display_name} bindet ab!"
                            msg = f"Abbindebeginn erkannt um {t_str} bei {temp_ab:.2f} °C (Aktuell: {temps[-1]:.2f} °C)."
                            if ntfy_enabled:
                                send_ntfy(topic, msg, title=title, priority=4, tags=["rotating_light", "warning"], attach_buf=buf, filename=f"setting_{box_name}_{fname}.png")
                            record_alert(conn, dev, ch, "TRIGGER_SETTING", t_event, title, msg)

                    # 4. Automatischer Export (+30 Min & +120 Min)
                    if state["trigger_sent"] and state["t_ab_time"]:
                        t_ref = state["t_ab_time"]
                        elapsed = (datetime.now(t_ref.tzinfo or timezone.utc) - t_ref).total_seconds()
                        try:
                            tz = ZoneInfo(tz_name)
                        except Exception:
                            tz = ZoneInfo(DEFAULT_TZ_NAME)

                        # 30-Minuten-Report
                        if not state["export_30_sent"] and elapsed >= 1800:
                            buf, _, _ = exporter.generate_plot(times, temps, ambs, display_name, tz_str=tz_name)
                            csv_bytes = exporter.generate_csv(times, temps, ambs, display_name, tz_str=tz_name, t_ab_dt=t_ref, t_ab_temp=t_ab_temp_stored)
                            ts_key = datetime.now(tz).strftime("%Y%m%d_%H%M")
                            file_tag = sanitize_filename(f"temp{ch}_{fname}_{ts_key}_30m")
                            save_to_archive(dev, file_tag, buf, csv_bytes)

                            title = f"Report (30min): {display_name}"
                            msg = f"30-Minuten-Fortschrittsbericht nach Abbindebeginn (Trigger bei {t_ab_temp_stored:.2f} °C)." if t_ab_temp_stored is not None else "30-Minuten-Fortschrittsbericht nach Abbindebeginn."
                            if ntfy_enabled:
                                send_ntfy(topic, msg, title=title, priority=3, tags=["bar_chart"], attach_buf=buf, filename=f"report_30m_{box_name}_{fname}.png")
                            record_alert(conn, dev, ch, "REPORT_30M", datetime.now(timezone.utc), title, msg)

                            if auto_reset_30m:
                                cur.execute("DELETE FROM analyzer_state WHERE device_id = %s AND channel = %s;", (dev, ch))
                                logger.info(f"[AUTO-RESET 30M] Kanal {ch} fuer {dev} nach 30m-Report zurueckgesetzt.")
                                check_and_disable_box_logging(conn, dev)
                                continue
                            else:
                                cur.execute("UPDATE analyzer_state SET export_30_sent = TRUE WHERE device_id = %s AND channel = %s;", (dev, ch))

                        # 120-Minuten-Abschlussbericht
                        if not state["export_120_sent"] and elapsed >= 7200:
                            buf, _, _ = exporter.generate_plot(times, temps, ambs, display_name, tz_str=tz_name)
                            csv_bytes = exporter.generate_csv(times, temps, ambs, display_name, tz_str=tz_name, t_ab_dt=t_ref, t_ab_temp=t_ab_temp_stored)
                            ts_key = datetime.now(tz).strftime("%Y%m%d_%H%M")
                            file_tag = sanitize_filename(f"temp{ch}_{fname}_{ts_key}_120m")
                            save_to_archive(dev, file_tag, buf, csv_bytes)

                            title = f"Abschlussbericht (120min): {display_name}"
                            msg = "120-Minuten-Abschlussbericht."
                            if ntfy_enabled:
                                send_ntfy(topic, msg, title=title, priority=3, tags=["chart"], attach_buf=buf, filename=f"report_120m_{box_name}_{fname}.png")
                                send_ntfy(topic, "Messdaten CSV-Export", title=f"CSV: {display_name}", priority=2, tags=["file_folder"], attach_buf=io.BytesIO(csv_bytes), filename=f"data_{box_name}_{fname}.csv")
                            record_alert(conn, dev, ch, "REPORT_120M", datetime.now(timezone.utc), title, msg)

                            cur.execute("UPDATE analyzer_state SET export_120_sent = TRUE WHERE device_id = %s AND channel = %s;", (dev, ch))
                            check_and_disable_box_logging(conn, dev)

        except Exception as err:
            logger.error(f"Fehler im Analyzer-Loop: {err}")
        finally:
            if conn:
                release_db_connection(conn)

        time.sleep(5)


if __name__ == "__main__":
    logger.info("Server Analyzer aktiv (Modular YAMLs & Dynamic Thresholds)...")
    process_active_streams()