/**
 * @file: history.js
 * @version: 1.2.0
 * @date: 2026-08-27
 * @description: Interaktive Chart.js-Steuerung mit lesbaren Labels, Event-Overlays, 
 *               vollständiger Checkbox-Synchronisation und CSV-Export.
 * @author: Patrick Staehli
 */

let chartInstance = null;
const PALETTE = [
    "#58a6ff", "#3fb950", "#d29922", "#f85149", 
    "#a371f7", "#388bfd", "#2ea043", "#bb8009", 
    "#db6d28", "#8b949e", "#ff7b72", "#7ee787"
];

function initDates() {
    const now = new Date();
    const past24h = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    document.getElementById("end-time").value = toLocalISO(now);
    document.getElementById("start-time").value = toLocalISO(past24h);
}

function toLocalISO(dt) {
    const pad = n => String(n).padStart(2, '0');
    return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
}

function applyPreset(hours, event) {
    document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
    if (event && event.target) {
        event.target.classList.add('active');
    }
    const now = new Date();
    const past = new Date(now.getTime() - hours * 60 * 60 * 1000);
    document.getElementById("end-time").value = toLocalISO(now);
    document.getElementById("start-time").value = toLocalISO(past);
    loadHistoryData();
}

function toggleBoxChannels(masterCb) {
    const boxId = masterCb.getAttribute("data-box-id");
    document.querySelectorAll(`.ch-cb[data-box-id="${boxId}"]`).forEach(cb => {
        cb.checked = masterCb.checked;
    });
    masterCb.indeterminate = false;
    loadHistoryData();
}

function onChildChannelChange(boxId) {
    const total = document.querySelectorAll(`.ch-cb[data-box-id="${boxId}"]`).length;
    const checked = document.querySelectorAll(`.ch-cb[data-box-id="${boxId}"]:checked`).length;
    const master = document.querySelector(`.box-master-cb[data-box-id="${boxId}"]`);
    if (master) {
        master.checked = checked > 0;
        master.indeterminate = checked > 0 && checked < total;
    }
    loadHistoryData();
}

function setSelectAll(state) {
    document.querySelectorAll('.box-master-cb').forEach(cb => {
        cb.checked = state;
        cb.indeterminate = false;
    });
    document.querySelectorAll('.ch-cb').forEach(cb => {
        cb.checked = state;
    });
    loadHistoryData();
}

function getSelectedFilters() {
    const devices = new Set();
    const channels = new Set();
    document.querySelectorAll(".ch-cb:checked").forEach(cb => {
        devices.add(cb.getAttribute("data-box-id"));
        channels.add(parseInt(cb.getAttribute("data-ch-num")));
    });

    const startVal = document.getElementById("start-time").value;
    const endVal = document.getElementById("end-time").value;

    return {
        device_ids: Array.from(devices),
        channels: Array.from(channels),
        start_time: startVal ? new Date(startVal).toISOString() : null,
        end_time: endVal ? new Date(endVal).toISOString() : null
    };
}

function getChannelLabel(key) {
    // Formatiert 'ccssite01_ch0' zu lesbarem Label aus dem DOM-Baum
    const parts = key.split('_ch');
    if (parts.length === 2) {
        const boxId = parts[0];
        const chNum = parts[1];
        const cb = document.querySelector(`.ch-cb[data-box-id="${boxId}"][data-ch-num="${chNum}"]`);
        if (cb) {
            const row = cb.closest('.tree-row');
            const boxName = row ? row.querySelector('.tree-parent span').innerText.trim() : boxId;
            const chLabel = cb.nextElementSibling ? cb.nextElementSibling.innerText.trim() : `Kanal ${parseInt(chNum) + 1}`;
            return `${boxName} – ${chLabel}`;
        }
    }
    return key;
}

async function loadHistoryData() {
    const payload = getSelectedFilters();
    if (payload.device_ids.length === 0 || payload.channels.length === 0) {
        if (chartInstance) chartInstance.destroy();
        document.getElementById("data-stats").innerText = "Keine Kanäle ausgewählt.";
        return;
    }

    const loader = document.getElementById("chart-loading");
    if (loader) loader.style.display = "block";

    try {
        const res = await fetch("/history/api/data", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        renderChart(data);
        document.getElementById("data-stats").innerText = `${data.series.length} Messzeitpunkte geladen.`;
    } catch (err) {
        alert("Fehler beim Laden der History-Daten: " + err);
    } finally {
        if (loader) loader.style.display = "none";
    }
}

function renderChart(data) {
    const ctx = document.getElementById("historyChart").getContext("2d");
    if (chartInstance) chartInstance.destroy();

    const labels = data.series.map(d => d.time);
    const seriesKeys = new Set();
    data.series.forEach(d => {
        Object.keys(d).forEach(k => { 
            if (k !== "time") seriesKeys.add(k); 
        });
    });

    let colorIdx = 0;
    const datasets = Array.from(seriesKeys).map(key => {
        return {
            label: getChannelLabel(key),
            data: data.series.map(d => d[key] !== undefined ? d[key] : null),
            borderColor: PALETTE[colorIdx++ % PALETTE.length],
            borderWidth: 1.8,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.1,
            spanGaps: true
        };
    });

    chartInstance = new Chart(ctx, {
        type: "line",
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false
            },
            scales: {
                x: { 
                    ticks: { color: "#8b949e", maxTicksLimit: 12 }, 
                    grid: { color: "#30363d" } 
                },
                y: { 
                    title: { display: true, text: "Temperatur (°C)", color: "#8b949e" }, 
                    ticks: { color: "#8b949e" }, 
                    grid: { color: "#30363d" } 
                }
            },
            plugins: {
                legend: { 
                    labels: { color: "#c9d1d9", font: { size: 12 } } 
                },
                zoom: {
                    zoom: { 
                        wheel: { enabled: true }, 
                        pinch: { enabled: true }, 
                        mode: 'x' 
                    },
                    pan: { 
                        enabled: true, 
                        mode: 'x' 
                    }
                }
            }
        }
    });
}

function resetZoom() {
    if (chartInstance) chartInstance.resetZoom();
}

async function exportCSV() {
    const payload = getSelectedFilters();
    if (payload.device_ids.length === 0 || payload.channels.length === 0) {
        alert("Bitte mindestens ein Gerät und einen Kanal auswählen.");
        return;
    }

    try {
        const res = await fetch("/history/api/export-csv", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
        }

        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `Concretum_Export_${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
    } catch (err) {
        alert("Fehler beim CSV-Export: " + err);
    }
}

document.addEventListener("DOMContentLoaded", () => {
    initDates();
    loadHistoryData();
});