"""
@file: math_engine.py
@version: 2.2.0
@date: 2026-08-26
@description: Mathematische Berechnungs-Engine fuer die Telemetrie. Angepasst an saubere 20s-Intervalle (erweitertes Lookback-Fenster, angepasste Schwellenwerte).
@author: Patrick Staehli
"""

import io
import math
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

DEFAULT_TZ = ZoneInfo("Europe/Zurich")

DEFAULT_TRIGGER_THRESHOLDS = {
    "sg_window": 15,
    "poly_order": 2,
    "lookback_sec": 300,
    "min_samples": 15,
    "accel_min": 0.0000015,
    "slope_min": 0.0003,
    "fallback_samples": 5,
    "fallback_step_min": 0.015
}

DEFAULT_TURNAROUND_THRESHOLDS = {
    "sg_window": 21,
    "cooling_slope_min": -0.0002,
    "reheating_slope_min": 0.0002,
    "min_cooling_delta": 0.15
}

class ConcreteAnalyzer:
    @staticmethod
    def savitzky_golay(y, window_size=21, order=2, deriv=0, rate=1):
        try:
            window_size = np.abs(int(window_size))
            order = np.abs(int(order))
            if window_size % 2 != 1 or window_size < 1:
                window_size = 21
            if window_size < order + 2:
                order = 2
            order_range = range(order + 1)
            half_window = (window_size - 1) // 2
            b = np.asmatrix([[k**i for i in order_range] for k in range(-half_window, half_window + 1)])
            m = np.linalg.pinv(b).A[deriv] * (rate**deriv) * math.factorial(deriv)
            firstvals = y[0] - np.abs(y[1:half_window + 1][::-1] - y[0])
            lastvals = y[-1] + np.abs(y[-half_window - 1:-1][::-1] - y[-1])
            y_padded = np.concatenate((firstvals, y, lastvals))
            return np.convolve(m[::-1], y_padded, mode='valid')
        except Exception:
            return np.array(y, dtype=float)

    def check_turnaround(self, raw_temps, thresholds=None):
        th = dict(DEFAULT_TURNAROUND_THRESHOLDS)
        if thresholds:
            th.update(thresholds)

        w_size = int(th.get("sg_window", 21))
        cooling_slope_limit = float(th.get("cooling_slope_min", -0.0002))
        reheating_slope_limit = float(th.get("reheating_slope_min", 0.0002))
        min_cooling_delta = float(th.get("min_cooling_delta", 0.15))

        if len(raw_temps) < max(25, w_size):
            return False, 0.0, 0.0, 0.0

        temps_arr = np.array(raw_temps, dtype=float)
        slope = self.savitzky_golay(temps_arr, window_size=w_size, order=2, deriv=1)
        smooth = self.savitzky_golay(temps_arr, window_size=w_size, order=2, deriv=0)

        lookback = min(len(slope) - 3, 60)
        if lookback < 3:
            return False, 0.0, 0.0, 0.0

        min_slope = np.min(slope[-lookback:-3])
        current_slope = slope[-1]

        if min_slope < cooling_slope_limit and current_slope > reheating_slope_limit:
            min_temp = float(np.min(smooth[-lookback:]))
            max_before = float(np.max(smooth[:-3])) if len(smooth) > 6 else float(smooth[0])
            cooling_delta = max_before - min_temp
            reheating_delta = float(smooth[-1]) - min_temp

            if cooling_delta >= min_cooling_delta and reheating_delta >= 0.03:
                return True, min_temp, cooling_delta, reheating_delta

        return False, float(smooth[-1]), 0.0, 0.0

    def evaluate_triggers(self, times, raw_temps, thresholds=None):
        th = dict(DEFAULT_TRIGGER_THRESHOLDS)
        if thresholds:
            th.update(thresholds)

        min_samples = int(th.get("min_samples", 15))
        if len(raw_temps) < min_samples:
            return None, 0.0, 0.0

        w_size = int(th.get("sg_window", 15))
        smooth = self.savitzky_golay(np.array(raw_temps, dtype=float), window_size=w_size, order=2)
        sec = np.array([(t - times[0]).total_seconds() for t in times])

        lookback_sec = int(th.get("lookback_sec", 300))
        mask_lookback = sec >= (sec[-1] - lookback_sec)
        t_sub = sec[mask_lookblock if 'mask_lookblock' in locals() else mask_lookback]
        temp_sub = smooth[mask_lookback]

        if len(t_sub) >= 4:
            try:
                poly = np.polyfit(t_sub - t_sub[0], temp_sub, 2)
                accel = float(2.0 * poly[0])
                slope = float(2.0 * poly[0] * (t_sub[-1] - t_sub[0]) + poly[1])

                if accel >= float(th.get("accel_min", 0.0000015)) and slope > float(th.get("slope_min", 0.0003)):
                    return "curvature_trigger", accel, slope
            except Exception:
                pass

        fb_samples = int(th.get("fallback_samples", 5))
        fb_step = float(th.get("fallback_step_min", 0.015))
        last_fb = raw_temps[-fb_samples:]
        if len(last_fb) == fb_samples and all(np.diff(last_fb) > fb_step):
            return "slope_fallback", 0.0, float(np.mean(np.diff(last_fb)))

        return None, 0.0, 0.0

    def calculate_tangent_intersection(self, times, raw_temps):
        if len(raw_temps) < 30:
            return None, None, None, None, None, None

        sec = np.array([(t - times[0]).total_seconds() for t in times])
        smooth = self.savitzky_golay(np.array(raw_temps, dtype=float), window_size=21, order=2)
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

    def generate_plot(self, times, temps, ambs, display_label, tz_str="Europe/Zurich"):
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = DEFAULT_TZ

        local_times = [
            (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(tz) 
            for t in times
        ]

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(local_times, temps, label=f'Beton {display_label}', color='#ff6600', linewidth=2, zorder=3)
        
        all_vals = list(temps)
        if ambs is not None and len(ambs) == len(times):
            ax.plot(local_times, ambs, label='Umgebung', color='#0056b3', linestyle='--', alpha=0.7, zorder=2)
            all_vals.extend(ambs)

        t_ab_dt, temp_ab, m1, b1, m2, b2 = self.calculate_tangent_intersection(times, temps)
        if t_ab_dt is not None:
            t_ab_local = (t_ab_dt if t_ab_dt.tzinfo else t_ab_dt.replace(tzinfo=timezone.utc)).astimezone(tz)
            ax.axvline(x=t_ab_local, color='red', linestyle=':', label=f'Abbindebeginn: {t_ab_local.strftime("%H:%M:%S")} ({temp_ab:.2f} °C)', zorder=4)
            ax.scatter([t_ab_local], [temp_ab], color='red', s=90, zorder=5)

            sec_list = np.array([(t - times[0]).total_seconds() for t in times])
            y1 = m1 * sec_list + b1
            y2 = m2 * sec_list + b2

            ax.plot(local_times, y1, color='#28a745', linestyle='--', alpha=0.7, label='Steigungstangente', zorder=2)
            ax.plot(local_times, y2, color='#6c757d', linestyle='--', alpha=0.7, label='Ruhetangente', zorder=2)

            all_vals.append(temp_ab)

        y_min = math.floor(min(all_vals) - 1.5)
        y_max = math.ceil(max(all_vals) + 1.5)
        ax.set_ylim(y_min, y_max)

        ax.set_title(f'Abbindeverhalten - {display_label}', fontsize=12, fontweight='bold')
        ax.set_xlabel('Uhrzeit (Lokalzeit)', fontsize=10)
        ax.set_ylabel('Temperatur (°C)', fontsize=10)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=tz))
        ax.grid(True, linestyle='--', alpha=0.6)
        ax.legend(loc='upper left', framealpha=0.9)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150)
        buf.seek(0)
        plt.close(fig)
        return buf, t_ab_dt, temp_ab

    def generate_csv(self, times, temps, ambs, display_label, tz_str="Europe/Zurich", t_ab_dt=None, t_ab_temp=None):
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = DEFAULT_TZ

        local_time_strs = [
            (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(tz).strftime('%d.%m.%Y %H:%M:%S') 
            for t in times
        ]

        t_ab_str = "-"
        if t_ab_dt is not None:
            t_ab_local = (t_ab_dt if t_ab_dt.tzinfo else t_ab_dt.replace(tzinfo=timezone.utc)).astimezone(tz)
            t_ab_str = t_ab_local.strftime('%d.%m.%Y %H:%M:%S')

        t_ab_temp_str = f"{t_ab_temp:.2f}" if t_ab_temp is not None else "-"

        df = pd.DataFrame({
            'Zeitstempel_Lokal': local_time_strs,
            f'Temperatur_{display_label}_GradC': temps,
            'Umgebung_GradC': ambs if ambs is not None else [20.0] * len(times),
            'Abbindebeginn_Zeitstempel': [t_ab_str] * len(times),
            'Abbindebeginn_Temperatur_GradC': [t_ab_temp_str] * len(times)
        })
        return df.to_csv(index=False, sep=';').encode('utf-8')