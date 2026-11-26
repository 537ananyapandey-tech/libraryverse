/* Explore books: live search, filters, sorting, grid/list toggle, load more. */
(() => {
  const { $, $$, api, debounce } = window.LV;
  const root = $('#catalogue');
  if (!root) return;
  const results = $('#book-results'), skeletons = $('#skeletons'), none = $('#no-results');
  const more = $('#load-more'), count = $('#result-count'), clear = $('#clear-filters');
  const fields = { q: $('#f-q'), category: $('#f-category'), subject: $('#f-subject'), availability: $('#f-availability'), sort: $('#f-sort') };
  let page = 1, ticket = 0;

  const params = (p) => {
    const u = new URLSearchParams();
    Object.entries(fields).forEach(([k, el]) => { if (el.value) u.set(k, el.value.trim()); });
    if (p > 1) u.set('page', p);
    return u;
  };
  const dirty = () => fields.q.value || fields.category.value || fields.subject.value || fields.availability.value || fields.sort.value !== 'title';

  async function load(reset = true) {
    const mine = ++ticket;
    if (reset) { page = 1; results.innerHTML = ''; }
    else page += 1;
    results.setAttribute('aria-busy', 'true');
    skeletons.hidden = !reset; none.hidden = true; more.hidden = true;
    try {
      const data = await api(`${root.dataset.endpoint}?${params(page)}`);
      if (mine !== ticket) return;                       // a newer request replaced this one
      results.insertAdjacentHTML('beforeend', data.html);
      count.textContent = data.total === 0 ? 'No books found' : `${data.total} book${data.total === 1 ? '' : 's'} found`;
      none.hidden = data.total !== 0;
      more.hidden = !data.has_more;
      clear.hidden = !dirty();
      const qs = params(1).toString();
      history.replaceState(null, '', qs ? `?${qs}` : location.pathname);
    } catch (err) {
      if (mine !== ticket) return;
      count.textContent = 'Could not load books';
      window.LV.toast(err.message, 'error');
      if (!reset) page -= 1;
    } finally {
      if (mine === ticket) { skeletons.hidden = true; results.setAttribute('aria-busy', 'false'); }
    }
  }

  const reload = debounce(() => load(true), 250);
  fields.q.addEventListener('input', reload);
  ['category', 'subject', 'availability', 'sort'].forEach((k) => fields[k].addEventListener('change', () => load(true)));
  more.addEventListener('click', () => load(false));
  clear.addEventListener('click', () => {
    Object.values(fields).forEach((el) => { el.value = ''; });
    fields.sort.value = 'title';
    load(true); fields.q.focus();
  });

  // grid / list toggle (remembered when the browser allows it)
  const setView = (v) => {
    results.classList.toggle('is-list', v === 'list');
    $$('[data-view]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === v)));
    try { localStorage.setItem('lv-view', v); } catch { /* storage blocked */ }
  };
  $$('[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
  try { setView(localStorage.getItem('lv-view') === 'list' ? 'list' : 'grid'); } catch { setView('grid'); }

  load(true);
})();
