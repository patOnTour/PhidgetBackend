/**
 * @file: static/js/simulator.js
 * @version: 2.9.0
 * @date: 2026-08-29
 * @description: Interaktive Steuerung und Plotly-Visualisierung mit synchroner Mikro-Skalierung, 
 *               vollstaendiger Sensorik-Parametrierung (Einstich, Wendepunkt, Setting),
 *               Hamburger-Navigation und Toast-Meldungen (DEV-28).
 * @author: Patrick Stähli
 */

let currentData = null;

function getSelectedDeviceKey() {
  const sel = document.getElementById("selBox") || document.getElementById("target_box");
  if (!sel) return "baustellenkoffer_1";
  const val = sel.value.trim();
  const match = val.match(/\(([^)]+)\)/);
  return match ? match[1] : val;
}

function toggleHamburgerMenu() {
  const menu = document.getElementById('hamburger-menu');
  if (menu) {
    menu.classList.toggle('hidden');
  }
}

document.addEventListener('click', (e) => {
  const btnHam = document.getElementById('btn-hamburger');
  const menuHam = document.getElementById('hamburger-menu');
  if (btnHam && menuHam && !btnHam.contains(e.target) && !menuHam.contains(e.target)) {
    menuHam.classList.add('hidden');
  }
});

async function performAdminLogout() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
    showToast('Admin-Sitzung beendet', 'success');
    setTimeout(() => {
      window.location.href = '/';
    }, 400);
  } catch (e) {
    showToast('Fehler beim Abmelden', 'error');
  }
}

function showToast(msg, type) {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.innerText = msg;
  toast.className = `fixed bottom-4 left-1/2 -translate-x-1/2 px-4 py-2.5 rounded-lg text-xs font-semibold shadow-xl border transition-opacity duration-200 z-50 font-mono ${
    type === 'success' ? 'bg-emerald-900 border-emerald-700 text-emerald-200' : 'bg-rose-900 border-rose-700 text-rose-200'
  } opacity-100`;
  setTimeout(() => { toast.classList.add('opacity-0'); }, 2500);
}

document.addEventListener("DOMContentLoaded", () => {
  const versionEl = document.getElementById("jsVersionDisplay");
  if (versionEl) {
    versionEl.innerText = "JS v2.9.0";
  }

  const selBox = document.getElementById("selBox") || document.getElementById("target_box");
  if (selBox) {
    const savedBox = localStorage.getItem('concretum_sim_active_box');
    if (savedBox && Array.from(selBox.options).some(opt => opt.value === savedBox)) {
      selBox.value = savedBox;
    }

    selBox.addEventListener("change", (e) => {
      localStorage.setItem('concretum_sim_active_box', e.target.value);
      loadDeviceParams(e.target.value);
    });

    if (selBox.value) {
      loadDeviceParams(selBox.value);
    }
  }
});

async function loadBoxProfileParams(devKey) {
  loadDeviceParams(devKey);
}

async function loadDeviceParams(devKey) {
  if (!devKey) return;
  try {
    const res = await fetch(`/api/simulator/get-params/${devKey}`);
    const data = await res.json();
    if (data.success) {
      if (data.probe_detection) {
        const elDt = document.getElementById("pd_delta_t_min");
        const elSl = document.getElementById("pd_slope_min");
        const elRp = document.getElementById("pd_rot_peak_min");
        if (elDt && data.probe_detection.delta_t_min != null) elDt.value = data.probe_detection.delta_t_min;
        if (elSl && data.probe_detection.slope_min != null) elSl.value = data.probe_detection.slope_min;
        if (elRp && data.probe_detection.rot_peak_min != null) elRp.value = data.probe_detection.rot_peak_min;
      }
      if (data.turnaround_detection) {
        const elWin = document.getElementById("td_sg_window");
        const elDel = document.getElementById("td_min_cooling_delta");
        const elCool = document.getElementById("td_cooling_slope_min");
        const elHeat = document.getElementById("td_reheating_slope_min");
        if (elWin && data.turnaround_detection.sg_window != null) elWin.value = data.turnaround_detection.sg_window;
        if (elDel && data.turnaround_detection.min_cooling_delta != null) elDel.value = data.turnaround_detection.min_cooling_delta;
        if (elCool && data.turnaround_detection.cooling_slope_min != null) elCool.value = data.turnaround_detection.cooling_slope_min;
        if (elHeat && data.turnaround_detection.reheating_slope_min != null) elHeat.value = data.turnaround_detection.reheating_slope_min;
      }
      if (data.setting_detection) {
        const elLb = document.getElementById("sd_lookback_sec");
        const elAcc = document.getElementById("sd_accel_min");
        const elSlp = document.getElementById("sd_slope_min");
        if (elLb && data.setting_detection.lookback_sec != null) elLb.value = data.setting_detection.lookback_sec;
        if (elAcc && data.setting_detection.accel_min != null) elAcc.value = data.setting_detection.accel_min;
        if (elSlp && data.setting_detection.slope_min != null) elSlp.value = data.setting_detection.slope_min;
      }
    }
  } catch (err) {
    console.error("Fehler beim Laden der Geräte-Parameter:", err);
  }
}

async function runSimulation() {
  const fileInput = document.getElementById("csvFile");
  const formData = new FormData();

  if (fileInput && fileInput.files.length > 0) {
    formData.append("file", fileInput.files[0]);
  }
  
  formData.append("td_sg_window", document.getElementById("td_sg_window").value);
  formData.append("td_min_cooling_delta", document.getElementById("td_min_cooling_delta").value);
  formData.append("td_cooling_slope_min", document.getElementById("td_cooling_slope_min").value);
  formData.append("td_reheating_slope_min", document.getElementById("td_reheating_slope_min").value);
  formData.append("sd_lookback_sec", document.getElementById("sd_lookback_sec").value);
  formData.append("sd_accel_min", document.getElementById("sd_accel_min").value);
  formData.append("sd_slope_min", document.getElementById("sd_slope_min").value);

  try {
    const res = await fetch("/api/simulator/analyze", { method: "POST", body: formData });
    const result = await res.json();

    if (!result.success) {
      showToast(result.error || "Analysefehler", "error");
      return;
    }

    currentData = result.data;
    renderPlot(result.data);
    updateMetrics(result.data.metrics || result.data);
    showToast("Simulation erfolgreich berechnet!", "success");
  } catch (err) {
    showToast("Netzwerkfehler bei der Analyse", "error");
  }
}

function updateMetrics(m) {
  const isTurn = m.turnaround_detected || (m.turnaround_temp !== null && m.turnaround_temp !== undefined);
  document.getElementById("metricTurn").innerText = isTurn 
    ? `${(m.turnaround_temp || m.t_min_temp).toFixed(2)} °C (${m.turnaround_time || ''})`
    : "Nicht erkannt";
    
  document.getElementById("metricTab").innerText = m.t_ab_time || "Nicht erkannt";
  document.getElementById("metricTempAb").innerText = m.t_ab_temp ? `${m.t_ab_temp.toFixed(2)} °C` : "-";
  document.getElementById("metricTrigType").innerText = m.trigger_type || "Warten";
  
  const elNtfy = document.getElementById("metricNtfyTime");
  if (elNtfy) elNtfy.innerText = m.ntfy_alert_time || "Warten";
  
  const elDelay = document.getElementById("metricNtfyDelay");
  if (elDelay) elDelay.innerText = m.ntfy_delay || "-";
}

function renderPlot(d) {
  const traces = [
    { 
      x: d.times, 
      y: d.raw_temps, 
      mode: 'markers+lines', 
      name: 'Beton (Roh)', 
      line: { color: '#ff6600', width: 1.5 }, 
      marker: { size: 3 } 
    },
    { 
      x: d.times, 
      y: d.smooth_temps, 
      mode: 'lines', 
      name: 'Geglättet (Savitzky-Golay)', 
      line: { color: '#38bdf8', width: 2 } 
    }
  ];

  if (d.ambients && d.ambients.length > 0) {
    traces.push({ 
      x: d.times, 
      y: d.ambients, 
      mode: 'lines', 
      name: 'Umgebung', 
      line: { color: '#64748b', dash: 'dot' } 
    });
  }

  if (d.tangent_steigung) {
    traces.push({ 
      x: d.times, 
      y: d.tangent_steigung, 
      mode: 'lines', 
      name: 'Steigungstangente', 
      line: { color: '#22c55e', dash: 'dash', width: 1.5 } 
    });
  }
  
  if (d.tangent_ruhe) {
    traces.push({ 
      x: d.times, 
      y: d.tangent_ruhe, 
      mode: 'lines', 
      name: 'Ruhetangente', 
      line: { color: '#a855f7', dash: 'dash', width: 1.5 } 
    });
  }

  // Wendepunkt-Marker (t_min)
  if (d.turnaround_detected && d.turnaround_time && d.t_min_temp !== null && d.t_min_temp !== undefined) {
    traces.push({
      x: [d.turnaround_time],
      y: [d.t_min_temp],
      mode: 'markers',
      name: `Wendepunkt t_min (${d.t_min_temp.toFixed(2)} °C)`,
      marker: { size: 10, color: '#38bdf8', symbol: 'diamond' }
    });
  }

  // Lookback-Fit-Intervall & Alarmpunkt
  if (d.trigger_fit_segment && d.trigger_fit_segment.times) {
    traces.push({
      x: d.trigger_fit_segment.times,
      y: d.trigger_fit_segment.temps,
      mode: 'lines',
      name: 'Lookback-Fit Fenster (Trigger)',
      line: { color: '#fbbf24', width: 3.5 }
    });

    traces.push({
      x: [d.trigger_fit_segment.trigger_time],
      y: [d.trigger_fit_segment.trigger_temp],
      mode: 'markers',
      name: 'ntfy Alarmpunkt',
      marker: { size: 10, color: '#ef4444', symbol: 'diamond' }
    });
  }

  // Beschleunigungskurve: Skaliert auf µ°C/s²
  if (d.accel_series && d.accel_series.length > 0) {
    const accelMicro = d.accel_series.map(v => v * 1000000.0);
    traces.push({
      x: d.times,
      y: accelMicro,
      yaxis: 'y2',
      mode: 'lines',
      name: 'Rotationsbeschleunigung (d²T/dt²)',
      line: { color: '#f59e0b', width: 1.5, dash: 'dot' },
      visible: true
    });
  }

  // Schwellenwert-Linie
  const rawAccelLimit = parseFloat(document.getElementById("sd_accel_min").value) || 0.000010;
  const accelLimitMicro = rawAccelLimit * 1000000.0;
  traces.push({
    x: [d.times[0], d.times[d.times.length - 1]],
    y: [accelLimitMicro, accelLimitMicro],
    yaxis: 'y2',
    mode: 'lines',
    name: 'Schwelle Beschleunigung (Limit)',
    line: { color: '#ef4444', dash: 'dot', width: 1.5 }
  });

  const shapes = [];
  const gatekeeperEnd = d.turnaround_armed_time || d.turnaround_time;
  if (d.turnaround_detected && gatekeeperEnd) {
    shapes.push({
      type: 'rect',
      xref: 'x',
      yref: 'paper',
      x0: d.times[0],
      x1: gatekeeperEnd,
      y0: 0,
      y1: 1,
      fillcolor: 'rgba(56, 189, 248, 0.08)',
      line: { width: 0 },
      layer: 'below'
    });
    shapes.push({
      type: 'line',
      xref: 'x',
      yref: 'paper',
      x0: gatekeeperEnd,
      x1: gatekeeperEnd,
      y0: 0,
      y1: 1,
      line: { color: '#38bdf8', width: 1.5, dash: 'dashdot' },
      layer: 'below'
    });
  }

  const layout = {
    paper_bgcolor: '#020617',
    plot_bgcolor: '#020617',
    font: { color: '#cbd5e1' },
    margin: { t: 30, r: 60, l: 45, b: 40 },
    shapes: shapes,
    xaxis: { gridcolor: '#1e293b' },
    yaxis: { 
      gridcolor: '#1e293b', 
      title: 'Temperatur (°C)' 
    },
    yaxis2: {
      title: 'Rotationsbeschleunigung (µ°C/s²)',
      titlefont: { color: '#f59e0b' },
      tickfont: { color: '#f59e0b' },
      overlaying: 'y',
      side: 'right',
      showgrid: false,
      zeroline: true,
      zerolinecolor: '#475569',
      zerolinewidth: 1.5,
      range: [-15, 30]
    },
    legend: { orientation: 'h', y: -0.15 }
  };

  Plotly.newPlot('plotContainer', traces, layout, { responsive: true, displaylogo: false });
}

async function saveParamsToYaml(e) {
  if (e) e.preventDefault();

  const devKey = getSelectedDeviceKey();
  if (!devKey) {
    showToast('Bitte wählen Sie zuerst einen Koffer aus!', 'error');
    return;
  }

  const elDt = document.getElementById("pd_delta_t_min");
  const elSl = document.getElementById("pd_slope_min");
  const elRp = document.getElementById("pd_rot_peak_min");

  const payload = {
    device_key: devKey,
    probe_detection: {
      delta_t_min: elDt ? parseFloat(elDt.value) || 0.80 : 0.80,
      slope_min: elSl ? parseFloat(elSl.value) || 0.015 : 0.015,
      rot_peak_min: elRp ? parseFloat(elRp.value) || 0.035 : 0.035
    },
    turnaround_detection: {
      sg_window: parseInt(document.getElementById("td_sg_window").value) || 31,
      min_cooling_delta: parseFloat(document.getElementById("td_min_cooling_delta").value) || 0.20,
      cooling_slope_min: parseFloat(document.getElementById("td_cooling_slope_min").value) || -0.0003,
      reheating_slope_min: parseFloat(document.getElementById("td_reheating_slope_min").value) || 0.0003
    },
    setting_detection: {
      sg_window: 21,
      poly_order: 2,
      lookback_sec: parseInt(document.getElementById("sd_lookback_sec").value) || 120,
      min_samples: 15,
      accel_min: parseFloat(document.getElementById("sd_accel_min").value) || 0.000010,
      slope_min: parseFloat(document.getElementById("sd_slope_min").value) || 0.0002,
      fallback_samples: 5,
      fallback_step_min: 0.020
    }
  };

  try {
    const res = await fetch("/api/simulator/save-device-params", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Parameter in ${devKey}.yaml gespeichert!`, "success");
    } else {
      if (data.auth_required) {
        showToast("Admin-Rechte erforderlich.", "error");
        setTimeout(() => { window.location.href = '/'; }, 1000);
      } else {
        showToast(data.error || "Fehler beim Speichern", "error");
      }
    }
  } catch (err) {
    showToast("Netzwerkfehler beim Speichern", "error");
  }
}

async function generateNightReport() {
  if (!currentData) {
    showToast("Bitte führen Sie zuerst eine Analyse durch!", "error");
    return;
  }
  const devKey = getSelectedDeviceKey();
  try {
    const res = await fetch("/api/simulator/export-plot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        times: currentData.times,
        temps: currentData.raw_temps,
        ambs: currentData.ambients,
        label: devKey
      })
    });
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Report_${devKey}.png`;
    a.click();
    window.URL.revokeObjectURL(url);
    showToast("Report erfolgreich heruntergeladen!", "success");
  } catch (err) {
    showToast("Fehler beim Exportieren des Plots", "error");
  }
}