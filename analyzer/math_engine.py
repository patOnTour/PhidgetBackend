#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

LOCAL_TZ = ZoneInfo("Europe/Zurich")

class ConcreteAnalyzer:
    @staticmethod
    def savitzky_golay(y, window_size=31, order=2, deriv=0, rate=1):
        try:
            window_size = np.abs(int(window_size))
            order = np.abs(int(order))
            if window_size % 2 != 1 or window_size < 1:
                window_size = 31
            if window_size < order + 2:
                order = 2
            order_range = range(order + 1)
            half_window = (window_size - 1) // 2
            b = np.mat([[k**i for i in order_range] for k in range(-half_window, half_window + 1)])
            m = np.linalg.pinv(b).A[deriv] * rate**deriv * np.math.factorial(deriv)
            firstvals = y[0] - np.abs(y[1:half_window + 1][::-1] - y[0])
            lastvals = y[-1] + np.abs(y[-half_window - 1:-1][::-1] - y[-1])
            y_padded = np.concatenate((firstvals, y, lastvals))
            return np.convolve(m[::-1], y_padded, mode='valid')
        except Exception:
            return y

    def check_turnaround(self, raw_temps):
        if len(raw_temps) < 30:
            return False, 0.0, 0.0, 0.0
            
        smooth = self.savitzky_golay(np.array(raw_temps, dtype=float), window_size=31, order=2)
        window = smooth[-180:] if len(smooth) >= 180 else smooth
        min_temp = float(np.min(window))
        min_idx = int(np.argmin(window))
        current_temp = float(smooth[-1])

        pre_min = smooth[:len(smooth) - len(window) + min_idx]
        if len(pre_min) < 10:
            return False, 0.0, 0.0, 0.0

        max_before_min = float(np.max(pre_min[-60:] if len(pre_min) >= 60 else pre_min))
        cooling_delta = max_before_min - min_temp
        reheating_delta = current_temp - min_temp

        if cooling_delta >= 0.15 and reheating_delta >= 0.08:
            if min_idx < (len(window) - 5):
                return True, min_temp, cooling_delta, reheating_delta
                
        return False, min_temp, cooling_delta, reheating_delta

    def evaluate_triggers(self, times, raw_temps):
        if len(raw_temps) < 30:
            return None, 0.0, 0.0

        smooth = self.savitzky_golay(np.array(raw_temps, dtype=float), window_size=25, order=2)
        sec = np.array([(t - times[0]).total_seconds() for t in times])

        t_sub = sec[-120:] if len(sec) >= 120 else sec
        temp_sub = smooth[-120:] if len(smooth) >= 120 else smooth

        try:
            poly = np.polyfit(t_sub - t_sub[0], temp_sub, 2)
            accel = float(2.0 * poly[0])
            slope = float(2.0 * poly[0] * (t_sub[-1] - t_sub[0]) + poly[1])

            if accel >= 0.0000025 and slope > 0.0005:
                return "curvature_trigger", accel, slope
        except Exception:
            pass

        last_10 = raw_temps[-10:]
        if len(last_10) == 10 and all(np.diff(last_10) > 0.02):
            return "slope_fallback", 0.0, float(np.mean(np.diff(last_10)))

        return None, 0.0, 0.0

    def calculate_tangent_intersection(self, times, raw_temps):
        if len(raw_temps) < 60:
            return None, None, None, None, None, None

        sec = np.array([(t - times[0]).total_seconds() for t in times])
        smooth = self.savitzky_golay(np.array(raw_temps, dtype=float), window_size=31, order=2)
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

    def generate_plot(self, times, temps, ambs, display_label):
        # Konvertierung aller Zeitstempel nach Europe/Zurich
        local_times = [
            (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(LOCAL_TZ) 
            for t in times
        ]

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(local_times, temps, label=f'Beton {display_label}', color='#ff6600', linewidth=2)
        if ambs is not None and len(ambs) == len(times):
            ax.plot(local_times, ambs, label='Umgebung', color='#0056b3', linestyle='--', alpha=0.7)

        t_ab_dt, temp_ab, m1, b1, m2, b2 = self.calculate_tangent_intersection(times, temps)
        if t_ab_dt is not None:
            t_ab_local = (t_ab_dt if t_ab_dt.tzinfo else t_ab_dt.replace(tzinfo=timezone.utc)).astimezone(LOCAL_TZ)
            ax.axvline(x=t_ab_local, color='red', linestyle=':', label=f'Abbindebeginn: {t_ab_local.strftime("%H:%M:%S")}')
            ax.scatter([t_ab_local], [temp_ab], color='red', s=100, zorder=5)

            sec_list = np.array([(t - times[0]).total_seconds() for t in times])
            y1 = m1 * sec_list + b1
            y2 = m2 * sec_list + b2
            ax.plot(local_times, y1, color='#28a745', linestyle='--', alpha=0.7, label='Steigungstangente')
            ax.plot(local_times, y2, color='#6c757d', linestyle='--', alpha=0.7, label='Ruhetangente')
            ax.set_ylim(min(temps) - 1.0, max(temps) + 1.0)

        ax.set_title(f'Abbindeverhalten - {display_label}', fontsize=12, fontweight='bold')
        ax.set_xlabel('Uhrzeit (Lokalzeit)', fontsize=10)
        ax.set_ylabel('Temperatur (°C)', fontsize=10)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=LOCAL_TZ))
        ax.grid(True, linestyle='--', alpha=0.6)
        ax.legend(loc='upper left')
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150)
        buf.seek(0)
        plt.close(fig)
        return buf, t_ab_dt, temp_ab

    def generate_csv(self, times, temps, ambs, display_label):
        local_time_strs = [
            (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(LOCAL_TZ).strftime('%d.%m.%Y %H:%M:%S') 
            for t in times
        ]
        df = pd.DataFrame({
            'Zeitstempel_Lokal': local_time_strs,
            f'Temperatur_{display_label}_GradC': temps,
            'Umgebung_GradC': ambs if ambs is not None else [20.0] * len(times)
        })
        return df.to_csv(index=False, sep=';').encode('utf-8')
