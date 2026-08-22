import os
import re
import yaml
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request, send_from_directory, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_PATH = os.environ.get("YAML_CONFIG_PATH", os.path.join(BASE_DIR, "config", "devices.yaml"))
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")

DB_HOST = os.environ.get("DB_HOST", "timescaledb")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
DB_NAME = os.environ.get("DB_NAME", "telemetry_db")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS", "")

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

def load_yaml_raw():
    if not os.path.exists(YAML_PATH):
        return {"Server": {}}
    try:
        with open(YAML_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[YAML Load Error] {e}", flush=True)
        return {}

def get_parsed_config():
    data = load_yaml_raw()
    server_cfg = data.get("Server", {})
    g_cfg = server_cfg.get("grafana", {})

    boxes = []
    for key, val in data.items():
        if key == "Server" or not isinstance(val, dict):
            continue
        dev_id = val.get("device_id", key)
        ch_count = int(val.get("channel_count", 4))
        custom_labels = val.get("channel_labels", {})

        channels = []
        for i in range(ch_count):
            ch_id = f"temp{i}"
            ch_label = custom_labels.get(ch_id, f"Kanal {i + 1}")
            channels.append({"id": ch_id, "label": ch_label})
        
        # Grafana Basis-URLs dynamisch aus Server-Config bauen
        grafana_base_url = g_cfg.get("server_url", "https://grafana.concretum-setting.com")
        grafana_uid = g_cfg.get("uid", "paskgph")
        grafana_slug = g_cfg.get("slug", "temperatur")
        
        grafana_panels = {
            "temps": f"{grafana_base_url}/d-solo/{grafana_uid}/{grafana_slug}?orgId=1&panelId=2&var-device={dev_id}&theme=dark&kiosk&refresh=10s",
            "table": f"{grafana_base_url}/d-solo/{grafana_uid}/{grafana_slug}?orgId=1&panelId=3&var-device={dev_id}&theme=dark&kiosk&refresh=10s",
            "ambient": f"{grafana_base_url}/d-solo/{grafana_uid}/{grafana_slug}?orgId=1&panelId=4&var-device={dev_id}&theme=dark&kiosk&refresh=10s"
        }

        boxes.append({
            "yaml_key": key,
            "id": dev_id,
            "name": val.get("name", key),
            "box_label": val.get("box_label", ""),
            "topic": val.get("ntfy_channel", "Concretum"),
            "channel_count": ch_count,
            "serial": val.get("serial"),
            "ip_lan": val.get("ip_lan"),
            "mac_lan": val.get("mac_lan"),
            "mac_wlan": val.get("mac_wlan"),
            "workstation": val.get("workstation"),
            "probe_detection": val.get("probe_detection", {}),
            "auto_detection_enabled": val.get("auto_detection_enabled", True),
            "grafana_panels": grafana_panels,
            "channels": channels
        })
    return {"server": server_cfg, "boxes": boxes}

def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS, connect_timeout=3
        )
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"[DB Connect Error] {e}", flush=True)
        return None

@app.route("/")
def index():
    cfg = get_parsed_config()
    return render_template("index.html", boxes=cfg["boxes"], config=cfg)

@app.route("/config")
def config_page():
    cfg = get_parsed_config()
    return render_template("config.html", boxes=cfg["boxes"], server=cfg["server"])

@app.route("/api/live-data")
def live_data():
    cfg = get_parsed_config()
    conn = get_db_connection()
    boxes_status = {}
    db_triggers = []
    now = datetime.now(timezone.utc)

    box_names = {b["id"]: b["name"] for b in cfg["boxes"]}

    if conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, device_id, channel, event_type, event_time, title, message
                    FROM alerts_history
                    WHERE acknowledged = FALSE
                    ORDER BY event_time DESC
                    LIMIT 20;
                """)
                for r in cur.fetchall():
                    t_val = r["event_time"]
                    if t_val.tzinfo is None:
                        t_val = t_val.replace(tzinfo=timezone.utc)
                    db_triggers.append({
                        "id": r["id"],
                        "box_id": r["device_id"],
                        "box_name": box_names.get(r["device_id"], r["device_id"]),
                        "time": t_val.strftime("%H:%M:%S"),
                        "title": r["title"],
                        "message": r["message"]
                    })
        except Exception as e:
            print(f"[DB Alerts Error] {e}", flush=True)

    for box in cfg["boxes"]:
        bid = box["id"]
        box_data = {
            "online": False,
            "last_seen": "Nie",
            "pending_count": 0,
            "ambient": None,
            "humidity": None,
            "channel_temps": {},
            "channel_states": {},
            "channel_starts_ms": {},
            "last_message": None,
            "export_pairs": []
        }

        if conn:
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (channel) channel, temperature, time, job_id
                        FROM telemetry_data
                        WHERE device_id = %s AND time <= NOW()
                        ORDER BY channel, time DESC;
                    """, (bid,))
                    rows = cur.fetchall()

                    latest_time = None
                    for r in rows:
                        t_time = r["time"]
                        if t_time.tzinfo is None:
                            t_time = t_time.replace(tzinfo=timezone.utc)
                        if latest_time is None or t_time > latest_time:
                            latest_time = t_time

                        ch_num = r["channel"]
                        val = r["temperature"]

                        if ch_num == 100 or r.get("job_id") == "ambient":
                            box_data["ambient"] = val
                        elif ch_num == 101 or r.get("job_id") == "humidity":
                            box_data["humidity"] = val
                        else:
                            box_data["channel_temps"][f"temp{ch_num}"] = val

                    # Status & Pufferstand aus device_status laden
                    cur.execute("SELECT last_seen, pending_count FROM device_status WHERE device_id = %s;", (bid,))
                    stat_row = cur.fetchone()
                    if stat_row:
                        box_data["pending_count"] = stat_row.get("pending_count", 0)
                        ls_time = stat_row.get("last_seen")
                        if ls_time:
                            if ls_time.tzinfo is None:
                                ls_time = ls_time.replace(tzinfo=timezone.utc)
                            if latest_time is None or ls_time > latest_time:
                                latest_time = ls_time

                    if latest_time:
                        delta_sec = (now - latest_time).total_seconds()
                        box_data["online"] = delta_sec < 90
                        box_data["last_seen"] = latest_time.strftime("%H:%M:%S")

                    cur.execute("SELECT channel, turnaround_sent, trigger_sent, export_120_sent, started_at FROM analyzer_state WHERE device_id = %s;", (bid,))
                    for ast in cur.fetchall():
                        ch_key = f"temp{ast['channel']}"
                        if ast["export_120_sent"]:
                            state_str = "FINISHED"
                        elif ast["trigger_sent"]:
                            state_str = "TRIGGERED"
                        elif ast["turnaround_sent"]:
                            state_str = "TURNING"
                        else:
                            state_str = "RUNNING"
                        box_data["channel_states"][ch_key] = state_str

                        # Startzeitpunkt in Millisekunden für Grafana Zoom
                        if ast.get("started_at"):
                            st_dt = ast["started_at"]
                            if st_dt.tzinfo is None:
                                st_dt = st_dt.replace(tzinfo=timezone.utc)
                            box_data["channel_starts_ms"][ch_key] = int(st_dt.timestamp() * 1000)

                    cur.execute("SELECT title, message, event_time FROM alerts_history WHERE device_id = %s ORDER BY event_time DESC LIMIT 1;", (bid,))
                    last_alert = cur.fetchone()
                    if last_alert:
                        t_val = last_alert["event_time"]
                        if t_val.tzinfo is None:
                            t_val = t_val.replace(tzinfo=timezone.utc)
                        box_data["last_message"] = {
                            "title": last_alert["title"],
                            "message": last_alert["message"],
                            "time": t_val.strftime("%H:%M:%S")
                        }

            except Exception as e:
                print(f"[DB Loop Error] {bid}: {e}", flush=True)

        for ch in box.get("channels", []):
            if ch["id"] not in box_data["channel_states"]:
                box_data["channel_states"][ch["id"]] = "RESET"

        box_dir = os.path.join(ARCHIVE_DIR, bid)
        if os.path.exists(box_dir):
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
                    file_groups[common_key]["csv"] = f"/download/{bid}/{f}"
                elif ext == ".png":
                    file_groups[common_key]["png"] = f"/download/{bid}/{f}"

            sorted_pairs = sorted(file_groups.values(), key=lambda x: x.get("_sort_key", ""), reverse=True)
            for pair in sorted_pairs:
                pair.pop("_sort_key", None)

            box_data["export_pairs"] = sorted_pairs[:30]

        boxes_status[bid] = box_data

    if conn:
        conn.close()

    return jsonify({"boxes": boxes_status, "triggers": db_triggers})

@app.route("/api/toggle-autodetect", methods=["POST"])
def toggle_autodetect():
    data = request.get_json() or {}
    box_id = data.get("box_id", "").strip().lower()
    enabled = bool(data.get("enabled", True))

    raw_yaml = load_yaml_raw()
    target_key = None
    for k, v in raw_yaml.items():
        if k == "Server" or not isinstance(v, dict):
            continue
        if v.get("device_id", "").lower() == box_id or k.lower() == box_id:
            target_key = k
            break

    if not target_key:
        return jsonify({"success": False, "error": "Gerät nicht gefunden"}), 404

    raw_yaml[target_key]["auto_detection_enabled"] = enabled

    try:
        with open(YAML_PATH, "w", encoding="utf-8") as f:
            yaml.dump(raw_yaml, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return jsonify({"success": True, "enabled": enabled})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/clear-triggers", methods=["POST"])
def clear_triggers():
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Keine DB-Verbindung"}), 500
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE alerts_history SET acknowledged = TRUE WHERE acknowledged = FALSE;")
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            conn.close()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/update-channel-label", methods=["POST"])
def update_channel_label():
    data = request.get_json() or {}
    box_id = data.get("box_id", "").strip().lower()
    ch_id = data.get("channel_id", "").strip().lower()
    label = data.get("label", "").strip()

    if not box_id or not ch_id:
        return jsonify({"success": False, "error": "box_id und channel_id erforderlich"}), 400

    raw_yaml = load_yaml_raw()
    target_key = None
    for k, v in raw_yaml.items():
        if k == "Server" or not isinstance(v, dict):
            continue
        if v.get("device_id", "").lower() == box_id or k.lower() == box_id:
            target_key = k
            break

    if not target_key:
        return jsonify({"success": False, "error": "Gerät nicht gefunden"}), 404

    if "channel_labels" not in raw_yaml[target_key]:
        raw_yaml[target_key]["channel_labels"] = {}

    raw_yaml[target_key]["channel_labels"][ch_id] = label

    try:
        with open(YAML_PATH, "w", encoding="utf-8") as f:
            yaml.dump(raw_yaml, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return jsonify({"success": True, "label": label})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/config/save-box", methods=["POST"])
def save_box():
    data = request.get_json() or {}
    yaml_key = data.get("yaml_key", "").strip()
    dev_id = data.get("device_id", "").strip().lower()

    if not dev_id or not yaml_key:
        return jsonify({"success": False, "error": "YAML Key und Device ID sind Pflicht."}), 400

    raw_yaml = load_yaml_raw()
    if yaml_key == "Server":
        return jsonify({"success": False, "error": "Server-Key ist reserviert."}), 400

    existing_labels = raw_yaml.get(yaml_key, {}).get("channel_labels", {})
    for k, v in data.items():
        if k.startswith("chlabel_"):
            ch_name = k.replace("chlabel_", "")
            if v.strip():
                existing_labels[ch_name] = v.strip()

    probe_detection = {}
    if data.get("pd_delta_t_min"):
        probe_detection["delta_t_min"] = float(data["pd_delta_t_min"])
    if data.get("pd_slope_min"):
        probe_detection["slope_min"] = float(data["pd_slope_min"])
    if data.get("pd_rot_peak_min"):
        probe_detection["rot_peak_min"] = float(data["pd_rot_peak_min"])

    box_dict = {
        "name": data.get("name", yaml_key),
        "box_label": data.get("box_label") or None,
        "channel_count": int(data.get("channel_count", 4)),
        "device_id": dev_id,
        "serial": int(data["serial"]) if str(data.get("serial", "")).isdigit() else None,
        "ntfy_channel": data.get("ntfy_channel", "Concretum"),
        "ip_lan": data.get("ip_lan") or None,
        "mac_lan": data.get("mac_lan") or None,
        "mac_wlan": data.get("mac_wlan") or None,
        "workstation": data.get("workstation") or None,
        "auto_detection_enabled": raw_yaml.get(yaml_key, {}).get("auto_detection_enabled", True)
    }

    if probe_detection:
        box_dict["probe_detection"] = probe_detection
    if existing_labels:
        box_dict["channel_labels"] = existing_labels

    raw_yaml[yaml_key] = box_dict

    try:
        with open(YAML_PATH, "w", encoding="utf-8") as f:
            yaml.dump(raw_yaml, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/config/delete-box", methods=["POST"])
def delete_box():
    data = request.get_json() or {}
    yaml_key = data.get("yaml_key", "").strip()

    if not yaml_key or yaml_key == "Server":
        return jsonify({"success": False, "error": "Ungültiger Key."}), 400

    raw_yaml = load_yaml_raw()
    if yaml_key in raw_yaml:
        del raw_yaml[yaml_key]

    try:
        with open(YAML_PATH, "w", encoding="utf-8") as f:
            yaml.dump(raw_yaml, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/send-cmd", methods=["POST"])
def send_cmd():
    data = request.get_json() or {}
    box_id = data.get("box_id")
    cmd = data.get("cmd") or data.get("command")
    custom_label = data.get("label")

    if not box_id or not cmd:
        return jsonify({"success": False, "error": "box_id und cmd erforderlich"}), 400

    action, ch_str = cmd.split(":") if ":" in cmd else (cmd, None)
    ch_num = int(ch_str.replace("temp", "")) if ch_str and "temp" in ch_str else 0
    job_id = f"ch{ch_num}"

    if custom_label and ch_str:
        raw_yaml = load_yaml_raw()
        for k, v in raw_yaml.items():
            if k != "Server" and isinstance(v, dict) and (v.get("device_id", "").lower() == box_id.lower() or k.lower() == box_id.lower()):
                if "channel_labels" not in v:
                    v["channel_labels"] = {}
                v["channel_labels"][ch_str] = custom_label
                with open(YAML_PATH, "w", encoding="utf-8") as f:
                    yaml.dump(raw_yaml, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                break

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

            elif action.startswith("export"):
                cur.execute("""
                    INSERT INTO analyzer_state (device_id, channel, job_id, started_at, force_export)
                    VALUES (%s, %s, %s, NOW() - INTERVAL '2 hours', TRUE)
                    ON CONFLICT (device_id, channel, job_id)
                    DO UPDATE SET force_export = TRUE;
                """, (box_id, ch_num, job_id))

            elif action.startswith("reset"):
                cur.execute("DELETE FROM analyzer_state WHERE device_id = %s AND channel = %s;", (box_id, ch_num))

        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            conn.close()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/download/<box_id>/<filename>")
def download_file(box_id, filename):
    safe_dir = os.path.join(ARCHIVE_DIR, box_id)
    if not os.path.exists(os.path.join(safe_dir, filename)):
        abort(404)
    return send_from_directory(safe_dir, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)