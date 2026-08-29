"""
@file: setting_detector.py
@version: 2.6.0
@date: 2026-08-28
@description: Zentrale Erkennungs-Engine fuer Wendepunkt (t_min), Abbindebeginn (Trigger via Rotationsbeschleunigung),
              dynamische Lookback-Ableitungen und Tangentenschnittpunkte.
@author: Patrick Staehli
"""

from datetime import timedelta
import numpy as np
from filters import savitzky_golay

DEFAULT_TURNAROUND_THRESHOLDS = {
    "sg_window": 31,
    "cooling_slope_min": -0.0003,
    "reheating_slope_min": 0.0003,
    "min_cooling_delta": 0.20
}

DEFAULT_TRIGGER_THRESHOLDS = {
    "sg_window": 21,
    "poly_order": 2,
    "lookback_sec": 120,
    "min_samples": 15,
    "accel_min": 0.000010,
    "slope_min": 0.0002,
    "reheating_delta_min": 0.15,
    "fallback_samples": 5,
    "fallback_step_min": 0.020,
    "fallback_reheating_min": 0.20
}


class SettingDetector:
    def check_turnaround(self, raw_temps, thresholds=None):
        """Erkennt das Temperaturminimum (t_min) mit Abkuehl- und Wiederanstiegskriterium."""
        th = dict(DEFAULT_TURNAROUND_THRESHOLDS)
        if thresholds:
            th.update(thresholds)

        w_size = int(th.get("sg_window", 31))
        cooling_slope_limit = float(th.get("cooling_slope_min", -0.0003))
        reheating_slope_limit = float(th.get("reheating_slope_min", 0.0003))
        min_cooling_delta = float(th.get("min_cooling_delta", 0.20))

        if len(raw_temps) < max(25, w_size):
            return False, 0.0, 0.0, 0.0

        temps_arr = np.array(raw_temps, dtype=float)
        slope = savitzky_golay(temps_arr, window_size=w_size, order=2, deriv=1)
        smooth = savitzky_golay(temps_arr, window_size=w_size, order=2, deriv=0)

        lookback = min(len(slope) - 3, 60)
        if lookback < 3:
            return False, 0.0, 0.0, 0.0

        min_slope = float(np.min(slope[-lookback:-3]))
        current_slope = float(slope[-1])

        if min_slope < cooling_slope_limit and current_slope > reheating_slope_limit:
            min_temp = float(np.min(smooth[-lookback:]))
            max_before = float(np.max(smooth[:-3])) if len(smooth) > 6 else float(smooth[0])
            cooling_delta = max_before - min_temp
            reheating_delta = float(smooth[-1]) - min_temp

            if cooling_delta >= min_cooling_delta and reheating_delta >= 0.10:
                return True, min_temp, cooling_delta, reheating_delta

        return False, float(smooth[-1]), 0.0, 0.0

    def evaluate_triggers(self, times, raw_temps, t_min_temp=None, thresholds=None):
        """Live-Trigger-Pruefung fuer den aktuellen Messzeitpunkt."""
        th = dict(DEFAULT_TRIGGER_THRESHOLDS)
        if thresholds:
            th.update(thresholds)

        min_samples = int(th.get("min_samples", 15))
        if len(raw_temps) < min_samples or len(times) != len(raw_temps):
            return None, 0.0, 0.0

        temps_arr = np.array(raw_temps, dtype=float)
        w_size = int(th.get("sg_window", 21))
        smooth = savitzky_golay(temps_arr, window_size=w_size, order=int(th.get("poly_order", 2)))
        sec = np.array([(t - times[0]).total_seconds() for t in times])

        # 1. Reheating-Sperre
        reheating_min = float(th.get("reheating_delta_min", 0.15))
        if t_min_temp is not None:
            if (smooth[-1] - t_min_temp) < reheating_min:
                return None, 0.0, 0.0

        # 2. Lookback-Polynomfit (Krümmung / Beschleunigung)
        lookback_sec = int(th.get("lookback_sec", 120))
        mask_lookback = sec >= (sec[-1] - lookback_sec)
        t_sub = sec[mask_lookback]
        temp_sub = smooth[mask_lookback]

        if len(t_sub) >= 5:
            try:
                poly = np.polyfit(t_sub - t_sub[0], temp_sub, 2)
                accel = float(2.0 * poly[0])
                slope = float(2.0 * poly[0] * (t_sub[-1] - t_sub[0]) + poly[1])

                accel_limit = float(th.get("accel_min", 0.000010))
                slope_limit = float(th.get("slope_min", 0.0002))

                if accel >= accel_limit and slope >= slope_limit:
                    return "curvature_trigger", accel, slope
            except Exception:
                pass

        # 3. Notfall-Fallback
        fb_reheating = float(th.get("fallback_reheating_min", reheating_min + 0.05))
        if t_min_temp is not None and (smooth[-1] - t_min_temp) >= fb_reheating:
            fb_samples = int(th.get("fallback_samples", 5))
            fb_step = float(th.get("fallback_step_min", 0.020))
            last_fb = temps_arr[-fb_samples:]
            if len(last_fb) == fb_samples and all(np.diff(last_fb) > fb_step):
                return "slope_fallback", 0.0, float(np.mean(np.diff(last_fb)))

        return None, 0.0, 0.0

    def calculate_acceleration_series(self, times, raw_temps, thresholds=None):
        """
        Berechnet die geglättete Temperatur sowie eine beruhigte Beschleunigungsserie
        exakt synchron zum Trigger-Lookback-Polynom.
        """
        th = dict(DEFAULT_TRIGGER_THRESHOLDS)
        if thresholds:
            th.update(thresholds)

        w_size = int(th.get("sg_window", 21))
        lookback_sec = int(th.get("lookback_sec", 120))

        smooth = savitzky_golay(np.array(raw_temps, dtype=float), window_size=w_size, order=2)
        sec = np.array([(t - times[0]).total_seconds() for t in times])

        accel_series_raw = []
        slope_series_raw = []

        for i in range(len(times)):
            mask = (sec <= sec[i]) & (sec >= sec[i] - lookback_sec)
            t_win = sec[mask]
            temp_win = smooth[mask]

            if len(t_win) >= 5:
                try:
                    p = np.polyfit(t_win - t_win[0], temp_win, 2)
                    cur_a = float(2.0 * p[0])
                    cur_s = float(2.0 * p[0] * (t_win[-1] - t_win[0]) + p[1])
                    accel_series_raw.append(cur_a)
                    slope_series_raw.append(cur_s)
                except Exception:
                    accel_series_raw.append(0.0)
                    slope_series_raw.append(0.0)
            else:
                accel_series_raw.append(0.0)
                slope_series_raw.append(0.0)

        # Leichte Glättung der Serie zur Beseitigung diskreter Abtastartefakte
        k_len = max(5, min(15, lookback_sec // 15))
        if k_len % 2 == 0:
            k_len += 1
        kernel = np.ones(k_len) / float(k_len)
        accel_series = np.convolve(accel_series_raw, kernel, mode='same').tolist()
        slope_series = np.convolve(slope_series_raw, kernel, mode='same').tolist()

        return smooth, accel_series, slope_series

    def calculate_tangent_intersection(self, times, raw_temps):
        """Berechnet den Tangentenschnittpunkt (t_ab)."""
        if len(raw_temps) < 30:
            return None, None, None, None, None, None

        sec = np.array([(t - times[0]).total_seconds() for t in times])
        smooth = savitzky_golay(np.array(raw_temps, dtype=float), window_size=21, order=2)
        d1 = np.gradient(smooth, sec)

        skip_start = min(15, len(smooth) // 4)
        valid_indices = range(skip_start, len(smooth))
        if not valid_indices:
            return None, None, None, None, None, None

        max_slope_idx = skip_start + int(np.argmax(d1[skip_start:]))
        t_max_sec = sec[max_slope_idx]

        w_t1 = (sec >= t_max_sec - 300) & (sec <= t_max_sec + 300)
        if np.sum(w_t1) < 3:
            return None, None, None, None, None, None
        m1, b1 = np.polyfit(sec[w_t1], smooth[w_t1], 1)

        pre_wendepunkt = smooth[:max_slope_idx]
        min_idx = int(np.argmin(pre_wendepunkt)) if len(pre_wendepunkt) > 0 else 0
        t_min_sec = sec[min_idx]

        w_t2 = (sec >= t_min_sec - 600) & (sec <= t_min_sec + 600)
        if np.sum(w_t2) < 3:
            w_t2 = (sec >= max(0, t_min_sec - 120)) & (sec <= t_min_sec + 120)

        m2, b2 = np.polyfit(sec[w_t2], smooth[w_t2], 1)

        if abs(m1 - m2) < 1e-8:
            return None, None, None, None, None, None

        xs = (b2 - b1) / (m1 - m2)
        if xs < (t_min_sec - 900) or xs > t_max_sec:
            return None, None, None, None, None, None

        t_ab_dt = times[0] + timedelta(seconds=xs)
        temp_ab = float(m1 * xs + b1)
        return t_ab_dt, temp_ab, m1, b1, m2, b2