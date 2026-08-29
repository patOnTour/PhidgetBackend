/**
 * @file: main.js
 * @version: 2.0.1
 * @date: 2026-08-29
 * @description: Frontend-Steuerung fuer das Telemetrie-Dashboard mit zuverlaessigem Redirect nach Admin-Login.
 * @author: Patrick Stähli
 */

let activeBoxId = localStorage.getItem('concretum_active_box') || window.INITIAL_BOX_ID || '';
let activeSubTab = "control";
let currentDashboardMode = "tabs";
let liveCacheData = {};
let metaOptionsCache = null;
const channelCounts = window.CHANNEL_COUNTS || {};
const widgetState = {};

// Zustand fuer Zeitkorrektur-Modal
let activeModalContext = {
  boxId: null,
  channel: null,
  eventType: null,
  currentTimestamp: null
};

// ==========================================
// I18N & SPRACHE
// ==========================================

function t(path, fallback = '') {
  if (!window.I18N) return fallback;
  const keys = path.split('.');
  let current = window.I18N;
  for (const k of keys) {
    if (current && typeof current === 'object' && k in current) {
      current = current[k];
    } else {
      return fallback;
    }
  }
  return current || fallback;
}

async function changeLanguage(langCode) {
  try {
    const res = await fetch('/api/set-language', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lang: langCode })
    });
    const data = await res.json();
    if (data.success) {
      location.reload();
    } else {
      showToast(data.error || 'Fehler beim Sprachwechsel', 'error');
    }
  } catch (err) {
    showToast('Netzwerkfehler', 'error');
  }
}

// ==========================================
// HAMBURGER-MENÜ & ADMIN-AUTH
// ==========================================

function toggleHamburgerMenu() {
  const menu = document.getElementById('hamburger-menu');
  if (menu) {
    menu.classList.toggle('hidden');
  }
}

function toggleVisibilityMenu() {
  const menu = document.getElementById('visibility-dropdown');
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

  const visBtn = document.querySelector('button[onclick="toggleVisibilityMenu()"]');
  const visMenu = document.getElementById('visibility-dropdown');
  if (visBtn && visMenu && !visBtn.contains(e.target) && !visMenu.contains(e.target)) {
    visMenu.classList.add('hidden');
  }
});

let pendingRedirectUrl = null;

function openProtectedPage(targetUrl) {
  if (window.IS_ADMIN) {
    window.location.href = targetUrl;
    return;
  }
  pendingRedirectUrl = targetUrl;
  const modal = document.getElementById('modal-admin-auth');
  const input = document.getElementById('admin-password-input');
  const err = document.getElementById('admin-auth-error');
  if (err) err.classList.add('hidden');
  if (input) input.value = '';
  if (modal) modal.classList.remove('hidden');
  if (input) input.focus();
}

function closeAdminAuthModal() {
  const modal = document.getElementById('modal-admin-auth');
  if (modal) modal.classList.add('hidden');
  pendingRedirectUrl = null;
}

async function submitAdminLogin(event) {
  event.preventDefault();
  const input = document.getElementById('admin-password-input');
  const err = document.getElementById('admin-auth-error');
  const pwd = input ? input.value : '';
  const targetUrl = pendingRedirectUrl; // Ziel-URL vor dem Schliessen des Modals sichern

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd })
    });
    const data = await res.json();
    if (data.success) {
      window.IS_ADMIN = true;
      const logoutBtn = document.getElementById('btn-admin-logout');
      if (logoutBtn) logoutBtn.classList.remove('hidden');
      closeAdminAuthModal();
      
      if (targetUrl) {
        window.location.href = targetUrl;
      } else {
        showToast('Erfolgreich als Admin angemeldet', 'success');
      }
    } else {
      if (err) {
        err.innerText = data.error || 'Ungültiges Passwort';
        err.classList.remove('hidden');
      }
    }
  } catch (e) {
    if (err) {
      err.innerText = 'Netzwerkfehler';
      err.classList.remove('hidden');
    }
  }
}

async function performAdminLogout() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.IS_ADMIN = false;
    const logoutBtn = document.getElementById('btn-admin-logout');
    if (logoutBtn) logoutBtn.classList.add('hidden');
    showToast('Admin-Sitzung beendet', 'success');
  } catch (e) {
    showToast('Fehler beim Abmelden', 'error');
  }
}

// ==========================================
// KOFFER-SICHTBARKEIT (LOCALSTORAGE)
// ==========================================

function getHiddenBoxes() {
  try {
    return JSON.parse(localStorage.getItem('concretum_hidden_boxes') || '[]');
  } catch (e) {
    return [];
  }
}

function saveHiddenBoxes(list) {
  localStorage.setItem('concretum_hidden_boxes', JSON.stringify(list));
}

function applySavedBoxVisibility() {
  const hiddenBoxes = getHiddenBoxes();

  document.querySelectorAll('.box-visibility-cb').forEach(cb => {
    cb.checked = !hiddenBoxes.includes(cb.value);
  });

  hiddenBoxes.forEach(boxId => {
    const tabBtn = document.getElementById(`tab-btn-${boxId}`);
    const tabContent = document.getElementById(`tab-content-${boxId}`);
    const gridCard = document.getElementById(`grid-card-${boxId}`);
    if (tabBtn) tabBtn.classList.add('hidden');
    if (tabContent) tabContent.classList.add('hidden');
    if (gridCard) gridCard.classList.add('hidden');
  });

  if (hiddenBoxes.includes(activeBoxId)) {
    const firstVisibleBtn = document.querySelector('.tab-btn:not(.hidden)');
    if (firstVisibleBtn) {
      const fallbackId = firstVisibleBtn.id.replace('tab-btn-', '');
      switchTab(fallbackId);
    }
  }
}

function handleBoxVisibilityChange(boxId, isVisible) {
  let hidden = getHiddenBoxes();
  if (!isVisible) {
    if (!hidden.includes(boxId)) hidden.push(boxId);
  } else {
    hidden = hidden.filter(id => id !== boxId);
  }
  saveHiddenBoxes(hidden);
  applySavedBoxVisibility();
}

function resetBoxVisibility() {
  localStorage.removeItem('concretum_hidden_boxes');
  document.querySelectorAll('.tab-btn, .tab-content, [id^="grid-card-"]').forEach(el => {
    el.classList.remove('hidden');
  });
  document.querySelectorAll('.box-visibility-cb').forEach(cb => {
    cb.checked = true;
  });
  if (activeBoxId) switchTab(activeBoxId);
  showToast('Alle Koffer eingeblendet', 'success');
}

// ==========================================
// ALARM FORMATIERUNG & FILTERUNG
// ==========================================

function formatAlertText(boxName, boxId, title, message) {
  const rawMsg = message || '';
  const fullText = `${title || ''} ${rawMsg}`.toLowerCase();

  if (fullText.includes('offline') || fullText.includes('keine messwerte') || fullText.includes('has not sent telemetry') || fullText.includes('no envía')) {
    const tmplTitle = t('alarms.events.offline_title', 'OFFLINE-ALARM');
    let bName = boxName;
    let bId = boxId;

    if (!bId) {
      const matchId = rawMsg.match(/\((ccssite\d+|[^)]+)\)/i);
      if (matchId) bId = matchId[1];
    }
    if (!bName) {
      const matchName = rawMsg.match(/achtung:\s*([^(\s]+)|warning:\s*([^(\s]+)|atención:\s*([^(\s]+)/i);
      if (matchName) bName = matchName[1] || matchName[2] || matchName[3];
    }

    const tmplMsg = t('alarms.events.offline_msg', 'Achtung: {name} ({id}) liefert seit >60s keine Messwerte mehr an den Server, obwohl eine Messung läuft!')
      .replace('{name}', bName || bId || 'Box')
      .replace('{id}', bId || 'ccssite');
    return { title: `⚠️ ${tmplTitle}`, message: tmplMsg };
  }

  if (fullText.includes('einstich') || fullText.includes('probe insertion') || fullText.includes('inserción')) {
    const chMatch = rawMsg.match(/kanal\s*(\d+)|channel\s*(\d+)|canal\s*(\d+)/i);
    const chNum = chMatch ? (chMatch[1] || chMatch[2] || chMatch[3]) : '1';
    const tmplTitle = t('alarms.events.probe_detected_title', 'EINSTICH ERKANNT');
    const tmplMsg = t('alarms.events.probe_detected_msg', 'Einstich an {name} ({id}) auf Kanal {ch} registriert. Messung gestartet.')
      .replace('{name}', boxName || boxId || 'Box')
      .replace('{id}', boxId || '')
      .replace('{ch}', chNum);
    return { title: tmplTitle, message: tmplMsg };
  }

  if (fullText.includes('wendepunkt') || fullText.includes('turnaround') || fullText.includes('inflexión')) {
    const chMatch = rawMsg.match(/kanal\s*(\d+)|channel\s*(\d+)|canal\s*(\d+)/i);
    const chNum = chMatch ? (chMatch[1] || chMatch[2] || chMatch[3]) : '1';
    const tmplTitle = t('alarms.events.turnaround_title', 'WENDEPUNKT ERREICHT');
    const tmplMsg = t('alarms.events.turnaround_msg', 'Wendepunkt an {name} ({id}) auf Kanal {ch} erkannt.')
      .replace('{name}', boxName || boxId || 'Box')
      .replace('{id}', boxId || '')
      .replace('{ch}', chNum);
    return { title: tmplTitle, message: tmplMsg };
  }

  if (fullText.includes('trigger') || fullText.includes('disparo') || fullText.includes('abbindebeginn')) {
    const chMatch = rawMsg.match(/kanal\s*(\d+)|channel\s*(\d+)|canal\s*(\d+)/i);
    const chNum = chMatch ? (chMatch[1] || chMatch[2] || chMatch[3]) : '1';
    const tempMatch = rawMsg.match(/([\d.]+)\s*°?c/i);
    const tempVal = tempMatch ? tempMatch[1] : '--';
    const tmplTitle = t('alarms.events.trigger_title', 'ABB кар-BEGINN ERREICHT');
    const tmplMsg = t('alarms.events.trigger_msg', 'Abbindebeginn an {name} ({id}) auf Kanal {ch} registriert ({temp}°C).')
      .replace('{name}', boxName || boxId || 'Box')
      .replace('{id}', boxId || '')
      .replace('{ch}', chNum)
      .replace('{temp}', tempVal);
    return { title: tmplTitle, message: tmplMsg };
  }

  if (fullText.includes('export') || fullText.includes('abgeschlossen') || fullText.includes('completed') || fullText.includes('completada')) {
    const chMatch = rawMsg.match(/kanal\s*(\d+)|channel\s*(\d+)|canal\s*(\d+)/i);
    const chNum = chMatch ? (chMatch[1] || chMatch[2] || chMatch[3]) : '1';
    const tmplTitle = t('alarms.events.export_ready_title', 'EXPORT BEREIT');
    const tmplMsg = t('alarms.events.export_ready_msg', 'Messung an {name} ({id}) auf Kanal {ch} abgeschlossen. Export-Dateien erstellt.')
      .replace('{name}', boxName || boxId || 'Box')
      .replace('{id}', boxId || '')
      .replace('{ch}', chNum);
    return { title: tmplTitle, message: tmplMsg };
  }

  return { title: title || '', message: rawMsg };
}

// ==========================================
// VIEW MODES & TABS
// ==========================================

function setDashboardViewMode(mode) {
  currentDashboardMode = mode;
  const containerTabs = document.getElementById('container-view-tabs');
  const containerGrid = document.getElementById('container-view-grid');
  const btnTabs = document.getElementById('btn-mode-tabs');
  const btnGrid = document.getElementById('btn-mode-grid');

  if (mode === 'grid') {
    if (containerTabs) containerTabs.classList.add('hidden');
    if (containerGrid) containerGrid.classList.remove('hidden');
    if (btnGrid) btnGrid.className = 'px-2.5 py-1 rounded bg-slate-800 text-white font-semibold transition';
    if (btnTabs) btnTabs.className = 'px-2.5 py-1 rounded text-slate-400 hover:text-white transition';
  } else {
    if (containerGrid) containerGrid.classList.add('hidden');
    if (containerTabs) containerTabs.classList.remove('hidden');
    if (btnTabs) btnTabs.className = 'px-2.5 py-1 rounded bg-slate-800 text-white font-semibold transition';
    if (btnGrid) btnGrid.className = 'px-2.5 py-1 rounded text-slate-400 hover:text-white transition';
    if (activeBoxId) switchTab(activeBoxId);
  }
}

function openBoxTab(boxId) {
  setDashboardViewMode('tabs');
  switchTab(boxId);
}

function switchTab(boxId) {
  if (!boxId) return;
  
  window.ACTIVE_BOX_ID = boxId;
  activeBoxId = boxId;
  localStorage.setItem('concretum_active_box', boxId);

  document.querySelectorAll('.tab-content').forEach(el => {
    el.classList.add('hidden');
  });

  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.remove('bg-slate-800', 'text-white');
    btn.classList.add('bg-slate-900', 'text-slate-400');
  });

  const targetContent = document.getElementById(`tab-content-${boxId}`);
  const targetBtn = document.getElementById(`tab-btn-${boxId}`);

  if (targetContent) targetContent.classList.remove('hidden');
  if (targetBtn) {
    targetBtn.classList.add('bg-slate-800', 'text-white');
    targetBtn.classList.remove('bg-slate-900', 'text-slate-400');
  }

  if (typeof loadArchiveFiles === 'function') {
    loadArchiveFiles(boxId);
  }
  if (activeSubTab === 'quick_table') {
    loadWidgetData(boxId);
  } else if (activeSubTab === 'meta') {
    loadChannelMetadata(boxId);
  }
}

function switchSubTab(boxId, tabKey) {
  activeSubTab = tabKey;
  ['control', 'quick_table', 'meta'].forEach(k => {
    const content = document.getElementById(`subtab-content-${boxId}-${k}`);
    const btn = document.getElementById(`subtab-btn-${boxId}-${k}`);
    if (content) content.classList.add('hidden');
    if (btn) {
      btn.classList.remove('bg-slate-800', 'text-white', 'border-slate-700');
      btn.classList.add('bg-slate-900', 'text-slate-400', 'border-slate-800');
    }
  });

  const activeContent = document.getElementById(`subtab-content-${boxId}-${tabKey}`);
  const activeBtn = document.getElementById(`subtab-btn-${boxId}-${tabKey}`);
  if (activeContent) activeContent.classList.remove('hidden');
  if (activeBtn) {
    activeBtn.classList.remove('bg-slate-900', 'text-slate-400', 'border-slate-800');
    activeBtn.classList.add('bg-slate-800', 'text-white', 'border-slate-700');
  }

  if (tabKey === 'quick_table') {
    loadWidgetData(boxId);
  } else if (tabKey === 'meta') {
    loadChannelMetadata(boxId);
  }
}

// ==========================================
// INTERAKTIVE PHASEN- & ZEITSTEUERUNG (DEV-28)
// ==========================================

async function triggerSettingNow(boxId, chNum, btn) {
  const orig = btn.innerText;
  btn.innerText = "⏳";
  btn.disabled = true;

  try {
    const res = await fetch('/api/channel/trigger-setting-now', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ box_id: boxId, channel: chNum })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Abbindebeginn manuell gesetzt!', 'success');
      fetchData();
    } else {
      showToast(data.error || 'Fehler beim Setzen des Abbindebeginns', 'error');
    }
  } catch (e) {
    showToast('Netzwerkfehler', 'error');
  } finally {
    btn.innerText = orig;
    btn.disabled = false;
  }
}

function openTimeAdjustModal(boxId, chNum, eventType, chLabel) {
  activeModalContext = {
    boxId: boxId,
    channel: chNum,
    eventType: eventType,
    currentTimestamp: new Date()
  };

  const titleMap = {
    'start': 'Startzeitpunkt anpassen',
    'turnaround': 'Wendepunkt (Min) anpassen',
    'trigger': 'Abbindebeginn (Setting) anpassen'
  };

  const modal = document.getElementById('modal-time-adjust');
  const elTitle = document.getElementById('modal-adjust-title');
  const elSubtitle = document.getElementById('modal-adjust-subtitle');
  const input = document.getElementById('modal-adjust-input');

  if (elTitle) elTitle.innerText = titleMap[eventType] || 'Zeit anpassen';
  if (elSubtitle) elSubtitle.innerText = `${boxId} &bull; Kanal ${chNum + 1} (${chLabel || 'temp' + chNum})`;

  // Aktuellen Phasen-Zeitstempel aus dem Live-Cache laden
  let initDate = new Date();
  if (liveCacheData && liveCacheData.boxes && liveCacheData.boxes[boxId]) {
    const phases = liveCacheData.boxes[boxId].channel_phases;
    const chKey = `temp${chNum}`;
    if (phases && phases[chKey] && phases[chKey][eventType]) {
      initDate = new Date(phases[chKey][eventType]);
    }
  }

  activeModalContext.currentTimestamp = initDate;
  if (input) {
    input.value = formatDateForInput(initDate);
  }

  if (modal) modal.classList.remove('hidden');
}

function closeTimeAdjustModal() {
  const modal = document.getElementById('modal-time-adjust');
  if (modal) modal.classList.add('hidden');
}

function formatDateForInput(date) {
  const pad = (n) => String(n).padStart(2, '0');
  const yr = date.getFullYear();
  const mo = pad(date.getMonth() + 1);
  const da = pad(date.getDate());
  const ho = pad(date.getHours());
  const mi = pad(date.getMinutes());
  const se = pad(date.getSeconds());
  return `${yr}-${mo}-${da}T${ho}:${mi}:${se}`;
}

function adjustModalTimeDelta(seconds) {
  const input = document.getElementById('modal-adjust-input');
  if (!input || !input.value) return;

  const cur = new Date(input.value);
  if (isNaN(cur.getTime())) return;

  cur.setSeconds(cur.getSeconds() + seconds);
  activeModalContext.currentTimestamp = cur;
  input.value = formatDateForInput(cur);
}

function setModalTimeToNow() {
  const input = document.getElementById('modal-adjust-input');
  const now = new Date();
  activeModalContext.currentTimestamp = now;
  if (input) input.value = formatDateForInput(now);
}

async function saveAdjustedTime() {
  const input = document.getElementById('modal-adjust-input');
  if (!input || !input.value) return;

  const targetDate = new Date(input.value);
  if (isNaN(targetDate.getTime())) {
    showToast('Ungültiges Datumsformat', 'error');
    return;
  }

  try {
    const res = await fetch('/api/channel/override-timestamp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        box_id: activeModalContext.boxId,
        channel: activeModalContext.channel,
        event_type: activeModalContext.eventType,
        new_timestamp: targetDate.toISOString()
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Zeitpunkt erfolgreich korrigiert!', 'success');
      closeTimeAdjustModal();
      fetchData();
    } else {
      showToast(data.error || 'Fehler beim Speichern', 'error');
    }
  } catch (e) {
    showToast('Netzwerkfehler', 'error');
  }
}

// ==========================================
// WIDGET TELEMETRIE & CHART
// ==========================================

function initWidgetState(boxId) {
  if (!widgetState[boxId]) {
    const count = parseInt(channelCounts[boxId] || 4);
    const initialChannels = [];
    for (let i = 0; i < count; i++) {
      initialChannels.push(i);
    }
    initialChannels.push(100);

    widgetState[boxId] = {
      range: 15,
      view: 'chart',
      selectedChannels: new Set(initialChannels),
      chart: null
    };
  }
}

function setWidgetView(boxId, viewType) {
  initWidgetState(boxId);
  widgetState[boxId].view = viewType;

  const btnChart = document.getElementById(`btn-view-chart-${boxId}`);
  const btnTable = document.getElementById(`btn-view-table-${boxId}`);
  const vChart = document.getElementById(`widget-view-chart-${boxId}`);
  const vTable = document.getElementById(`widget-view-table-${boxId}`);

  if (viewType === 'chart') {
    if (vChart) vChart.classList.remove('hidden');
    if (vTable) vTable.classList.add('hidden');
    if (btnChart) btnChart.className = 'px-3 py-1 rounded bg-sky-950 text-sky-300 font-bold border border-sky-800 flex items-center gap-1.5';
    if (btnTable) btnTable.className = 'px-3 py-1 rounded text-slate-400 hover:text-white flex items-center gap-1.5';
  } else {
    if (vChart) vChart.classList.add('hidden');
    if (vTable) vTable.classList.remove('hidden');
    if (btnTable) btnTable.className = 'px-3 py-1 rounded bg-sky-950 text-sky-300 font-bold border border-sky-800 flex items-center gap-1.5';
    if (btnChart) btnChart.className = 'px-3 py-1 rounded text-slate-400 hover:text-white flex items-center gap-1.5';
  }
  loadWidgetData(boxId);
}

function setWidgetRange(boxId, min) {
  initWidgetState(boxId);
  widgetState[boxId].range = parseInt(min);

  document.querySelectorAll(`.range-btn-${boxId}`).forEach(btn => {
    if (parseInt(btn.getAttribute('data-min')) === parseInt(min)) {
      btn.className = `range-btn-${boxId} px-2.5 py-1 rounded bg-slate-800 text-white font-semibold`;
    } else {
      btn.className = `range-btn-${boxId} px-2.5 py-1 rounded text-slate-400 hover:text-white`;
    }
  });
  loadWidgetData(boxId);
}

function toggleWidgetChannel(boxId, ch) {
  initWidgetState(boxId);
  const chNum = parseInt(ch);
  const tagBtn = document.getElementById(`tag-${boxId}-${ch}`);

  if (widgetState[boxId].selectedChannels.has(chNum)) {
    widgetState[boxId].selectedChannels.delete(chNum);
    if (tagBtn) tagBtn.classList.add('opacity-30');
  } else {
    widgetState[boxId].selectedChannels.add(chNum);
    if (tagBtn) tagBtn.classList.remove('opacity-30');
  }
  loadWidgetData(boxId);
}

const PALETTE = ['#10b981', '#38bdf8', '#f59e0b', '#ec4899', '#a855f7', '#6366f1', '#14b8a6', '#f43f5e'];

function getChannelLabel(boxId, ch) {
  if (ch === 100) return t('widget.ambient_label', 'Umgebung');
  const prefix = t('widget.channel_prefix', 'Kanal');
  const labelInput = document.getElementById(`label-input-${boxId}-temp${ch}`);
  if (labelInput && labelInput.value.trim()) {
    return `${labelInput.value.trim()} (${prefix} ${ch + 1})`;
  }
  return `${prefix} ${ch + 1}`;
}

async function loadWidgetData(boxId) {
  initWidgetState(boxId);
  const state = widgetState[boxId];
  try {
    const res = await fetch(`/api/widget-data/${boxId}?minutes=${state.range}`);
    const resData = await res.json();
    const rows = resData.series || [];

    const sortedChannels = Array.from(state.selectedChannels).sort((a, b) => {
      if (a === 100) return 1;
      if (b === 100) return -1;
      return a - b;
    });

    if (state.view === 'table') {
      const thead = document.getElementById(`widget-table-head-${boxId}`);
      const tbody = document.getElementById(`widget-table-body-${boxId}`);

      if (thead) {
        let thHtml = `<th class="py-2 px-2">${t('widget.time_col', 'Uhrzeit')}</th>`;
        sortedChannels.forEach(ch => {
          thHtml += `<th class="py-2 px-2 whitespace-nowrap">${getChannelLabel(boxId, ch)}</th>`;
        });
        thead.innerHTML = thHtml;
      }

      if (tbody) {
        if (rows.length === 0) {
          tbody.innerHTML = `<tr><td class="py-4 text-center text-slate-500 italic">${t('widget.no_data', 'Keine Daten im gewählten Zeitraum')}</td></tr>`;
        } else {
          tbody.innerHTML = rows.slice().reverse().map(r => {
            let cols = `<td class="py-1.5 px-2 text-slate-400 whitespace-nowrap">${r.time}</td>`;
            sortedChannels.forEach(ch => {
              const val = r[`ch_${ch}`];
              cols += `<td class="py-1.5 px-2 font-mono">${val != null ? Number(val).toFixed(1) : '--'}</td>`;
            });
            return `<tr class="border-b border-slate-800/40">${cols}</tr>`;
          }).join('');
        }
      }
    }

    if (state.view === 'chart') {
      const labels = rows.map(r => r.time);
      const datasets = [];

      sortedChannels.forEach((ch) => {
        const label = getChannelLabel(boxId, ch);
        datasets.push({
          label: label,
          data: rows.map(r => r[`ch_${ch}`] ?? null),
          borderColor: ch === 100 ? '#0284c7' : PALETTE[ch % PALETTE.length],
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: rows.length > 60 ? 0 : 2,
          tension: 0.2
        });
      });

      const canvas = document.getElementById(`canvas-widget-${boxId}`);
      if (canvas) {
        const ctx = canvas.getContext('2d');
        if (state.chart) {
          state.chart.data.labels = labels;
          state.chart.data.datasets = datasets;
          state.chart.update('none');
        } else {
          state.chart = new Chart(ctx, {
            type: 'line',
            data: { labels, datasets },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              animation: false,
              scales: {
                x: { ticks: { color: '#64748b', maxTicksLimit: 10 }, grid: { color: '#1e293b' } },
                y: { 
                  ticks: { 
                    color: '#64748b',
                    callback: function(value) {
                      return Number(value).toFixed(2);
                    }
                  }, 
                  grid: { color: '#1e293b' } 
                }
              },
              plugins: {
                tooltip: {
                  callbacks: {
                    label: function(context) {
                      const val = context.parsed.y;
                      return `${context.dataset.label}: ${val != null ? Number(val).toFixed(2) + ' °C' : '--'}`;
                    }
                  }
                },
                legend: {
                  display: true,
                  position: 'top',
                  align: 'start',
                  labels: {
                    color: '#cbd5e1',
                    font: { size: 11, family: 'monospace' },
                    boxWidth: 12,
                    boxHeight: 12,
                    padding: 8
                  }
                }
              }
            }
          });
        }
      }
    }
  } catch (err) {
    console.error("Widget Data Load Error", err);
  }
}

// ==========================================
// KANAL-METADATEN (LABOR & FILEMAKER)
// ==========================================

async function loadMetaOptions() {
  if (metaOptionsCache) return metaOptionsCache;
  try {
    const res = await fetch('/api/metadata/options');
    metaOptionsCache = await res.json();
    return metaOptionsCache;
  } catch (e) {
    console.error("Meta Options Error", e);
    return null;
  }
}

async function updateRecipeDropdown(boxId, chNum) {
  const opts = await loadMetaOptions();
  if (!opts) return;

  const locEl = document.getElementById(`meta-loc-${boxId}-${chNum}`);
  const ifaceEl = document.getElementById(`meta-iface-${boxId}-${chNum}`);
  const recipeSelect = document.getElementById(`meta-recipe-${boxId}-${chNum}`);
  
  if (!locEl || !ifaceEl || !recipeSelect) return;

  if (recipeSelect.tagName.toLowerCase() === 'input') {
    return; // Rezept-ID als Text-Eingabefeld (DEV-31 Vorbereitung)
  }

  const loc = locEl.value;
  const iface = ifaceEl.value;
  const key = `${loc}_${iface}`;
  const recipes = (opts.recipes && opts.recipes[key]) ? opts.recipes[key] : [];

  recipeSelect.innerHTML = recipes.length 
    ? recipes.map(r => `<option value="${r}">${r}</option>`).join('') 
    : `<option value="">-</option>`;
}

async function updateCementIdDropdown(boxId, chNum) {
  const opts = await loadMetaOptions();
  if (!opts) return;

  const cnameEl = document.getElementById(`meta-cname-${boxId}-${chNum}`);
  const cidSelect = document.getElementById(`meta-cid-${boxId}-${chNum}`);
  
  if (!cnameEl || !cidSelect) return;

  const cname = cnameEl.value;
  const ids = (opts.cement_ids && opts.cement_ids[cname]) ? opts.cement_ids[cname] : [];
  
  cidSelect.innerHTML = ids.length 
    ? ids.map(id => `<option value="${id}">${id}</option>`).join('') 
    : `<option value="">-</option>`;
}

async function loadChannelMetadata(boxId) {
  await loadMetaOptions();
  const chCount = channelCounts[boxId] || 4;

  for (let ch = 0; ch < chCount; ch++) {
    await updateRecipeDropdown(boxId, ch);
    await updateCementIdDropdown(boxId, ch);
  }

  try {
    const res = await fetch(`/api/metadata/get/${boxId}`);
    const data = await res.json();
    if (data.success && data.metadata) {
      for (const [ch, m] of Object.entries(data.metadata)) {
        const elLoc = document.getElementById(`meta-loc-${boxId}-${ch}`);
        const elIface = document.getElementById(`meta-iface-${boxId}-${ch}`);
        const elCustom = document.getElementById(`meta-custom-${boxId}-${ch}`);
        const elRecipe = document.getElementById(`meta-recipe-${boxId}-${ch}`);
        const elCname = document.getElementById(`meta-cname-${boxId}-${ch}`);
        const elCid = document.getElementById(`meta-cid-${boxId}-${ch}`);

        if (elLoc && m.location) elLoc.value = m.location;
        if (elIface && m.interface) elIface.value = m.interface;
        if (elCustom) elCustom.value = m.custom_string || '';
        
        await updateRecipeDropdown(boxId, parseInt(ch));
        if (elRecipe && m.recipe_id) elRecipe.value = m.recipe_id;

        if (elCname && m.cement_name) elCname.value = m.cement_name;
        await updateCementIdDropdown(boxId, parseInt(ch));
        if (elCid && m.cement_id) elCid.value = m.cement_id;
      }
    }
  } catch (e) {
    console.error("Load Meta Error", e);
  }
}

async function saveChannelMeta(boxId, chNum, btn) {
  const orig = btn.innerText;
  btn.innerText = "⏳";
  btn.disabled = true;

  const payload = {
    box_id: boxId,
    channel: chNum,
    location: document.getElementById(`meta-loc-${boxId}-${chNum}`).value,
    interface: document.getElementById(`meta-iface-${boxId}-${chNum}`).value,
    custom_string: document.getElementById(`meta-custom-${boxId}-${chNum}`).value.trim(),
    recipe_id: document.getElementById(`meta-recipe-${boxId}-${chNum}`).value.trim(),
    cement_name: document.getElementById(`meta-cname-${boxId}-${chNum}`).value,
    cement_id: document.getElementById(`meta-cid-${boxId}-${chNum}`).value
  };

  try {
    const res = await fetch('/api/metadata/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    showToast(data.success ? 'Kanal-Infos gespeichert!' : 'Fehler beim Speichern', data.success ? 'success' : 'error');
  } catch (e) {
    showToast('Netzwerkfehler', 'error');
  } finally {
    btn.innerText = orig;
    btn.disabled = false;
  }
}

// ==========================================
// EXPORTE & ARCHIV
// ==========================================

async function loadArchiveFiles(boxId) {
  const fileBox = document.getElementById(`files-${boxId}`);
  if (!fileBox) return;
  try {
    const res = await fetch(`/api/archive-files/${boxId}`);
    const data = await res.json();
    if (data.files && data.files.length > 0) {
      fileBox.innerHTML = data.files.map(pair => `
        <div class="grid grid-cols-2 gap-2 bg-slate-950 border border-slate-800/80 rounded-lg p-1.5 items-center font-mono">
          ${pair.csv ? `
            <a href="${pair.csv}" download class="flex items-center justify-center gap-1.5 bg-slate-900 hover:bg-slate-800 text-sky-400 py-1.5 px-2 rounded text-[11px] font-semibold border border-slate-800 transition truncate">
              📄 CSV (${pair.name})
            </a>
          ` : `<span class="text-slate-600 text-center text-[11px] py-1.5">${t('box.no_csv', 'Kein CSV')}</span>`}
          
          ${pair.png ? `
            <button type="button" onclick="openImageModal('${pair.png}')" class="flex items-center justify-center gap-1.5 bg-slate-900 hover:bg-slate-800 text-emerald-400 py-1.5 px-2 rounded text-[11px] font-semibold border border-slate-800 transition truncate w-full">
              ${t('box.btn_graph', '📈 Graph')}
            </button>
          ` : `<span class="text-slate-600 text-center text-[11px] py-1.5">${t('box.no_png', 'Kein PNG')}</span>`}
        </div>
      `).join('');
    } else {
      fileBox.innerHTML = `<span class="text-slate-500 italic block py-2">${t('box.no_exports', 'Keine Exporte vorhanden')}</span>`;
    }
  } catch (e) {
    console.error("Archive Files Error", e);
  }
}

// ==========================================
// HARDWARE TOGGLES & COMMANDS
// ==========================================

async function toggleNtfy(boxId, isEnabled) {
  try {
    const res = await fetch('/api/toggle-ntfy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ box_id: boxId, enabled: isEnabled })
    });
    const data = await res.json();
    const msg = isEnabled ? t('toasts.ntfy_on', 'Ntfy-Alarme AKTIVIERT') : t('toasts.ntfy_off', 'Ntfy-Alarme STUMMGESCHALTET');
    showToast(data.success ? msg : t('toasts.error', 'Fehler'), data.success ? 'success' : 'error');
  } catch (e) {
    showToast(t('toasts.network_error', 'Netzwerkfehler'), 'error');
  }
}

async function toggleAutoDetect(boxId, isEnabled) {
  try {
    const res = await fetch('/api/toggle-autodetect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ box_id: boxId, enabled: isEnabled })
    });
    const data = await res.json();
    const msg = isEnabled ? t('toasts.auto_probe_on', 'Auto-Einstich AKTIVIERT') : t('toasts.auto_probe_off', 'Auto-Einstich DEAKTIVIERT');
    showToast(data.success ? msg : t('toasts.error', 'Fehler'), data.success ? 'success' : 'error');
  } catch (e) {
    showToast(t('toasts.network_error', 'Netzwerkfehler'), 'error');
  }
}

async function toggleTurnaroundDetect(boxId, isEnabled) {
  try {
    const res = await fetch('/api/toggle-turnaround-detect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ box_id: boxId, enabled: isEnabled })
    });
    const data = await res.json();
    const msg = isEnabled ? t('toasts.turnaround_on', 'Wendepunkt AKTIVIERT') : t('toasts.turnaround_off', 'Wendepunkt DEAKTIVIERT');
    showToast(data.success ? msg : t('toasts.error', 'Fehler'), data.success ? 'success' : 'error');
  } catch (e) {
    showToast(t('toasts.network_error', 'Netzwerkfehler'), 'error');
  }
}

async function toggleChannelRecording(boxId, isEnabled) {
  try {
    const res = await fetch('/api/toggle-channel-recording', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ box_id: boxId, enabled: isEnabled })
    });
    const data = await res.json();
    const msg = isEnabled ? t('toasts.logging_on', 'Kanal-Logging AKTIVIERT') : t('toasts.logging_off', 'Kanal-Logging PAUSIERT');
    showToast(data.success ? msg : t('toasts.error', 'Fehler'), data.success ? 'success' : 'error');
  } catch (e) {
    showToast(t('toasts.network_error', 'Netzwerkfehler'), 'error');
  }
}

function openImageModal(imgUrl) {
  const modal = document.getElementById('image-modal');
  const img = document.getElementById('modal-img');
  img.src = imgUrl;
  modal.classList.remove('hidden');
}

function closeImageModal() {
  const modal = document.getElementById('image-modal');
  modal.classList.add('hidden');
  document.getElementById('modal-img').src = '';
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeImageModal();
    closeTimeAdjustModal();
    closeAdminAuthModal();
  }
});

async function saveAllChannelLabels(boxId, channelCount) {
  const labels = {};
  for (let i = 0; i < channelCount; i++) {
    const input = document.getElementById(`label-input-${boxId}-temp${i}`);
    if (input) {
      labels[`temp${i}`] = input.value.trim();
    }
  }

  try {
    const res = await fetch('/api/update-channel-labels-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ box_id: boxId, labels: labels })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Kanalnamen erfolgreich gespeichert!', 'success');
    } else {
      showToast(data.error || 'Fehler beim Speichern', 'error');
    }
  } catch (e) {
    showToast('Netzwerkfehler', 'error');
  }
}

async function startMeasurement(boxId, chId, btn) {
  const swLog = document.getElementById(`switch-logging-${boxId}`);
  if (swLog) swLog.checked = true;
  const swAuto = document.getElementById(`switch-autodetect-${boxId}`);
  if (swAuto) swAuto.checked = true;
  const swTurn = document.getElementById(`switch-turnaround-${boxId}`);
  if (swTurn) swTurn.checked = true;

  const labelInput = document.getElementById(`label-input-${boxId}-${chId}`);
  const labelVal = labelInput ? labelInput.value.trim() : '';
  await sendCmd(boxId, `run:${chId}`, btn, labelVal);
}

async function sendCmd(boxId, cmd, btn, label) {
  const orig = btn.innerText;
  btn.innerText = "⏳";
  btn.disabled = true;
  try {
    const res = await fetch('/api/send-cmd', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ box_id: boxId, cmd: cmd, label: label })
    });
    const data = await res.json();
    showToast(data.success ? `${t('toasts.executed', 'Ausgeführt: ')}${cmd}` : t('toasts.error', 'Fehler'), data.success ? 'success' : 'error');
    fetchData();
  } catch (e) {
    showToast(t('toasts.network_error', 'Netzwerkfehler'), 'error');
  } finally {
    btn.innerText = orig;
    btn.disabled = false;
  }
}

async function clearTriggers() {
  try {
    const res = await fetch('/api/clear-triggers', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      fetchData();
      showToast(t('toasts.alarms_ack', 'Alarme quittiert'), 'success');
    }
  } catch (e) {
    showToast(t('toasts.alarms_ack_error', 'Fehler beim Quittieren'), 'error');
  }
}

function showToast(msg, type) {
  const toast = document.getElementById('toast');
  toast.innerText = msg;
  toast.className = `fixed bottom-4 left-1/2 -translate-x-1/2 px-4 py-2.5 rounded-lg text-xs font-semibold shadow-xl border transition-opacity duration-200 z-50 font-mono ${
    type === 'success' ? 'bg-emerald-900 border-emerald-700 text-emerald-200' : 'bg-rose-900 border-rose-700 text-rose-200'
  } opacity-100`;
  setTimeout(() => { toast.classList.add('opacity-0'); }, 2500);
}

function formatIsoTime(isoStr) {
  if (!isoStr) return '--:--:--';
  try {
    const dt = new Date(isoStr);
    return dt.toLocaleTimeString('de-CH', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch (e) {
    return '--:--:--';
  }
}

// ==========================================
// LIVE-DATA POLLING LOOP
// ==========================================

async function fetchData() {
  try {
    const res = await fetch('/api/live-data');
    const data = await res.json();
    liveCacheData = data;

    const triggerBody = document.getElementById('trigger-table-body');
    if (triggerBody && data.triggers && data.triggers.length > 0) {
      triggerBody.innerHTML = data.triggers.map(tData => {
        const localized = formatAlertText(tData.box_name, tData.box_id, tData.title, tData.message);
        return `
          <tr>
            <td class="py-1.5 text-slate-400 whitespace-nowrap pr-2">${tData.time}</td>
            <td class="py-1.5 font-semibold text-rose-300 whitespace-nowrap pr-2">${tData.box_name}</td>
            <td class="py-1.5 text-slate-300">
              ${localized.title ? '<span class="text-rose-400 font-semibold">' + localized.title + ':</span> ' : ''}${localized.message}
            </td>
          </tr>
        `;
      }).join('');
    } else if (triggerBody) {
      triggerBody.innerHTML = `<tr><td colspan="3" class="py-2 text-slate-500 text-center italic">${t('alarms.empty', 'Keine Beton-Alarme registriert')}</td></tr>`;
    }

    for (const [bid, bdata] of Object.entries(data.boxes)) {
      const dot = document.getElementById(`tab-dot-${bid}`);
      if (dot) dot.className = `w-2 h-2 rounded-full ${bdata.online ? 'bg-emerald-400' : 'bg-rose-500'}`;

      const pill = document.getElementById(`status-pill-${bid}`);
      if (pill) {
        pill.innerText = bdata.online ? t('box.online', 'ONLINE') : t('box.offline', 'OFFLINE');
        pill.className = `px-2 py-0.5 rounded text-[10px] font-mono font-semibold border ${
          bdata.online ? 'bg-emerald-950 text-emerald-400 border-emerald-800' : 'bg-rose-950 text-rose-400 border-rose-800'
        }`;
      }

      const elPend = document.getElementById(`pending-pill-${bid}`);
      if (elPend) {
        const pCount = bdata.pending_count || 0;
        elPend.innerText = `${t('box.buffer', 'Puffer')}: ${pCount}`;
        if (pCount > 60) {
          elPend.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-amber-950 text-amber-400 border border-amber-800 animate-pulse';
        } else {
          elPend.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-slate-800 text-slate-400 border border-slate-700';
        }
      }

      const elAmb = document.getElementById(`val-ambient-${bid}`);
      if (elAmb) elAmb.innerText = bdata.ambient != null ? `${bdata.ambient.toFixed(1)} °C` : '--.- °C';

      const elHum = document.getElementById(`val-humidity-${bid}`);
      if (elHum) elHum.innerText = bdata.humidity != null ? `${bdata.humidity.toFixed(1)} %` : '--.- %';

      if (bdata.last_message && document.getElementById(`msg-text-${bid}`)) {
        const localizedLast = formatAlertText('', bid, bdata.last_message.title, bdata.last_message.message);
        document.getElementById(`msg-text-${bid}`).innerText = `${localizedLast.title ? localizedLast.title + '\n' : ''}${localizedLast.message}`;
        document.getElementById(`msg-time-${bid}`).innerText = bdata.last_message.time;
      }

      if (bdata.channel_temps) {
        for (const [chid, cval] of Object.entries(bdata.channel_temps)) {
          const elVal = document.getElementById(`val-ch-${bid}-${chid}`);
          if (elVal) elVal.innerText = cval != null ? `${cval.toFixed(1)} °C` : '--.- °C';
        }
      }

      if (bdata.channel_states) {
        for (const [chid, st] of Object.entries(bdata.channel_states)) {
          const elBadge = document.getElementById(`badge-ch-${bid}-${chid}`);
          if (elBadge) {
            let stText = st;
            if (st === 'RESET') stText = t('box.ready', 'BEREIT');
            elBadge.innerText = stText;
            elBadge.className = `badge-${st} px-2 py-0.5 rounded text-[10px] font-bold font-mono inline-block mt-0.5`;
          }
        }
      }

      // Phasen-Timeline Zeiten und Statusbadges aktualisieren
      if (bdata.channel_phases) {
        for (const [chid, phases] of Object.entries(bdata.channel_phases)) {
          const elStart = document.getElementById(`phase-time-start-${bid}-${chid}`);
          const elTurn = document.getElementById(`phase-time-turnaround-${bid}-${chid}`);
          const elTrig = document.getElementById(`phase-time-trigger-${bid}-${chid}`);

          if (elStart) elStart.innerText = formatIsoTime(phases.start);
          if (elTurn) elTurn.innerText = formatIsoTime(phases.turnaround);
          if (elTrig) elTrig.innerText = formatIsoTime(phases.trigger);

          const chipStart = document.getElementById(`phase-start-${bid}-${chid}`);
          const chipTurn = document.getElementById(`phase-turnaround-${bid}-${chid}`);
          const chipTrig = document.getElementById(`phase-trigger-${bid}-${chid}`);

          if (chipStart) {
            chipStart.className = `p-1 rounded border transition cursor-pointer ${
              phases.start ? 'bg-emerald-950/60 border-emerald-700/80 text-emerald-200' : 'bg-slate-900 border-slate-800 text-slate-400 hover:border-sky-500'
            }`;
          }
          if (chipTurn) {
            chipTurn.className = `p-1 rounded border transition cursor-pointer ${
              phases.turnaround ? 'bg-amber-950/60 border-amber-700/80 text-amber-200' : 'bg-slate-900 border-slate-800 text-slate-400 hover:border-amber-500'
            }`;
          }
          if (chipTrig) {
            chipTrig.className = `p-1 rounded border transition cursor-pointer ${
              phases.trigger ? 'bg-rose-950/60 border-rose-700/80 text-rose-200' : 'bg-slate-900 border-slate-800 text-slate-400 hover:border-rose-500'
            }`;
          }
        }
      }

      // Grid-Ansicht Karten
      const gDot = document.getElementById(`grid-dot-${bid}`);
      if (gDot) gDot.className = `w-2 h-2 rounded-full ${bdata.online ? 'bg-emerald-400' : 'bg-rose-500'}`;

      const gPill = document.getElementById(`grid-status-pill-${bid}`);
      if (gPill) {
        gPill.innerText = bdata.online ? t('box.online', 'ONLINE') : t('box.offline', 'OFFLINE');
        gPill.className = `px-2 py-0.5 rounded text-[10px] font-mono font-semibold border ${
          bdata.online ? 'bg-emerald-950 text-emerald-400 border-emerald-800' : 'bg-rose-950 text-rose-400 border-rose-800'
        }`;
      }

      if (bdata.channel_temps) {
        for (const [chid, cval] of Object.entries(bdata.channel_temps)) {
          const gVal = document.getElementById(`grid-val-ch-${bid}-${chid}`);
          if (gVal) gVal.innerText = cval != null ? `${cval.toFixed(1)} °C` : '--.- °C';
        }
      }

      if (bdata.channel_states) {
        for (const [chid, st] of Object.entries(bdata.channel_states)) {
          const gBadge = document.getElementById(`grid-badge-ch-${bid}-${chid}`);
          if (gBadge) {
            let stText = st;
            if (st === 'RESET') stText = t('box.ready', 'BEREIT');
            gBadge.innerText = stText;
            gBadge.className = `badge-${st} px-1.5 py-0.2 rounded text-[9px] font-bold font-mono`;
          }
        }
      }

      // Synchronisation der Kippschalter
      const swLog = document.getElementById(`switch-logging-${bid}`);
      if (swLog && bdata.channel_recording_enabled !== undefined) swLog.checked = bdata.channel_recording_enabled;

      const swAuto = document.getElementById(`switch-autodetect-${bid}`);
      if (swAuto && bdata.auto_detection_enabled !== undefined) swAuto.checked = bdata.auto_detection_enabled;

      const swTurn = document.getElementById(`switch-turnaround-${bid}`);
      if (swTurn && bdata.turnaround_detection_enabled !== undefined) swTurn.checked = bdata.turnaround_detection_enabled;

      const swNtfy = document.getElementById(`switch-ntfy-${bid}`);
      if (swNtfy && bdata.ntfy_enabled !== undefined) swNtfy.checked = bdata.ntfy_enabled;
    }

    const currentBox = window.ACTIVE_BOX_ID || activeBoxId;
    if (currentBox && currentDashboardMode === 'tabs') {
      const quickTableVisible = document.getElementById(`subtab-content-${currentBox}-quick_table`);
      if (quickTableVisible && !quickTableVisible.classList.contains('hidden')) {
        loadWidgetData(currentBox);
      }
    }
  } catch (err) {
    console.error("Fetch Data Error:", err);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  applySavedBoxVisibility();

  const saved = localStorage.getItem('concretum_active_box');
  let targetId = (saved && document.getElementById(`tab-content-${saved}`)) ? saved : '';
  
  if (!targetId) {
    const firstTab = document.querySelector('.tab-content');
    if (firstTab && firstTab.id) {
      targetId = firstTab.id.replace('tab-content-', '');
    } else {
      targetId = window.INITIAL_BOX_ID || '';
    }
  }

  if (targetId) {
    activeBoxId = targetId;
    window.ACTIVE_BOX_ID = targetId;
    switchTab(targetId);
  }

  fetch('/api/auth/status')
    .then(r => r.json())
    .then(data => {
      window.IS_ADMIN = Boolean(data.is_admin);
      const logoutBtn = document.getElementById('btn-admin-logout');
      if (logoutBtn && window.IS_ADMIN) {
        logoutBtn.classList.remove('hidden');
      }
    })
    .catch(() => {});

  fetchData();
  setInterval(fetchData, 20000);
});