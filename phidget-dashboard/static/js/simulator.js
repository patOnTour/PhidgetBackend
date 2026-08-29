/**
 * @file: simulator.js
 * @version: 2.8.0
 * @date: 2026-08-28
 * @description: Interaktive Steuerung und Plotly-Visualisierung mit synchroner Mikro-Skalierung, 
 *               beruhigter Beschleunigungskurve und sichtbarer Gatekeeper-Phasenschattierung.
 * @author: Patrick Stähli
 */

let currentData = null;

function getSelectedDeviceKey() {
  const sel = document.getElementById("target_box") || document.getElementById("selBox");
  if (!sel) return "baustellenkoffer_1";
  const val = sel.value.trim();
  const match = val.match(/\(([^)]+)\)/);
  return match ? match[1] : val;
}

document.addEventListener("DOMContentLoaded", () => {
  const versionEl = document.getElementById("jsVersionDisplay");
  if (versionEl) {
    versionEl.innerText = "JS v2.8.0";
  }

  const selBox = document.getElementById("target_box") || document.getElementById("selBox");
  if (selBox) {
    selBox.addEventListener("change", (e) => loadDeviceParams(e.target.value));
    if (selBox.value) {
      loadDeviceParams(selBox.value);
    }
  }
});

async function loadDeviceParams(devKey) {
  if (!devKey) return;
  try {
    const res = await fetch(`/api/simulator/get-params/${devKey}`);
    const data = await res.json();
    if (data.success) {
      if (data.turnaround_detection) {
        document.getElementById("td_sg_window").value = data.turnaround_detection.sg_window || 31;
        document.getElementById("td_min_cooling_delta").value = data.turnaround_detection.min_cooling_delta || 0.20;
        document.getElementById("td_cooling_slope_min").value = data.turnaround_detection.cooling_slope_min || -0.0003;
        document.getElementById("td_reheating_slope_min").value = data.turnaround_detection.reheating_slope_min || 0.0003;
      }
      if (data.setting_detection) {
        document.getElementById("sd_lookback_sec").value = data.setting_detection.lookback_sec || 120;
        document.getElementById("sd_accel_min").value = data.setting_detection.accel_min || 0.000010;
        document.getElementById("sd_slope_min").value = data.setting_detection.slope_min || 0.0002;
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
      alert(result.error || "Analysefehler");
      return;
    }

    currentData = result.data;
    renderPlot(result.data);
    updateMetrics(result.data.metrics || result.data);
  } catch (err) {
    alert("Netzwerkfehler bei der Analyse: " + err);
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

  // Wendepunkt-Marker (t_min) auf der Temperaturkurve
  if (d.turnaround_detected && d.turnaround_time && d.t_min_temp !== null && d.t_min_temp !== undefined) {
    traces.push({
      x: [d.turnaround_time],
      y: [d.t_min_temp],
      mode: 'markers',
      name: `Wendepunkt t_min (${d.t_min_temp.toFixed(2)} °C)`,
      marker: { size: 10, color: '#38bdf8', symbol: 'diamond' }
    });
  }

  // Lookback-Fit-Intervall & Alarmpunkt am Triggerpunkt
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

  // Beschleunigungskurve: Skaliert auf µ°C/s² (Faktor 1e6)
  if (d.accel_series && d.accel_series.length > 0) {
    const accelMicro = d.accel_series.map(v => v * 1000000.0);
    traces.push({
      x: d.times,
      y: accelMicro,
      yaxis: 'y2',
      mode: 'lines',
      name: 'Rotationsbeschleunigung (d²T/dt²)',
      line: { color: '#f59e0b', width: 1.5, dash: 'dot' },
      visible: true // Vollstaendig sichtbar durch kaskadierte Glättung
    });
  }

  // Schwellenwert-Linie: Fest an Sekundaerachse (y2) gebunden mit Mikro-Skalierung
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

  // Layout Shapes: Gatekeeper-Schattierung (Sperrphase bis zum Scharfschalten / t_min)
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
      range: [-15, 30] // Fester Bereich erzwingt, dass die Schwellenlinie korrekt auf der rechten Skala liegt
    },
    legend: { orientation: 'h', y: -0.15 }
  };

  Plotly.newPlot('plotContainer', traces, layout, { responsive: true, displaylogo: false });
}

async function saveParamsToYaml(e) {
  if (e) e.preventDefault();

  const devKey = getSelectedDeviceKey();
  if (!devKey) {
    alert("❌ Bitte wählen Sie zuerst einen Koffer aus!");
    return;
  }

  const payload = {
    device_key: devKey,
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
      alert(`✅ Parameter erfolgreich in ${devKey}.yaml gespeichert!`);
    } else {
      alert("❌ Fehler beim Speichern: " + (data.error || "Unbekannter Fehler"));
    }
  } catch (err) {
    alert("Netzwerkfehler beim Speichern: " + err);
  }
}

async function generateNightReport() {
  if (!currentData) {
    alert("Bitte führen Sie zuerst eine Analyse durch!");
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
  } catch (err) {
    alert("Fehler beim Exportieren des Plots: " + err);
  }
}