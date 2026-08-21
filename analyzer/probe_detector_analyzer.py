#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import logging
from datetime import datetime, timezone
import numpy as np
import yaml
import psycopg2
from psycopg2.extras import RealDictCursor
import requests

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
NTFY_URL = os.getenv("NTFY_URL", "http://ntfy:80")
INGEST_CONTROL_URL = os.getenv("INGEST_CONTROL_URL", "https://telemetry.concretum-setting.com/api/v1/control/start-channel")
YAML_PATH = os.getenv("YAML_CONFIG_PATH", "/app/config/devices.yaml")
ADMIN_TOPIC = "Concretum"

DEFAULT_THRESHOLDS = {
    "delta_t_min": 0.80,
    "slope_min": 0.015,
    "rot_peak_min": 0.035
}

COOLDOWN_SECONDS = 300
POLL_INTERVAL = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [PROBE-DETECTOR] %(message)s")
logger = logging.getLogger("ProbeDetector")

plug_alert_cooldown = {}

def get_db():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )
    conn.autocommit = True
    return conn

def get_box_config(box_id, ch_num):
    topic = ADMIN_TOPIC
    box_name = str(box_id)
    fname = f"Kanal {ch_num + 1}"
    thresholds = dict(DEFAULT_THRESHOLDS)
    
    if not os.path.exists(YAML_PATH):
        return topic, box_name, fname, thresholds
        
    try:
        with open(YAML_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            
            # Server Defaults übernehmen
            server_pd = cfg.get("Server", {}).get("probe_detection", {})
            for k in thresholds:
                if k in server_pd:
                    thresholds[k] = float(server_pd[k])

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
                    
                    # Gerätespezifische Übersteuerung
                    box_pd = val.get("probe_detection", {})
                    for th_k in thresholds:
                        if th_k in box_pd:
                            thresholds[th_k] = float(box_pd[th_k])
                    break
    except Exception as e:
        logger.error(f"Fehler beim Laden von devices.yaml: {e}")
        
    return topic, box_name, fname, thresholds

def send_plug_ntfy(topic, device_id, channel, box_name, friendly_name, delta_t, current_temp):
    url = f"{NTFY_URL.rstrip('/')}"
    display_name = f"{box_name} - {friendly_name}"
    
    payload = {
        "topic": topic,
        "title": f"Sonde erkannt: {display_name}",
        "message": f"Einstich erkannt: {delta_t:+.2f} °C in <60s (Aktuell: {current_temp:.2f} °C).\nMessung für {friendly_name} starten?",
        "priority": 4,
        "tags": ["electric_plug", "bell"],
        "actions": [
            {
                "action": "http",
                "label": f"{box_name}: {friendly_name} starten",
                "url": INGEST_CONTROL_URL,
                "method": "POST",
                "headers": {
                    "Content-Type": "application/json"
                },
                "body": json.dumps({"device_id": device_id, "channel": channel})
            }
        ]
    }
    
    try:
        res = requests.post(url, json=payload, timeout=5)
        if res.status_code == 200:
            logger.info(f"[NTFY OK] Einstich-Alarm fuer {display_name} an Topic '{topic}'")
        else:
            logger.error(f"[NTFY ERR] Status {res.status_code}: {res.text}")
    except Exception as ex:
        logger.error(f"[NTFY EXCEPTION] Fehler beim Senden: {ex}")

def evaluate_tangent_rotation(temps, thresholds):
    if len(temps) < 15:
        return False, 0.0
    
    kernel = np.ones(5) / 5.0
    smooth = np.convolve(temps, kernel, mode='valid')
    if len(smooth) < 10:
        return False, 0.0

    delta_t = smooth[-1] - smooth[0]
    if abs(delta_t) < thresholds["delta_t_min"]:
        return False, delta_t

    d1 = np.gradient(smooth)
    d2 = np.gradient(d1)

    max_slope = np.max(np.abs(d1))
    if max_slope < thresholds["slope_min"]:
        return False, delta_t

    max_rot = np.max(d2)
    min_rot = np.min(d2)
    idx_max = np.argmax(d2)
    idx_min = np.argmin(d2)
    rot_limit = thresholds["rot_peak_min"]

    if delta_t > 0 and max_rot > rot_limit and min_rot < -rot_limit and idx_max < idx_min:
        return True, delta_t

    if delta_t < 0 and min_rot < -rot_limit and max_rot > rot_limit and idx_min < idx_max:
        return True, delta_t

    return False, delta_t

def scan_for_probe_events():
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT device_id 
                FROM telemetry_data 
                WHERE channel = 100 AND time >= NOW() - INTERVAL '30 seconds';
            """)
            active_boxes = [r['device_id'] for r in cur.fetchall()]
            if not active_boxes:
                return

            cur.execute("""
                SELECT t.device_id, t.channel, t.temperature, t.time
                FROM telemetry_data t
                WHERE t.device_id = ANY(%s)
                  AND t.channel BETWEEN 0 AND 7
                  AND t.time >= NOW() - INTERVAL '60 seconds'
                  AND NOT EXISTS (
                      SELECT 1 FROM analyzer_state s
                      WHERE s.device_id = t.device_id 
                        AND s.channel = t.channel
                        AND s.started_at IS NOT NULL
                        AND s.export_120_sent = FALSE
                  )
                ORDER BY t.device_id, t.channel, t.time ASC;
            """, (active_boxes,))
            rows = cur.fetchall()

    if not rows:
        return

    channel_series = {}
    for r in rows:
        key = (r['device_id'], r['channel'])
        channel_series.setdefault(key, []).append(float(r['temperature']))

    for (dev, ch), temps in channel_series.items():
        last_alert = plug_alert_cooldown.get((dev, ch))
        if last_alert and (now - last_alert).total_seconds() < COOLDOWN_SECONDS:
            continue

        topic, box_name, fname, th = get_box_config(dev, ch)
        is_insertion, delta_t = evaluate_tangent_rotation(temps, th)
        if is_insertion:
            plug_alert_cooldown[(dev, ch)] = now
            current_temp = temps[-1]
            logger.info(f"Einstich erkannt! {box_name} | {fname} (Ch {ch}): {delta_t:+.2f} °C (Jetzt {current_temp:.2f} °C)")
            send_plug_ntfy(topic, dev, ch, box_name, fname, delta_t, current_temp)

def main():
    logger.info("Probe Detector Daemon aktiv (Dynamische YAML Schwellenwerte)...")
    while True:
        try:
            scan_for_probe_events()
        except Exception as e:
            logger.error(f"Fehler im Scan-Loop: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
