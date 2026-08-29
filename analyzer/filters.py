"""
@file: filters.py
@version: 1.0.0
@date: 2026-08-24
@description: Mathematische Basis-Filter fuer Zeitreihenanalysen (Savitzky-Golay, Moving Average, Polynomfilter 2. Ordnung).
@author: Patrick Staehli
"""

import math
import numpy as np


def savitzky_golay(y, window_size=31, order=2, deriv=0, rate=1):
    """
    Glaettet Daten oder berechnet deren Ableitung mittels Savitzky-Golay-Filter.
    """
    try:
        window_size = np.abs(int(window_size))
        order = np.abs(int(order))
        if window_size % 2 != 1 or window_size < 1:
            window_size = 31
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


def moving_average(y, window_size=5):
    """
    Einfacher gleitender Mittelwertfilter.
    """
    if len(y) < window_size:
        return np.array(y, dtype=float)
    kernel = np.ones(window_size) / float(window_size)
    return np.convolve(y, kernel, mode='valid')


def polynomial_smooth_2nd_order(x_sec, y_vals):
    """
    Passt ein Polynom 2. Ordnung ueber das gesamte Fenster an und gibt die geglaetteten Werte zurueck.
    """
    if len(y_vals) < 3:
        return np.array(y_vals, dtype=float)
    try:
        poly = np.polyfit(x_sec - x_sec[0], y_vals, 2)
        fitted = np.polyval(poly, x_sec - x_sec[0])
        return fitted
    except Exception:
        return np.array(y_vals, dtype=float)
