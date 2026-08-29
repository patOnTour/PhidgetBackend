"""
@file: routes/simulator.py
@version: 3.0.0
@date: 2026-08-29
@description: Simulator Blueprint mit konsistenter Beschleunigungsberechnung via SettingDetector 
              und zentralisiertem ConfigManager fuer threadsicheres Laden und Speichern von Parametern.
@author: Patrick Staehli
"""

import os
import io
import math
import importlib
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint, render_template, request, jsonify, send_file

from core.config_manager import config_manager

simulator_bp = Blueprint("simulator", __name__)

import filters
import setting_detector
from setting_detector import SettingDetector
from export_generator import ExportGenerator

DEFAULT_TZ = ZoneInfo("Europe/Zurich")


def parse_swiss_csv(file_stream):
    """Liest Schweizer CSVs (Semikolon-getrennt, Punkt als Dezimaltrenner) flexibel ein."""
    content = file_stream.read()
    if isinstance(content, bytes):
        content = content.decode('utf-8-sig', errors='replace')
        
    df = pd.read_csv(io.StringIO(content), sep=';', decimal='.', engine='python')
    
    time_col = None
    for col in df.columns:
        c_low = str(col).lower()
        if any(k in c_low for k in ['zeit', 'time', 'timestamp', 'datum']):
            time_col = col
            break
            
    if not time_col:
        time_col = df.columns[0]
        
    df['parsed_time'] = pd.to_datetime(df[time_col], dayfirst=True, errors='coerce')
    df = df.dropna(subset=['parsed_time']).sort_values('parsed_time').reset_index(drop=True)
    
    temp_col = None
    amb_col = None
    
    for col in df.columns:
        if col in [time_col, 'parsed_time']:
            continue
        c_low = str(col).lower()
        if 'umgebung' in c_low or 'ambient' in c_low or 'ch100' in c_low or 'luft' in c_low:
            amb_col = col
        elif temp_col is None and any(k in c_low for k in ['temp', 'kanal', 'ch', 'beton', 'grad', '°c']):
            temp_col = col
            
    if not temp_col:
        temp_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

    times = df['parsed_time'].tolist()
    temps = pd.to_numeric(df[temp_col], errors='coerce').ffill().bfill().tolist()
    ambs = pd.to_numeric(df[amb_col], errors='coerce').ffill().bfill().tolist() if amb_col else None

    return times, temps, ambs, temp_col


@simulator_bp.route("/simulator")
def simulator_index():
    cfg = config_manager.get_parsed_config()
    return render_template("simulator.html", boxes=cfg["boxes"], config=cfg)


@simulator_bp.route("/api/simulator/get-params/<dev_key>", methods=["GET"])
def get_device_params(dev_key):
    mapping = config_manager.get_device_map()
    meta = mapping.get(str(dev_key).strip().lower())
    
    if not meta:
        return jsonify({"success": False, "error": f"Gerät '{dev_key}' nicht gefunden."}), 404

    data = meta["data"]
    return jsonify({
        "success": True,
        "device_key": meta["root_key"],
        "turnaround_detection": data.get("turnaround_detection", {}),
        "setting_detection": data.get("setting_detection", {})
    })


@simulator_bp.route("/api/simulator/save-device-params", methods=["POST"])
def save_device_params():
    req_data = request.get_json() or {}
    dev_key = str(req_data.get("device_key", "")).strip().lower()
    
    if not dev_key:
        return jsonify({"success": False, "error": "Kein Gerätebezeichner übermittelt."}), 400

    mapping = config_manager.get_device_map()
    meta = mapping.get(dev_key)

    if not meta:
        return jsonify({"success": False, "error": f"Keine passende Konfiguration für '{dev_key}' gefunden."}), 404

    root_key = meta["root_key"]
    raw_yaml = config_manager.load_all_raw()
    box_data = raw_yaml.get(root_key, {})

    td_incoming = req_data.get("turnaround_detection", {})
    sd_incoming = req_data.get("setting_detection", {})

    if "turnaround_detection" not in box_data:
        box_data["turnaround_detection"] = {}
    if "setting_detection" not in box_data:
        box_data["setting_detection"] = {}

    # Validierte Übernahme der Parameter
    if isinstance(td_incoming, dict):
        for k, v in td_incoming.items():
            if v is not None and str(v).strip() != "":
                box_data["turnaround_detection"][k] = int(v) if k == "sg_window" else float(v)

    if isinstance(sd_incoming, dict):
        for k, v in sd_incoming.items():
            if v is not None and str(v).strip() != "":
                if k in ["sg_window", "poly_order", "lookback_sec", "min_samples", "fallback_samples"]:
                    box_data["setting_detection"][k] = int(v)
                else:
                    box_data["setting_detection"][k] = float(v)

    if config_manager.save_device_config(root_key, box_data):
        return jsonify({
            "success": True, 
            "message": f"Erfolgreich für '{root_key}' gespeichert."
        })
    
    return jsonify({"success": False, "error": "Fehler beim atomaren Speichern."}), 500


@simulator_bp.route("/api/simulator/analyze", methods=["POST"])
def analyze_csv():
    try:
        importlib.reload(filters)
        importlib.reload(setting_detector)
    except Exception as e:
        print(f"[RELOAD WARN] {e}", flush=True)

    from setting_detector import SettingDetector
    from filters import savitzky_golay

    if "file" not in request.files:
        return jsonify({"success": False, "error": "Keine CSV-Datei übermittelt."}), 400

    csv_file = request.files["file"]
    try:
        times, raw_temps, ambs, label = parse_swiss_csv(csv_file.stream)
    except Exception as e:
        return jsonify({"success": False, "error": f"CSV-Parsing-Fehler: {str(e)}"}), 400

    n_samples = len(raw_temps)
    if n_samples < 15:
        return jsonify({"success": False, "error": "Zu wenige Messpunkte in der CSV (mind. 15 erforderlich)."}), 400

    form = request.form
    sg_w = int(form.get("td_sg_window", 31))
    c_slope_min = float(form.get("td_cooling_slope_min", -0.0003))
    r_slope_min = float(form.get("td_reheating_slope_min", 0.0003))
    min_c_delta = float(form.get("td_min_cooling_delta", 0.20))

    sd_lookback = int(form.get("sd_lookback_sec", 120))
    sd_accel = float(form.get("sd_accel_min", 0.000010))
    sd_slope = float(form.get("sd_slope_min", 0.0002))

    td_thresholds = {
        "sg_window": sg_w,
        "cooling_slope_min": c_slope_min,
        "reheating_slope_min": r_slope_min,
        "min_cooling_delta": min_c_delta
    }

    sd_thresholds = {
        "sg_window": 21,
        "poly_order": 2,
        "lookback_sec": sd_lookback,
        "min_samples": 15,
        "accel_min": sd_accel,
        "slope_min": sd_slope,
        "reheating_delta_min": 0.15,
        "fallback_samples": 5,
        "fallback_step_min": 0.020,
        "fallback_reheating_min": 0.20
    }

    detector = SettingDetector()
    sec = np.array([(t - times[0]).total_seconds() for t in times])

    # 1. Glättung & stetige Beschleunigungsserie via SettingDetector (kaskadierte Faltungsglättung)
    smooth_temps, accel_series, slope_series = detector.calculate_acceleration_series(
        times, raw_temps, thresholds=sd_thresholds
    )

    # 2. Tangentenschnittpunkt via SettingDetector
    t_ab_dt, temp_ab, m1, b1, m2, b2 = detector.calculate_tangent_intersection(times, raw_temps)

    # 3. Kausaler Loop fuer Gatekeeper & Triggererkennung
    turnaround_found = False
    turnaround_time_str = "Nicht erkannt"
    turnaround_armed_time_str = "Nicht scharf"
    turnaround_temp = None
    t_min_val = None

    trigger_time_dt = None
    trigger_idx = None
    trigger_type = "Warten"
    ntfy_alert_time_str = "Warten"
    ntfy_delay_str = "-"
    trigger_fit_segment = None

    for i in range(15, n_samples):
        sub_times = times[:i + 1]
        sub_temps = raw_temps[:i + 1]
        sub_smooth = smooth_temps[:i + 1]

        # A) Wendepunkt (Turnaround)
        if not turnaround_found:
            is_turn, detected_min, _, _ = detector.check_turnaround(sub_temps, thresholds=td_thresholds)
            if is_turn and (sub_smooth[-1] - detected_min) >= 0.15:
                turnaround_found = True
                t_min_val = detected_min
                turnaround_temp = round(detected_min, 2)
                turnaround_armed_time_str = times[i].strftime("%H:%M:%S")

                # Exaktes Minimum in der bisherigen Kurve ermitteln
                min_idx = int(np.argmin(sub_smooth))
                turnaround_time_str = times[min_idx].strftime("%H:%M:%S")

        # B) Abbinde-Trigger
        if turnaround_found and trigger_time_dt is None:
            trig_name, trig_accel, trig_slope = detector.evaluate_triggers(
                sub_times, sub_temps, t_min_temp=t_min_val, thresholds=sd_thresholds
            )
            if trig_name:
                trigger_time_dt = times[i]
                trigger_idx = i
                trigger_type = trig_name
                ntfy_alert_time_str = times[i].strftime("%H:%M:%S")
                if t_ab_dt:
                    diff_sec = int((times[i] - t_ab_dt).total_seconds())
                    if diff_sec >= 0:
                        ntfy_delay_str = f"+{diff_sec // 60}m {diff_sec % 60}s nach t_ab"
                    else:
                        diff_abs = abs(diff_sec)
                        ntfy_delay_str = f"-{diff_abs // 60}m {diff_abs % 60}s vor t_ab"

    # Segment der Fit-Parabel im Lookback-Bereich fuer das Frontend extrahieren
    if trigger_idx is not None:
        t_trig_sec = sec[trigger_idx]
        mask_fit = (sec <= t_trig_sec) & (sec >= t_trig_sec - sd_lookback)
        t_fit = sec[mask_fit]
        idx_fit = np.where(mask_fit)[0]
        if len(t_fit) >= 5:
            poly_fit = np.polyfit(t_fit - t_fit[0], smooth_temps[mask_fit], 2)
            fit_curve = np.polyval(poly_fit, t_fit - t_fit[0]).tolist()
            trigger_fit_segment = {
                "times": [times[j].strftime("%H:%M:%S") for j in idx_fit],
                "temps": [round(float(v), 2) for v in fit_curve],
                "trigger_time": times[trigger_idx].strftime("%H:%M:%S"),
                "trigger_temp": round(float(smooth_temps[trigger_idx]), 2)
            }

    # Tangentenreihen
    tangent_steigung = []
    tangent_ruhe = []
    if m1 is not None and b1 is not None and m2 is not None and b2 is not None:
        tangent_steigung = (m1 * sec + b1).tolist()
        tangent_ruhe = (m2 * sec + b2).tolist()

    time_strs = [t.strftime("%H:%M:%S") for t in times]
    raw_rounded = [round(float(v), 2) for v in raw_temps]
    smooth_rounded = [round(float(v), 2) for v in smooth_temps]

    metrics = {
        "turnaround_detected": turnaround_found,
        "t_min_temp": turnaround_temp if turnaround_temp is not None else 0.0,
        "turnaround_time": turnaround_time_str,
        "turnaround_armed_time": turnaround_armed_time_str,
        "t_ab_time": t_ab_dt.strftime("%H:%M:%S") if t_ab_dt else "Nicht erkannt",
        "t_ab_temp": round(float(temp_ab), 2) if temp_ab else None,
        "trigger_type": trigger_type,
        "ntfy_alert_time": ntfy_alert_time_str,
        "ntfy_delay": ntfy_delay_str,
        "trigger_accel_limit": sd_accel
    }

    payload_data = {
        "times": time_strs,
        "timestamps": time_strs,
        "raw_temps": raw_rounded,
        "smooth_temps": smooth_rounded,
        "ambients": [round(float(v), 2) for v in ambs] if ambs else [],
        "slope_series": [float(v) for v in slope_series],
        "accel_series": [float(v) for v in accel_series],
        "tangent_steigung": [round(float(v), 2) for v in tangent_steigung] if len(tangent_steigung) > 0 else None,
        "tangent_ruhe": [round(float(v), 2) for v in tangent_ruhe] if len(tangent_ruhe) > 0 else None,
        "trigger_fit_segment": trigger_fit_segment,
        "metrics": metrics,
        **metrics
    }

    return jsonify({
        "success": True,
        "data": payload_data,
        **payload_data
    })


@simulator_bp.route("/api/simulator/export-plot", methods=["POST"])
def export_plot():
    """Generiert einen validen PNG-Export mit dem echten ExportGenerator."""
    data = request.get_json() or {}
    raw_times = data.get("times", [])
    temps = data.get("temps", [])
    ambs = data.get("ambs", None)
    label = data.get("label", "Simulation")

    if not raw_times or not temps:
        return jsonify({"success": False, "error": "Keine Daten fuer Export vorhanden."}), 400

    parsed_times = []
    base_date = datetime.now(DEFAULT_TZ).date()
    for ts_str in raw_times:
        try:
            t_obj = datetime.strptime(ts_str, "%H:%M:%S").time()
            parsed_times.append(datetime.combine(base_date, t_obj, tzinfo=DEFAULT_TZ))
        except Exception:
            parsed_times.append(datetime.now(DEFAULT_TZ))

    exporter = ExportGenerator()
    buf, _, _ = exporter.generate_plot(parsed_times, temps, ambs, label)

    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=f"Report_{label}.png")