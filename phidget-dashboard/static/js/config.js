/**
 * @file: static/js/config.js
 * @version: 2.0.0
 * @date: 2026-08-29
 * @description: Clientseitige Formular-Serialisierung mit Tab-Persistenz via sessionStorage,
 *               Hamburger-Navigation und Admin-Logout (DEV-28).
 * @author: Patrick Staehli
 */

let activeKey = sessionStorage.getItem('concretum_active_config_key') || window.INITIAL_CONFIG_KEY || '__NEW__';

function switchConfigTab(key) {
  activeKey = key;
  sessionStorage.setItem('concretum_active_config_key', key);

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

  // Checkboxen explizit als Boolean uebertragen
  const checkboxes = form.querySelectorAll('input[type="checkbox"]');
  checkboxes.forEach(cb => {
    payload[cb.name] = cb.checked;
  });

  // Tab vor Reload fixieren
  sessionStorage.setItem('concretum_active_config_key', key);

  try {
    const res = await fetch('/api/config/save-box', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await res.json();
    if (result.success) {
      showToast('Aenderungen erfolgreich in YAML gespeichert!', 'success');
      setTimeout(() => location.reload(), 500);
    } else {
      if (result.auth_required) {
        showToast('Sitzung abgelaufen. Bitte neu anmelden.', 'error');
        setTimeout(() => { window.location.href = '/'; }, 1000);
      } else {
        showToast(result.error || 'Fehler beim Speichern', 'error');
      }
    }
  } catch (err) {
    showToast('Netzwerkfehler', 'error');
  }
}

async function saveNewBox(e) {
  e.preventDefault();
  const newKey = document.getElementById('new-yaml-key').value.trim();
  const payload = {
    yaml_key: newKey,
    name: document.getElementById('new-name').value.trim(),
    device_id: document.getElementById('new-device-id').value.trim(),
    order: parseInt(document.getElementById('new-order').value || 100),
    ntfy_channel: document.getElementById('new-topic').value.trim(),
    timezone: document.getElementById('new-tz').value.trim(),
    channel_count: parseInt(document.getElementById('new-channel-count').value || 4),
    box_label: document.getElementById('new-box-label').value.trim(),
    ntfy_enabled: true,
    auto_reset_after_30m: true
  };

  sessionStorage.setItem('concretum_active_config_key', newKey);

  try {
    const res = await fetch('/api/config/save-box', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await res.json();
    if (result.success) {
      showToast('Neues Geraet angelegt!', 'success');
      setTimeout(() => location.reload(), 500);
    } else {
      showToast(result.error || 'Fehler beim Anlegen', 'error');
    }
  } catch (err) {
    showToast('Netzwerkfehler', 'error');
  }
}

async function deleteBox(key, name) {
  if (!confirm(`Soll das Geraet "${name}" (${key}) wirklich aus der YAML geloescht werden?`)) return;

  sessionStorage.removeItem('concretum_active_config_key');

  try {
    const res = await fetch('/api/config/delete-box', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yaml_key: key })
    });
    const result = await res.json();
    if (result.success) {
      showToast('Geraet geloescht!', 'success');
      setTimeout(() => location.reload(), 500);
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
  toast.className = `fixed bottom-4 left-1/2 -translate-x-1/2 px-4 py-2.5 rounded-lg text-xs font-semibold shadow-xl border transition-opacity duration-200 z-50 font-mono ${
    type === 'success' ? 'bg-emerald-900 border-emerald-700 text-emerald-200' : 'bg-rose-900 border-rose-700 text-rose-200'
  } opacity-100`;
  setTimeout(() => { toast.classList.add('opacity-0'); }, 2500);
}

document.addEventListener('DOMContentLoaded', () => {
  const savedKey = sessionStorage.getItem('concretum_active_config_key');
  const targetKey = (savedKey && document.getElementById(`tab-content-${savedKey}`)) ? savedKey : (window.INITIAL_CONFIG_KEY || '__NEW__');
  if (targetKey) switchConfigTab(targetKey);
});