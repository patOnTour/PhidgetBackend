"""
@file: app.py
@version: 2.4.0
@date: 2026-08-29
@description: Dashboard App mit zentralisiertem ConfigManager (core/config_manager.py)
              fuer atomare YAML-Operationen, Thread-/Prozess-Sicherheit, Metadata-APIs und Blueprints.
@author: Patrick Staehli
"""

import os
import re
import yaml
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request, send_from_directory, abort, session, redirect
import zoneinfo

from core.config_manager import config_manager

ALL_TIMEZONES = sorted([tz for tz in zoneinfo.available_timezones() if "/" in tz])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")
TRANSLATIONS_DIR = os.path.join(BASE_DIR, "translations")

DB_HOST = os.environ.get("DB_HOST", "timescaledb")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
DB_NAME = os.environ.get("DB_NAME", "telemetry_db")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS", "")

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

FLASK_SECRET_KEY = os.environ.get("SECRET_KEY", "concretum_fallback_key_2026")
app.secret_key = FLASK_SECRET_KEY

# DB Parameter an App-Konfiguration binden (fuer Blueprints)
app.config["DB_HOST"] = DB_HOST
app.config["DB_PORT"] = DB_PORT
app.config["DB_NAME"] = DB_NAME
app.config["DB_USER"] = DB_USER
app.config["DB_PASS"] = DB_PASS

# Blueprints registrieren
from routes.history import history_bp
app.register_blueprint(history_bp)

from routes.simulator import simulator_bp
app.register_blueprint(simulator_bp)


# ==========================================
# DB CONNECTION HELPER
# ==========================================

def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            cursor_factory=RealDictCursor,
            connect_timeout=3
        )
        return conn
    except Exception as e:
        print(f"[DB Connection Error] {e}", flush=True)
        return None


# ==========================================
# I18N / TRANSLATIONS
# ==========================================

_TRANSLATIONS_CACHE = {}
LANG_NAME_MAP = {"de": "Deutsch", "en": "English", "es": "Español", "fr": "Français", "it": "Italiano"}

def get_available_languages():
    languages = []
    if not os.path.exists(TRANSLATIONS_DIR):
        return [{"code": "de", "name": "Deutsch"}]
    for filename in sorted(os.listdir(TRANSLATIONS_DIR)):
        if filename.endswith(".yaml") or filename.endswith(".yml"):
            code = os.path.splitext(filename)[0].lower()
            languages.append({"code": code, "name": LANG_NAME_MAP.get(code, code.upper())})
    return languages if languages else [{"code": "de", "name": "Deutsch"}]

def load_translations(lang_code):
    global _TRANSLATIONS_CACHE
    if lang_code in _TRANSLATIONS_CACHE:
        return _TRANSLATIONS_CACHE[lang_code]
    file_path = os.path.join(TRANSLATIONS_DIR, f"{lang_code}.yaml")
    if not os.path.exists(file_path):
        file_path = os.path.join(TRANSLATIONS_DIR, "de.yaml")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            _TRANSLATIONS_CACHE[lang_code] = data
            return data
    except Exception:
        return {}

def get_current_language():
    return session.get("lang", "de")

def translate_key(path, lang_code=None, default=""):
    lang = lang_code or get_current_language()
    trans = load_translations(lang)
    keys = path.split(".")
    val = trans
    for k in keys:
        if isinstance(val, dict) and k in val:
            val = val[k]
        else:
            return default or path
    return val if isinstance(val, str) else default

@app.context_processor
def inject_i18n():
    current_lang = get_current_language()
    return {
        "t": lambda key, default="": translate_key(key, current_lang, default),
        "current_lang": current_lang,
        "available_languages": get_available_languages(),
        "translations_json": load_translations(current_lang)
    }


# ==============================================================================
# VIEWS & ROUTEN
# ==============================================================================

@app.route("/set-language", methods=["POST"])
@app.route("/api/set-language", methods=["POST"])
@app.route("/set-language/<lang_code>", methods=["GET"])
def set_language(lang_code=None):
    valid_codes = [l["code"] for l in get_available_languages()]
    
    if request.method == "POST":
        data = request.get_json() or {}
        lang = data.get("lang", "de").strip().lower()
        if lang in valid_codes:
            session["lang"] = lang
            return jsonify({"success": True, "lang": lang})
        return jsonify({"success": False, "error": f"Language '{lang}' not supported"}), 400

    if lang_code and lang_code.lower() in valid_codes:
        session["lang"] = lang_code.lower()
        return redirect(request.referrer or "/")
        
    return jsonify({"success": True, "lang": session.get("lang", "de")})

@app.route("/")
def index():
    cfg = config_manager.get_parsed_config()
    return render_template("index.html", boxes=cfg["boxes"], config=cfg)

@app.route("/config")
def config_page():
    cfg = config_manager.get_parsed_config()
    return render_template(
        "config.html", 
        boxes=cfg["boxes"], 
        server=cfg["server"], 
        all_timezones=ALL_TIMEZONES
    )

@app.route("/api/metadata/options")
def get_metadata_options():
    server_cfg = config_manager.get_server_config()
    srv_opts = server_cfg.get("metadata_options", {})
    default_opts = {
        "locations": ["Extern", "EbiLab", "Ebirec"],
        "interfaces": ["Lieferschein", "BT11", "LUCY", "ANA", "IDA"],
        "cement_names": ["cem100", "cem50"],
        "recipes": {},
        "cement_ids": {}
    }
    default_opts.update(srv_opts)
    return jsonify(default_opts)

@app.route("/api/metadata/get/<box_id>")
def get_channel_metadata(box_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "DB Connection Error"}), 500
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT channel, location, interface, custom_string, recipe_id, cement_name, cement_id, last_updated
                FROM device_channel_metadata
                WHERE device_id = %s;
            """, (str(box_id),))
            rows = cur.fetchall()
            
            meta_map = {}
            for r in rows:
                ch_idx = int(r["channel"])
                meta_map[ch_idx] = {
                    "channel": ch_idx,
                    "location": r.get("location") or "Extern",
                    "interface": r.get("interface") or "Lieferschein",
                    "custom_string": r.get("custom_string") or "",
                    "recipe_id": r.get("recipe_id") or "",
                    "cement_name": r.get("cement_name") or "cem100",
                    "cement_id": r.get("cement_id") or "",
                    "last_updated": r.get("last_updated").isoformat() if r.get("last_updated") else None
                }
            return jsonify({"success": True, "metadata": meta_map})
    except Exception as e:
        print(f"[Metadata Get Error for {box_id}] {e}", flush=True)
        return jsonify({"success": False, "error": str(e), "metadata": {}}), 200
    finally:
        conn.close()

@app.route("/api/metadata/save", methods=["POST"])
def save_channel_metadata():
    data = request.get_json() or {}
    box_id = data.get("box_id")
    channel = int(data.get("channel", 0))
    location = data.get("location", "Extern")
    interface = data.get("interface", "Lieferschein")
    custom_string = data.get("custom_string", "")
    recipe_id = data.get("recipe_id", "")
    cement_name = data.get("cement_name", "cem100")
    cement_id = data.get("cement_id", "")

    if not box_id:
        return jsonify({"success": False, "error": "box_id fehlt"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "DB Connection Error"}), 500

    now = datetime.now(timezone.utc)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO device_channel_metadata 
                    (device_id, channel, location, interface, custom_string, recipe_id, cement_name, cement_id, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_id, channel) DO UPDATE SET
                    location = EXCLUDED.location,
                    interface = EXCLUDED.interface,
                    custom_string = EXCLUDED.custom_string,
                    recipe_id = EXCLUDED.recipe_id,
                    cement_name = EXCLUDED.cement_name,
                    cement_id = EXCLUDED.cement_id,
                    last_updated = EXCLUDED.last_updated;
            """, (box_id, channel, location, interface, custom_string, recipe_id, cement_name, cement_id, now))

            cur.execute("""
                UPDATE device_channel_metadata_history
                SET valid_to = %s
                WHERE device_id = %s AND channel = %s AND valid_to IS NULL;
            """, (now, box_id, channel))

            cur.execute("""
                INSERT INTO device_channel_metadata_history
                    (device_id, channel, location, interface, custom_string, recipe_id, cement_name, cement_id, valid_from, valid_to)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL);
            """, (box_id, channel, location, interface, custom_string, recipe_id, cement_name, cement_id, now))

        dev_map = config_manager.get_device_map()
        meta = dev_map.get(str(box_id).strip().lower())
        if meta:
            target_key = meta["yaml_key"]
            raw_yaml = config_manager.load_all_raw()
            box_data = raw_yaml.get(target_key, {})
            if "channel_metadata" not in box_data:
                box_data["channel_metadata"] = {}
            box_data["channel_metadata"][f"temp{channel}"] = {
                "location": location,
                "interface": interface,
                "custom_string": custom_string,
                "recipe_id": recipe_id,
                "cement_name": cement_name,
                "cement_id": cement_id
            }
            config_manager.save_device_config(target_key, box_data)

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

# ==========================================
# API ENDPOINTS
# ==========================================

@app.route("/api/live-data")
def live_data():
    cfg = config_manager.get_parsed_config()
    conn = get_db_connection()
    boxes_status = {}
    db_triggers = []
    now = datetime.now(timezone.utc)

    box_names = {b["id"]: b["name"] for b in cfg["boxes"]}
    all_dev_ids = list(box_names.keys())

    for box in cfg["boxes"]:
        boxes_status[box["id"]] = {
            "online": False,
            "last_seen": "Nie",
            "pending_count": 0,
            "ambient": None,
            "humidity": None,
            "channel_temps": {},
            "channel_states": {ch["id"]: "RESET" for ch in box["channels"]},
            "channel_starts_ms": {},
            "last_message": None,
            "channel_recording_enabled": box.get("channel_recording_enabled", True),
            "auto_detection_enabled": box.get("auto_detection_enabled", False),
            "turnaround_detection_enabled": box.get("turnaround_detection_enabled", False),
            "ntfy_enabled": box.get("ntfy_enabled", False)
        }

    if conn and all_dev_ids:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, device_id, channel, event_type, event_time, title, message
                    FROM alerts_history
                    WHERE acknowledged = FALSE
                    ORDER BY event_time DESC
                    LIMIT 20;
                """)
                for r in cur.fetchall():
                    t_val = r["event_time"]
                    if t_val and t_val.tzinfo is None:
                        t_val = t_val.replace(tzinfo=timezone.utc)
                    db_triggers.append({
                        "id": r["id"],
                        "box_id": r["device_id"],
                        "box_name": box_names.get(r["device_id"], r["device_id"]),
                        "time": t_val.strftime("%H:%M:%S") if t_val else "-",
                        "title": r["title"],
                        "message": r["message"]
                    })

                cur.execute("""
                    SELECT DISTINCT ON (device_id, channel) device_id, channel, temperature, time, job_id
                    FROM telemetry_data
                    WHERE device_id = ANY(%s) AND time >= NOW() - INTERVAL '2 hours'
                    ORDER BY device_id, channel, time DESC;
                """, (all_dev_ids,))
                for r in cur.fetchall():
                    bid = r["device_id"]
                    if bid not in boxes_status:
                        continue
                    ch_num = r["channel"]
                    val = float(r["temperature"]) if r["temperature"] is not None else None
                    if ch_num == 100 or r.get("job_id") == "ambient":
                        boxes_status[bid]["ambient"] = val
                    elif ch_num == 101 or r.get("job_id") == "humidity":
                        boxes_status[bid]["humidity"] = val
                    else:
                        boxes_status[bid]["channel_temps"][f"temp{ch_num}"] = val

                cur.execute("""
                    SELECT device_id, last_seen, pending_count 
                    FROM device_status 
                    WHERE device_id = ANY(%s);
                """, (all_dev_ids,))
                for r in cur.fetchall():
                    bid = r["device_id"]
                    if bid not in boxes_status:
                        continue
                    boxes_status[bid]["pending_count"] = r.get("pending_count", 0) or 0
                    ls_time = r.get("last_seen")
                    if ls_time:
                        if ls_time.tzinfo is None:
                            ls_time = ls_time.replace(tzinfo=timezone.utc)
                        delta_sec = (now - ls_time).total_seconds()
                        boxes_status[bid]["online"] = delta_sec < 90
                        boxes_status[bid]["last_seen"] = ls_time.strftime("%H:%M:%S")

                cur.execute("""
                    SELECT device_id, channel, turnaround_sent, trigger_sent, export_120_sent, started_at 
                    FROM analyzer_state 
                    WHERE device_id = ANY(%s);
                """, (all_dev_ids,))
                for ast in cur.fetchall():
                    bid = ast["device_id"]
                    if bid not in boxes_status:
                        continue
                    ch_key = f"temp{ast['channel']}"
                    if not ast.get("started_at"):
                        state_str = "RESET"
                    elif ast.get("export_120_sent"):
                        state_str = "FINISHED"
                    elif ast.get("trigger_sent"):
                        state_str = "SETTING"
                    elif ast.get("turnaround_sent"):
                        state_str = "TURNING"
                    else:
                        state_str = "RUNNING"
                    boxes_status[bid]["channel_states"][ch_key] = state_str
                    if ast.get("started_at"):
                        st_dt = ast["started_at"]
                        if st_dt.tzinfo is None:
                            st_dt = st_dt.replace(tzinfo=timezone.utc)
                        boxes_status[bid]["channel_starts_ms"][ch_key] = int(st_dt.timestamp() * 1000)

                cur.execute("""
                    SELECT DISTINCT ON (device_id) device_id, title, message, event_time 
                    FROM alerts_history 
                    WHERE device_id = ANY(%s) 
                    ORDER BY device_id, event_time DESC;
                """, (all_dev_ids,))
                for r in cur.fetchall():
                    bid = r["device_id"]
                    if bid not in boxes_status:
                        continue
                    t_val = r["event_time"]
                    if t_val and t_val.tzinfo is None:
                        t_val = t_val.replace(tzinfo=timezone.utc)
                    boxes_status[bid]["last_message"] = {
                        "title": r["title"],
                        "message": r["message"],
                        "time": t_val.strftime("%H:%M:%S") if t_val else "-"
                    }
        except Exception as e:
            print(f"[DB Live Error] {e}", flush=True)
        finally:
            conn.close()

    return jsonify({"boxes": boxes_status, "triggers": db_triggers})

@app.route("/api/widget-data/<box_id>")
def get_widget_data(box_id):
    minutes = int(request.args.get("minutes", 15))
    bucket_sec = "20s" if minutes <= 15 else "60s"

    conn = get_db_connection()
    if not conn:
        return jsonify({"series": [], "available_channels": []})

    dev_map = config_manager.get_device_map()
    meta = dev_map.get(str(box_id).strip().lower())
    server_cfg = config_manager.get_server_config()
    server_tz = server_cfg.get("timezone", "Europe/Zurich")
    box_tz_name = meta["data"].get("timezone", server_tz) if meta else server_tz

    try:
        target_tz = zoneinfo.ZoneInfo(box_tz_name)
    except Exception:
        target_tz = zoneinfo.ZoneInfo("Europe/Zurich")

    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT 
                    time_bucket('{bucket_sec}', time) AS bucket,
                    channel,
                    ROUND(AVG(temperature)::numeric, 2) AS avg_temp
                FROM telemetry_data
                WHERE device_id = %s AND time >= NOW() - INTERVAL '{minutes} minutes'
                GROUP BY bucket, channel
                ORDER BY bucket ASC, channel ASC;
            """, (box_id,))
            rows = cur.fetchall()

            time_map = {}
            seen_channels = set()
            for r in rows:
                b_dt = r["bucket"].replace(tzinfo=timezone.utc) if r["bucket"].tzinfo is None else r["bucket"]
                b_time = b_dt.astimezone(target_tz).strftime("%H:%M:%S")
                seen_channels.add(r["channel"])

                if b_time not in time_map:
                    time_map[b_time] = {"time": b_time}
                time_map[b_time][f"ch_{r['channel']}"] = float(r["avg_temp"])

            return jsonify({
                "series": list(time_map.values()),
                "available_channels": sorted(list(seen_channels))
            })
    except Exception as e:
        print(f"[Widget Data Error] {e}", flush=True)
        return jsonify({"series": [], "available_channels": []})
    finally:
        conn.close()

@app.route("/api/archive-files/<box_id>")
def get_archive_files(box_id):
    try:
        box_dir = os.path.join(ARCHIVE_DIR, str(box_id).strip())
        if not os.path.exists(box_dir):
            os.makedirs(box_dir, exist_ok=True)
            return jsonify({"files": []})

        file_groups = {}
        for f in os.listdir(box_dir):
            if f.startswith("."):
                continue
            name, ext = os.path.splitext(f)
            ext = ext.lower()
            if ext not in [".csv", ".png"]:
                continue

            common_key = name
            for prefix in ["Daten_", "Graph_", "daten_", "graph_"]:
                if common_key.startswith(prefix):
                    common_key = common_key[len(prefix):]
                    break

            if common_key not in file_groups:
                ts_match = re.search(r"(\d{8}_\d{4})", common_key)
                try:
                    mtime = os.path.getmtime(os.path.join(box_dir, f))
                except Exception:
                    mtime = 0
                sort_val = ts_match.group(1) if ts_match else str(mtime)

                file_groups[common_key] = {
                    "name": common_key,
                    "csv": None,
                    "png": None,
                    "_sort_key": sort_val
                }

            if ext == ".csv":
                file_groups[common_key]["csv"] = f"/download/{box_id}/{f}"
            elif ext == ".png":
                file_groups[common_key]["png"] = f"/download/{box_id}/{f}"

        sorted_pairs = sorted(file_groups.values(), key=lambda x: x.get("_sort_key", ""), reverse=True)
        for pair in sorted_pairs:
            pair.pop("_sort_key", None)

        return jsonify({"files": sorted_pairs[:30]})
    except Exception as e:
        print(f"[Archive Error for {box_id}] {e}", flush=True)
        return jsonify({"files": []})

@app.route("/api/toggle-logging", methods=["POST"])
@app.route("/api/toggle-channel-recording", methods=["POST"])
def api_toggle_logging():
    data = request.get_json() or {}
    box_id = data.get("box_id")
    state = data.get("enabled", True)
    success = config_manager.update_box_switches(box_id, logging_state=state)
    return jsonify({"success": success})

@app.route("/api/toggle-autodetect", methods=["POST"])
def api_toggle_autodetect():
    data = request.get_json() or {}
    box_id = data.get("box_id")
    state = data.get("enabled", True)
    dev_map = config_manager.get_device_map()
    meta = dev_map.get(str(box_id).strip().lower(), {})
    cur_logging = meta.get("data", {}).get("channel_recording_enabled", True)
    success = config_manager.update_box_switches(box_id, logging_state=cur_logging, auto_detect=state)
    return jsonify({"success": success})

@app.route("/api/toggle-turnaround-detect", methods=["POST"])
@app.route("/api/toggle-turnaround", methods=["POST"])
def api_toggle_turnaround():
    data = request.get_json() or {}
    box_id = data.get("box_id")
    state = data.get("enabled", True)
    dev_map = config_manager.get_device_map()
    meta = dev_map.get(str(box_id).strip().lower(), {})
    cur_logging = meta.get("data", {}).get("channel_recording_enabled", True)
    success = config_manager.update_box_switches(box_id, logging_state=cur_logging, turnaround=state)
    return jsonify({"success": success})

@app.route("/api/toggle-ntfy", methods=["POST"])
def api_toggle_ntfy():
    data = request.get_json() or {}
    box_id = data.get("box_id")
    state = data.get("enabled", True)
    dev_map = config_manager.get_device_map()
    meta = dev_map.get(str(box_id).strip().lower(), {})
    cur_logging = meta.get("data", {}).get("channel_recording_enabled", True)
    success = config_manager.update_box_switches(box_id, logging_state=cur_logging, ntfy_state=state)
    return jsonify({"success": success})

@app.route("/api/update-channel-labels-batch", methods=["POST"])
def update_channel_labels_batch():
    data = request.get_json() or {}
    box_id = data.get("box_id", "").strip().lower()
    labels = data.get("labels", {})

    if not box_id or not labels:
        return jsonify({"success": False, "error": "box_id und labels erforderlich"}), 400

    dev_map = config_manager.get_device_map()
    meta = dev_map.get(box_id)
    if not meta:
        return jsonify({"success": False, "error": "Geraet nicht gefunden"}), 404

    target_key = meta["yaml_key"]
    raw_yaml = config_manager.load_all_raw()
    box_data = raw_yaml.get(target_key, {})

    if "channel_labels" not in box_data:
        box_data["channel_labels"] = {}

    for ch_id, label in labels.items():
        box_data["channel_labels"][ch_id] = str(label).strip()

    if config_manager.save_device_config(target_key, box_data):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Fehler beim Speichern der YAML"}), 500

@app.route("/api/config/save-box", methods=["POST"])
def save_box():
    data = request.get_json() or {}
    yaml_key = data.get("yaml_key", "").strip()
    dev_id = data.get("device_id", "").strip().lower()

    if not dev_id or not yaml_key or yaml_key.lower() == "server":
        return jsonify({"success": False, "error": "Ungültige Box-Parameter."}), 400

    try:
        raw_yaml = config_manager.load_all_raw()
        existing_box = raw_yaml.get(yaml_key, {})
        existing_labels = existing_box.get("channel_labels", {})
        
        for k, v in data.items():
            if k.startswith("chlabel_"):
                ch_name = k.replace("chlabel_", "")
                if str(v).strip():
                    existing_labels[ch_name] = str(v).strip()

        # 1. Probe Detection
        probe_detection = existing_box.get("probe_detection", {})
        if data.get("pd_delta_t_min"): probe_detection["delta_t_min"] = float(data["pd_delta_t_min"])
        if data.get("pd_slope_min"): probe_detection["slope_min"] = float(data["pd_slope_min"])
        if data.get("pd_rot_peak_min"): probe_detection["rot_peak_min"] = float(data["pd_rot_peak_min"])

        # 2. Turnaround Detection
        turnaround_detection = existing_box.get("turnaround_detection", {})
        if data.get("td_sg_window"): turnaround_detection["sg_window"] = int(data["td_sg_window"])
        if data.get("td_cooling_slope_min"): turnaround_detection["cooling_slope_min"] = float(data["td_cooling_slope_min"])
        if data.get("td_reheating_slope_min"): turnaround_detection["reheating_slope_min"] = float(data["td_reheating_slope_min"])
        if data.get("td_min_cooling_delta"): turnaround_detection["min_cooling_delta"] = float(data["td_min_cooling_delta"])

        # 3. Setting Detection
        setting_detection = existing_box.get("setting_detection", {})
        if data.get("sd_sg_window"): setting_detection["sg_window"] = int(data["sd_sg_window"])
        if data.get("sd_poly_order"): setting_detection["poly_order"] = int(data["sd_poly_order"])
        if data.get("sd_lookback_sec"): setting_detection["lookback_sec"] = int(data["sd_lookback_sec"])
        if data.get("sd_min_samples"): setting_detection["min_samples"] = int(data["sd_min_samples"])
        if data.get("sd_accel_min"): setting_detection["accel_min"] = float(data["sd_accel_min"])
        if data.get("sd_slope_min"): setting_detection["slope_min"] = float(data["sd_slope_min"])
        if data.get("sd_fallback_samples"): setting_detection["fallback_samples"] = int(data["sd_fallback_samples"])
        if data.get("sd_fallback_step_min"): setting_detection["fallback_step_min"] = float(data["sd_fallback_step_min"])

        def parse_bool(val, default=True):
            if val is None:
                return default
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes", "on")
            return bool(val)

        box_dict = {
            "name": data.get("name", existing_box.get("name", yaml_key)),
            "box_label": data.get("box_label") or existing_box.get("box_label", ""),
            "channel_count": int(data.get("channel_count", existing_box.get("channel_count", 4))),
            "device_id": dev_id,
            "serial": int(data["serial"]) if str(data.get("serial", "")).isdigit() else existing_box.get("serial"),
            "ntfy_channel": data.get("ntfy_channel", existing_box.get("ntfy_channel", "Concretum")),
            "timezone": data.get("timezone", existing_box.get("timezone", "Europe/Zurich")),
            "ip_lan": data.get("ip_lan") or existing_box.get("ip_lan"),
            "mac_lan": data.get("mac_lan") or existing_box.get("mac_lan"),
            "mac_wlan": data.get("mac_wlan") or existing_box.get("mac_wlan"),
            "workstation": data.get("workstation") or existing_box.get("workstation"),
            "auto_detection_enabled": parse_bool(data.get("auto_detection_enabled", existing_box.get("auto_detection_enabled", False))),
            "turnaround_detection_enabled": parse_bool(data.get("turnaround_detection_enabled", existing_box.get("turnaround_detection_enabled", False))),
            "channel_recording_enabled": parse_bool(data.get("channel_recording_enabled", existing_box.get("channel_recording_enabled", True))),
            "ntfy_enabled": parse_bool(data.get("ntfy_enabled", existing_box.get("ntfy_enabled", False))),
            "channel_labels": existing_labels
        }

        if probe_detection:
            box_dict["probe_detection"] = probe_detection
        if turnaround_detection:
            box_dict["turnaround_detection"] = turnaround_detection
        if setting_detection:
            box_dict["setting_detection"] = setting_detection

        if config_manager.save_device_config(yaml_key, box_dict):
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Speichern fehlgeschlagen"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/config/delete-box", methods=["POST"])
def delete_box():
    data = request.get_json() or {}
    yaml_key = data.get("yaml_key", "").strip()

    if not yaml_key or yaml_key.lower() == "server":
        return jsonify({"success": False, "error": "Ungueltiger Key."}), 400

    if config_manager.delete_device_config(yaml_key):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Loeschen fehlgeschlagen."}), 500

@app.route("/api/clear-triggers", methods=["POST"])
def clear_triggers():
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Keine DB-Verbindung"}), 500
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE alerts_history SET acknowledged = TRUE WHERE acknowledged = FALSE;")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

@app.route("/api/send-cmd", methods=["POST"])
def send_cmd():
    data = request.get_json() or {}
    box_id = data.get("box_id")
    cmd = data.get("cmd") or data.get("command")
    custom_label = data.get("label")

    if not box_id or not cmd:
        return jsonify({"success": False, "error": "box_id und cmd erforderlich"}), 400

    action, ch_str = cmd.split(":") if ":" in cmd else (cmd, "0")
    
    try:
        ch_num = int(re.sub(r"\D", "", ch_str)) if re.sub(r"\D", "", ch_str) else 0
    except ValueError:
        ch_num = 0

    job_id = f"ch{ch_num}"

    if custom_label and ch_str:
        try:
            dev_map = config_manager.get_device_map()
            meta = dev_map.get(str(box_id).strip().lower())
            if meta:
                target_key = meta["yaml_key"]
                raw_yaml = config_manager.load_all_raw()
                box_data = raw_yaml.get(target_key, {})
                if "channel_labels" not in box_data:
                    box_data["channel_labels"] = {}
                box_data["channel_labels"][f"temp{ch_num}"] = str(custom_label).strip()
                config_manager.save_device_config(target_key, box_data)
        except Exception as e:
            print(f"[YAML Cmd Label Error] {e}", flush=True)

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Keine DB-Verbindung"}), 500

    try:
        with conn.cursor() as cur:
            if action.startswith("run"):
                cur.execute("DELETE FROM analyzer_state WHERE device_id = %s AND channel = %s;", (box_id, ch_num))
                cur.execute("""
                    INSERT INTO analyzer_state (
                        device_id, channel, job_id, turnaround_sent, trigger_sent, 
                        export_30_sent, export_120_sent, force_export, started_at
                    )
                    VALUES (%s, %s, %s, FALSE, FALSE, FALSE, FALSE, FALSE, NOW());
                """, (box_id, ch_num, job_id))
                config_manager.update_box_switches(box_id, logging_state=True, auto_detect=True, turnaround=True)

            elif action.startswith("export"):
                cur.execute("""
                    INSERT INTO analyzer_state (device_id, channel, job_id, started_at, force_export)
                    VALUES (%s, %s, %s, NOW() - INTERVAL '2 hours', TRUE)
                    ON CONFLICT (device_id, channel, job_id)
                    DO UPDATE SET force_export = TRUE;
                """, (box_id, ch_num, job_id))

            elif action.startswith("reset"):
                cur.execute("DELETE FROM analyzer_state WHERE device_id = %s AND channel = %s;", (box_id, ch_num))
                cur.execute("""
                    SELECT COUNT(*) FROM analyzer_state 
                    WHERE device_id = %s AND started_at IS NOT NULL AND export_120_sent = FALSE;
                """, (box_id,))
                row = cur.fetchone()
                active_count = row["count"] if isinstance(row, dict) and "count" in row else (row[0] if row else 0)
                if active_count == 0:
                    config_manager.update_box_switches(box_id, logging_state=False)

            conn.commit()

        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

@app.route("/download/<box_id>/<filename>")
def download_file(box_id, filename):
    safe_dir = os.path.join(ARCHIVE_DIR, box_id)
    if not os.path.exists(os.path.join(safe_dir, filename)):
        abort(404)
    return send_from_directory(safe_dir, filename, as_attachment=True)

@app.route("/manual.html")
@app.route("/manual")
def manual_page():
    return render_template("manual.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)