/**
 * @file: config.js
 * @version: 1.0.0
 * @date: 2026-08-24
 * @description: Clientseitige Logik fuer die Geraete-Konfiguration, Tab-Umschaltung, Sprachwechsel und Formularverarbeitung (DEV-11).
 * @author: Patrick Staehli
 */

let activeKey = window.INITIAL_CONFIG_KEY || '__NEW__';

function switchConfigTab(key) {
  activeKey = key;
  document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach(el => {
    el.classList.remove('bg-slate-800', 'text-white', 'border-slate-600');
    el.classList.add('bg-slate-900', 'text-slate-400', 'border-slate-800');
  });

  const activeContent = document.getElementById(`tab-content-${key}`);
  if (activeContent) activeContent.classList.remove('hidden');

  const activeBtn = document.getElementById(`tab-btn-${key}`);
  if (activeBtn) {
    activeBtn.classList.remove('bg-slate-900', 'text-slate-400', 'border-slate-800');
    activeBtn.classList.add('bg-slate-800', 'text-white', 'border-slate-600');
  }
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

async function saveBox(e, key) {
  e.preventDefault();
  const form = e.target;
  const formData = new FormData(form);
  const payload = Object.fromEntries(formData.entries());

  // Checkboxen explizit als Boolean abbilden (falls nicht angehakt -> False)
  payload['ntfy_enabled'] = form.elements['ntfy_enabled'] ? form.elements['ntfy_enabled'].checked : true;
  payload['auto_reset_after_30m'] = form.elements['auto_reset_after_30m'] ? form.elements['auto_reset_after_30m'].checked : true;

  try {
    const res = await fetch('/api/config/save-box', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await res.json();
    if (result.success) {
      showToast('Aenderungen gespeichert!', 'success');
      setTimeout(() => location.reload(), 600);
    } else {
      showToast(result.error || 'Fehler beim Speichern', 'error');
    }
  } catch (err) {
    showToast('Netzwerkfehler', 'error');
  }
}

async function saveNewBox(e) {
  e.preventDefault();
  const payload = {
    yaml_key: document.getElementById('new-yaml-key').value.trim(),
    name: document.getElementById('new-name').value.trim(),
    device_id: document.getElementById('new-device-id').value.trim(),
    ntfy_channel: document.getElementById('new-topic').value.trim(),
    timezone: document.getElementById('new-tz').value.trim(),
    channel_count: document.getElementById('new-channel-count').value,
    box_label: document.getElementById('new-box-label').value.trim(),
    ntfy_enabled: true,
    auto_reset_after_30m: true
  };

  try {
    const res = await fetch('/api/config/save-box', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await res.json();
    if (result.success) {
      showToast('Neues Geraet angelegt!', 'success');
      setTimeout(() => location.reload(), 600);
    } else {
      showToast(result.error || 'Fehler beim Anlegen', 'error');
    }
  } catch (err) {
    showToast('Netzwerkfehler', 'error');
  }
}

async function deleteBox(key, name) {
  if (!confirm(`Soll das Geraet "${name}" (${key}) wirklich aus der YAML geloescht werden?`)) return;

  try {
    const res = await fetch('/api/config/delete-box', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yaml_key: key })
    });
    const result = await res.json();
    if (result.success) {
      showToast('Geraet geloescht!', 'success');
      setTimeout(() => location.reload(), 600);
    } else {
      showToast(result.error || 'Fehler beim Loeschen', 'error');
    }
  } catch (err) {
    showToast('Netzwerkfehler', 'error');
  }
}

function showToast(msg, type) {
  const toast = document.getElementById('toast');
  toast.innerText = msg;
  toast.className = `fixed bottom-4 left-1/2 -translate-x-1/2 px-4 py-2.5 rounded-lg text-xs font-semibold shadow-xl border transition-opacity duration-200 z-50 ${
    type === 'success' ? 'bg-emerald-900 border-emerald-700 text-emerald-200' : 'bg-rose-900 border-rose-700 text-rose-200'
  } opacity-100`;
  setTimeout(() => { toast.classList.add('opacity-0'); }, 2500);
}

document.addEventListener('DOMContentLoaded', () => {
  if (activeKey) switchConfigTab(activeKey);
});