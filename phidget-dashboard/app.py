import os
import json
import re
import threading
import time
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request, send_from_directory, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.json")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")

# Datenbank-Konfiguration (zieht Werte sauber aus Umgebungsvariablen)
DB_HOST = os.environ.get("DB_HOST", "timescaledb")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
DB_NAME = os.environ.get("DB_NAME", "telemetry_db")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS", "")

app = Flask(__name__)

channel_states = {}   # {box_id: {temp0: "RESET", ...}}
latest_messages = {}  # {box_id: {"message": "...", "time": "13:56:00", "title": "..."}}
trigger_events = []   # [{"box_id": "...", "box_name": "...", "time": "...", "title": "...", "message": "..."}]

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {"default_ntfy_server": "https://ntfy.sh", "boxes": []}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Config Error] {e}", flush=True)
        return {"default_ntfy_server": "https://ntfy.sh", "boxes": []}

def get_db_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            connect_timeout=3
        )
    except Exception:
        return None

def parse_and_update_channel_status(box_id, message_text):
    if not message_text:
        return
    if box_id not in channel_states:
        channel_states[box_id] = {}

    # Strenger Matcher: nur definierte Statuswörter parsen
    pattern = r"(?:`|\b)(temp\d+)(?:`|\b)[^:]*:\s*(?:[^\w\s]+\s*)?(RESET|RUNNING|TRIGGERED|FINISHED|EXPORT|STOPPED|BEREIT|OFFLINE)"
    matches = re.findall(pattern, message_text, re.IGNORECASE)
    for ch_id, status in matches:
        channel_states[box_id][ch_id] = status.strip().upper()

def ntfy_listener_thread(box):
    bid = box["id"]
    bname = box.get("name", bid)
    topic = box.get("topic")
    server = box.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    if not topic:
        return

    # 1. Letzte Meldung beim Start abfragen
    try:
        res = requests.get(f"{server}/{topic}/json?poll=1", timeout=5)
        if res.status_code == 200:
            for line in res.text.strip().split("\n"):
                if not line:
                    continue
                data = json.loads(line)
                if data.get("event") == "message":
                    msg = data.get("message", "")
                    title = data.get("title", "")
                    t_str = datetime.fromtimestamp(data.get("time", time.time()), timezone.utc).strftime("%H:%M:%S")
                    latest_messages[bid] = {"message": msg, "time": t_str, "title": title}
                    parse_and_update_channel_status(bid, msg)
    except Exception as e:
        print(f"[ntfy Poll Error] {bid}: {e}", flush=True)

    # 2. Live SSE Event-Stream
    while True:
        try:
            with requests.get(f"{server}/{topic}/json", stream=True, timeout=60) as resp:
                for line in resp.iter_lines():
                    if not line:
                        continue
                    event_data = json.loads(line.decode("utf-8"))
                    if event_data.get("event") == "message":
                        msg = event_data.get("message", "")
                        title = event_data.get("title", "")
                        t_str = datetime.fromtimestamp(event_data.get("time", time.time()), timezone.utc).strftime("%H:%M:%S")

                        latest_messages[bid] = {"message": msg, "time": t_str, "title": title}
                        parse_and_update_channel_status(bid, msg)

                        if any(k in msg.upper() or k in title.upper() for k in ["TRIGGER", "ALARM", "FERTIG"]):
                            trigger_events.insert(0, {
                                "box_id": bid,
                                "box_name": bname,
                                "time": t_str,
                                "title": title,
                                "message": msg
                            })
                            del trigger_events[20:]
        except Exception:
            time.sleep(5)

def start_background_listeners():
    cfg = load_config()
    for box in cfg.get("boxes", []):
        threading.Thread(target=ntfy_listener_thread, args=(box,), daemon=True).start()

start_background_listeners()

# ==================== ROUTEN ====================

@app.route("/")
def index():
    config = load_config()
    return render_template("index.html", boxes=config.get("boxes", []), config=config)

@app.route("/api/live-data")
def live_data():
    config = load_config()
    conn = get_db_connection()
    boxes_status = {}
    now = datetime.now(timezone.utc)

    for box in config.get("boxes", []):
        bid = box["id"]
        box_data = {
            "online": False,
            "last_seen": "Nie",
            "ambient": None,
            "humidity": None,
            "channel_temps": {},
            "channel_states": channel_states.get(bid, {}),
            "last_message": latest_messages.get(bid, None),
            "export_pairs": []
        }

        if conn:
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (channel) time, channel, temperature, job_id
                        FROM telemetry_data
                        WHERE device_id = %s
                        ORDER BY channel, time DESC
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

                    if latest_time:
                        delta_sec = (now - latest_time).total_seconds()
                        box_data["online"] = delta_sec < 90
                        box_data["last_seen"] = latest_time.strftime("%H:%M:%S")
            except Exception as e:
                print(f"[DB Error] {bid}: {e}", flush=True)

        # Archiv-Dateien scannen, gruppieren und nach Datum/Uhrzeit absteigend sortieren
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
                    # Extrahiert Zeitstempel YYYYMMDD_HHMM aus dem Dateinamen oder nutzt Dateizeit als Fallback
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

            # Absteigend nach Zeitstempel sortieren (neueste Exporte immer ganz oben)
            sorted_pairs = sorted(file_groups.values(), key=lambda x: x.get("_sort_key", ""), reverse=True)
            for pair in sorted_pairs:
                pair.pop("_sort_key", None)

            box_data["export_pairs"] = sorted_pairs[:30]

        boxes_status[bid] = box_data

    if conn:
        conn.close()

    return jsonify({
        "boxes": boxes_status,
        "triggers": trigger_events[:20]
    })

@app.route("/api/send-cmd", methods=["POST"])
def send_cmd():
    data = request.get_json() or {}
    box_id = data.get("box_id")
    cmd = data.get("cmd") or data.get("command")

    if not box_id or not cmd:
        return jsonify({"success": False, "error": "box_id und cmd erforderlich"}), 400

    config = load_config()
    target_box = next((b for b in config.get("boxes", []) if b["id"] == box_id), None)
    if not target_box:
        return jsonify({"success": False, "error": "Box nicht gefunden"}), 404

    topic = target_box.get("topic")
    server = target_box.get("ntfy_server", "https://ntfy.sh").rstrip("/")

    try:
        res = requests.post(
            f"{server}/{topic}",
            data=cmd.encode("utf-8"),
            headers={"Title": f"Command: {box_id}"},
            timeout=5
        )
        return jsonify({"success": res.status_code == 200})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/download/<box_id>/<filename>")
def download_file(box_id, filename):
    safe_dir = os.path.join(ARCHIVE_DIR, box_id)
    if not os.path.exists(os.path.join(safe_dir, filename)):
        abort(404)
    return send_from_directory(safe_dir, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)