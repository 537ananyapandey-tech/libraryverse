/* LIBRARYVERSE shared behaviour. No external libraries, so it works offline. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const LV = (window.LV = {});
  LV.$ = $; LV.$$ = $$;
  LV.csrf = () => $('meta[name="csrf-token"]').content;
  LV.debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  /* ---------- toasts ---------- */
  LV.toast = (message, type = 'info', ms = 5000) => {
    const box = $('#toasts');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    const p = document.createElement('p');
    p.textContent = message;
    const close = document.createElement('button');
    close.type = 'button'; close.setAttribute('aria-label', 'Dismiss message'); close.textContent = '×';
    const remove = () => { el.classList.add('leaving'); setTimeout(() => el.remove(), 250); };
    close.addEventListener('click', remove);
    el.append(p, close);
    box.append(el);
    if (ms) setTimeout(remove, ms);
  };

  /* ---------- fetch helper (adds CSRF token, throws friendly errors) ---------- */
  LV.api = async (url, options = {}) => {
    const opts = { method: 'GET', headers: {}, ...options };
    if (opts.method !== 'GET') opts.headers['X-CSRF-Token'] = LV.csrf();
    if (opts.json !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.json);
    }
    let res;
    try { res = await fetch(url, opts); }
    catch { throw Object.assign(new Error('Could not reach the server. Is it still running?'), { network: true }); }
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON error page */ }
    if (!res.ok || data.ok === false) {
      throw Object.assign(new Error(data.error || 'Something went wrong. Please try again.'), { field: data.field, status: res.status });
    }
    return data;
  };

  /* ---------- confirmation dialog ---------- */
  LV.confirm = ({ title = 'Are you sure?', body = '', confirmText = 'Confirm', danger = false } = {}) =>
    new Promise((resolve) => {
      const dlg = $('#confirm-dialog');
      if (!dlg.showModal) { resolve(window.confirm(`${title}\n${body}`)); return; }
      $('#cd-title').textContent = title;
      $('#cd-body').textContent = body;
      const ok = $('#cd-ok');
      ok.textContent = confirmText;
      ok.className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
      dlg.returnValue = '';
      dlg.addEventListener('close', () => resolve(dlg.returnValue === 'ok'), { once: true });
      dlg.showModal();
      $('#cd-cancel').focus();
    });

  document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (!form.dataset.confirm || form.dataset.confirmed) return;
    e.preventDefault();
    const yes = await LV.confirm({
      title: form.dataset.confirm, body: form.dataset.confirmBody || '',
      confirmText: form.dataset.confirmLabel || 'Confirm', danger: true,
    });
    if (yes) { form.dataset.confirmed = '1'; form.submit(); }
  });

  /* ---------- nav toggle ---------- */
  const toggle = $('.nav-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      const open = $('.site-header').classList.toggle('nav-open');
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    });
  }

  /* ---------- flashed server messages become toasts ---------- */
  try {
    JSON.parse($('#flash-data').textContent).forEach(([cat, msg]) =>
      LV.toast(msg, cat === 'message' ? 'info' : cat === 'danger' ? 'error' : cat));
  } catch { /* nothing flashed */ }
})();

/* ---------- locate book panel ---------- */
(() => {
  const btn = document.getElementById('locate-btn'), panel = document.getElementById('locate-panel');
  if (!btn || !panel) return;
  btn.addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    btn.setAttribute('aria-expanded', String(!panel.hidden));
  });
})();
