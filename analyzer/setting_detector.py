"""
@file: setting_detector.py
@version: 1.0.0
@date: 2026-08-24
@description: Erkennungs-Engine fuer Wendepunkt, Abbindebeginn (Trigger) und Tangentenschnittpunkt bei Beton-Temperaturkurven.
@author: Patrick Staehli
"""

from datetime import timedelta
import numpy as np
from filters import savitzky_golay

DEFAULT_TRIGGER_THRESHOLDS = {
    "sg_window": 25,
    "poly_order": 2,
    "lookback_sec": 120,
    "min_samples": 30,
    "accel_min": 0.0000025,
    "slope_min": 0.0005,
    "fallback_samples": 10,
    "fallback_step_min": 0.02
}

DEFAULT_TURNAROUND_THRESHOLDS = {
    "sg_window": 31,
    "cooling_slope_min": -0.0003,
    "reheating_slope_min": 0.0003,
    "min_cooling_delta": 0.20
}


class SettingDetector:
    def check_turnaround(self, raw_temps, thresholds=None):
        """
        Erkennt den tiefsten Wendepunkt (Uebergang von Abkuehlung zu Wiedererwaermung).
        """
        th = dict(DEFAULT_TURNAROUND_THRESHOLDS)
        if thresholds:
            th.update(thresholds)

        w_size = int(th.get("sg_window", 31))
        cooling_slope_limit = float(th.get("cooling_slope_min", -0.0003))
        reheating_slope_limit = float(th.get("reheating_slope_min", 0.0003))
        min_cooling_delta = float(th.get("min_cooling_delta", 0.20))

        if len(raw_temps) < max(35, w_size):
            return False, 0.0, 0.0, 0.0

        temps_arr = np.array(raw_temps, dtype=float)
        slope = savitzky_golay(temps_arr, window_size=w_size, order=2, deriv=1)
        smooth = savitzky_golay(temps_arr, window_size=w_size, order=2, deriv=0)

        lookback = min(len(slope) - 5, 120)
        if lookback < 5:
            return False, 0.0, 0.0, 0.0

        min_slope = np.min(slope[-lookback:-5])
        current_slope = slope[-1]

        if min_slope < cooling_slope_limit and current_slope > reheating_slope_limit:
            min_temp = float(np.min(smooth[-lookback:]))
            max_before = float(np.max(smooth[:-5])) if len(smooth) > 10 else float(smooth[0])
            cooling_delta = max_before - min_temp
            reheating_delta = float(smooth[-1]) - min_temp

            if cooling_delta >= min_cooling_delta and reheating_delta >= 0.05:
                return True, min_temp, cooling_delta, reheating_delta

        return False, float(smooth[-1]), 0.0, 0.0

    def evaluate_triggers(self, times, raw_temps, thresholds=None):
        """
        Bewertet Beschleunigung und Steigung fuer den Abbindebeginn-Trigger.
        """
        th = dict(DEFAULT_TRIGGER_THRESHOLDS)
        if thresholds:
            th.update(thresholds)

        min_samples = int(th.get("min_samples", 30))
        if len(raw_temps) < min_samples:
            return None, 0.0, 0.0

        w_size = int(th.get("sg_window", 25))
        smooth = savitzky_golay(np.array(raw_temps, dtype=float), window_size=w_size, order=2)
        sec = np.array([(t - times[0]).total_seconds() for t in times])

        lookback_sec = int(th.get("lookback_sec", 120))
        mask_lookback = sec >= (sec[-1] - lookback_sec)
        t_sub = sec[mask_lookback]
        temp_sub = smooth[mask_lookback]

        if len(t_sub) >= 5:
            try:
                poly = np.polyfit(t_sub - t_sub[0], temp_sub, 2)
                accel = float(2.0 * poly[0])
                slope = float(2.0 * poly[0] * (t_sub[-1] - t_sub[0]) + poly[1])

                if accel >= float(th.get("accel_min", 0.0000025)) and slope > float(th.get("slope_min", 0.0005)):
                    return "curvature_trigger", accel, slope
            except Exception:
                pass

        fb_samples = int(th.get("fallback_samples", 10))
        fb_step = float(th.get("fallback_step_min", 0.02))
        last_fb = raw_temps[-fb_samples:]
        if len(last_fb) == fb_samples and all(np.diff(last_fb) > fb_step):
            return "slope_fallback", 0.0, float(np.mean(np.diff(last_fb)))

        return None, 0.0, 0.0

    def calculate_tangent_intersection(self, times, raw_temps):
        """
        Berechnet den Schnittpunkt zwischen Ruhetangente und Steigungstangente (t_ab_dt, temp_ab).
        """
        if len(raw_temps) < 60:
            return None, None, None, None, None, None

        sec = np.array([(t - times[0]).total_seconds() for t in times])
        smooth = savitzky_golay(np.array(raw_temps, dtype=float), window_size=31, order=2)
        d1 = np.gradient(smooth, sec)

        skip_start = min(30, len(smooth) // 4)
        valid_indices = range(skip_start, len(smooth))
        if not valid_indices:
            return None, None, None, None, None, None

        max_slope_idx = skip_start + int(np.argmax(d1[skip_start:]))
        t_max_sec = sec[max_slope_idx]

        w_t1 = (sec >= t_max_sec - 180) & (sec <= t_max_sec + 180)
        if np.sum(w_t1) < 3:
            return None, None, None, None, None, None
        m1, b1 = np.polyfit(sec[w_t1], smooth[w_t1], 1)

        pre_wendepunkt = smooth[:max_slope_idx]
        min_idx = int(np.argmin(pre_wendepunkt)) if len(pre_wendepunkt) > 0 else 0
        t_min_sec = sec[min_idx]

        w_t2 = (sec >= t_min_sec - 300) & (sec <= t_min_sec + 300)
        if np.sum(w_t2) < 3:
            w_t2 = (sec >= max(0, t_min_sec - 60)) & (sec <= t_min_sec + 60)

        m2, b2 = np.polyfit(sec[w_t2], smooth[w_t2], 1)

        if abs(m1 - m2) < 1e-8:
            return None, None, None, None, None, None

        xs = (b2 - b1) / (m1 - m2)
        if xs < (t_min_sec - 600) or xs > t_max_sec:
            return None, None, None, None, None, None

        t_ab_dt = times[0] + timedelta(seconds=xs)
        temp_ab = float(m1 * xs + b1)
        return t_ab_dt, temp_ab, m1, b1, m2, b2
