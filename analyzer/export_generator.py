"""
@file: export_generator.py
@version: 1.0.0
@date: 2026-08-24
@description: Generator fuer PNG-Diagramme und CSV-Datensaetze von Abbindemessungen mit dynamischer Achsenskalierung und Zeitzonenunterstuetzung.
@author: Patrick Staehli
"""

import io
import math
from datetime import timezone
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from setting_detector import SettingDetector

DEFAULT_TZ = ZoneInfo("Europe/Zurich")


class ExportGenerator:
    def __init__(self):
        self.detector = SettingDetector()

    def generate_plot(self, times, temps, ambs, display_label, tz_str="Europe/Zurich"):
        """
        Erzeugt das Matplotlib-Diagramm mit Ruhetangente, Steigungstangente und Triggertemperatur.
        """
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

        t_ab_dt, temp_ab, m1, b1, m2, b2 = self.detector.calculate_tangent_intersection(times, temps)
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
        """
        Erstellt die tabellarische CSV-Exportdatei mit lokalem Zeitstempel und Triggermesswerten.
        """
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
