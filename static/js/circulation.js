/* Issue page (due-date slip, book picker, validation) and return page (search + return). */
(() => {
  const { $, $$, api, debounce } = window.LV;
  const fmt = (iso) => {
    if (!iso) return '';
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  };
  const iso = (dt) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;

  /* ================= ISSUE ================= */
  const form = $('#issue-form');
  if (form) {
    const f = { no: $('#student_no'), name: $('#student_name'), book: $('#book_id'), issue: $('#issue_date'), due: $('#due_date') };
    const loan = Number(form.dataset.loanDays) || 14;
    let dueTouched = false, bookTitle = form.dataset.bookTitle || '';

    const setErr = (field, msg) => {
      const el = $(`#err-${field}`); if (!el) return;
      el.textContent = msg || '';
      const input = $(`#${field}`) || (field === 'book_id' ? $('#book-search') : null);
      if (input) { input.classList.toggle('invalid', !!msg); input.setAttribute('aria-invalid', msg ? 'true' : 'false'); }
    };
    const clearErrs = () => ['student_no', 'student_name', 'book_id', 'issue_date', 'due_date'].forEach((k) => setErr(k, ''));

    const slip = () => {
      $('#slip-student').textContent = f.name.value.trim() ? `${f.name.value.trim()}${f.no.value.trim() ? ' (' + f.no.value.trim().toUpperCase() + ')' : ''}` : 'Not entered yet';
      $('#slip-book').textContent = bookTitle || 'Not chosen yet';
      $('#slip-issue').textContent = fmt(f.issue.value) || '-';
      $('#slip-due').textContent = fmt(f.due.value) || '-';
    };
    const syncDue = () => {
      if (dueTouched || !f.issue.value) return;
      const [y, m, d] = f.issue.value.split('-').map(Number);
      const due = new Date(y, m - 1, d + loan);
      f.due.value = iso(due);
    };
    [f.no, f.name].forEach((el) => el.addEventListener('input', slip));
    f.issue.addEventListener('change', () => { syncDue(); f.due.min = f.issue.value; slip(); });
    f.due.addEventListener('change', () => { dueTouched = true; slip(); });

    // look up a registered student when the ID field loses focus
    f.no.addEventListener('blur', async () => {
      const v = f.no.value.trim();
      if (v.length < 3) return;
      try {
        const r = await api(form.dataset.studentUrl + encodeURIComponent(v));
        const hint = $('#hint-student');
        if (r.found) { f.name.value = r.name; hint.textContent = `Registered student: ${r.name}.`; }
        else hint.textContent = 'New student ID. They will be registered when you issue the book.';
        slip();
      } catch { /* lookup is a convenience only */ }
    });

    // book picker
    const search = $('#book-search'), list = $('#book-options');
    const chooseBook = (b) => {
      f.book.value = b.id; bookTitle = b.title;
      $('#chosen-title').textContent = b.title;
      $('#book-chosen').hidden = false; $('#book-picker').hidden = true; list.hidden = true;
      setErr('book_id', ''); slip();
    };
    const renderOptions = (items) => {
      list.innerHTML = '';
      if (!items.length) {
        const li = document.createElement('li'); li.textContent = 'No books match that search.'; li.setAttribute('aria-disabled', 'true'); list.append(li);
      }
      items.forEach((b) => {
        const li = document.createElement('li'); li.setAttribute('role', 'option');
        const left = document.createElement('span'); const t = document.createElement('strong'); t.textContent = b.title;
        const a = document.createElement('small'); a.textContent = b.author; left.append(t, a);
        const right = document.createElement('small'); right.textContent = b.available_copies > 0 ? `${b.available_copies} in` : 'None available';
        li.append(left, right);
        if (b.available_copies <= 0) li.setAttribute('aria-disabled', 'true');
        else li.addEventListener('click', () => chooseBook(b));
        list.append(li);
      });
      list.hidden = false; search.setAttribute('aria-expanded', 'true');
    };
    const lookup = debounce(async () => {
      try { renderOptions((await api(`${form.dataset.booksUrl}?q=${encodeURIComponent(search.value)}`)).items); }
      catch (e) { window.LV.toast(e.message, 'error'); }
    }, 200);
    search.addEventListener('input', lookup);
    search.addEventListener('focus', lookup);
    document.addEventListener('click', (e) => { if (!$('#book-field').contains(e.target)) { list.hidden = true; search.setAttribute('aria-expanded', 'false'); } });
    $('#change-book').addEventListener('click', () => {
      $('#book-chosen').hidden = true; $('#book-picker').hidden = false; f.book.value = ''; bookTitle = ''; slip(); search.focus();
    });

    // submit
    const btn = $('#issue-submit');
    form.addEventListener('submit', async (e) => {
      e.preventDefault(); clearErrs();
      let bad = false;
      if (!/^[A-Za-z0-9][A-Za-z0-9\/_-]{2,19}$/.test(f.no.value.trim())) { setErr('student_no', 'Enter a student ID of 3 to 20 letters or digits.'); bad = true; }
      if (f.name.value.trim().length < 2) { setErr('student_name', 'Enter the student\u2019s full name.'); bad = true; }
      if (!f.book.value) { setErr('book_id', 'Choose a book to issue.'); bad = true; }
      if (!f.issue.value) { setErr('issue_date', 'Pick an issue date.'); bad = true; }
      if (!f.due.value) { setErr('due_date', 'Pick a due date.'); bad = true; }
      else if (f.issue.value && f.due.value < f.issue.value) { setErr('due_date', 'Due date must be on or after the issue date.'); bad = true; }
      if (bad) { const first = $('.invalid', form); if (first) first.focus(); window.LV.toast('Please fix the highlighted fields.', 'warning'); return; }

      btn.classList.add('loading'); btn.disabled = true;
      try {
        const r = await api(form.dataset.issueUrl, { method: 'POST', json: {
          student_no: f.no.value.trim(), student_name: f.name.value.trim(), book_id: f.book.value,
          issue_date: f.issue.value, due_date: f.due.value } });
        window.LV.toast(r.message, 'success');
        $('#stamp').hidden = false;
        $('#slip-due').textContent = fmt(r.record.due_date);
        $('#after-text').textContent = `${r.record.available} of ${r.record.total} copies of this book are still available. Due back ${fmt(r.record.due_date)}.`;
        $('#after-issue').hidden = false; form.hidden = true;
      } catch (err) {
        if (err.field) setErr(err.field, err.message);
        window.LV.toast(err.message, 'error');
      } finally { btn.classList.remove('loading'); btn.disabled = false; }
    });

    $('#issue-another').addEventListener('click', () => {
      form.hidden = false; $('#after-issue').hidden = true; $('#stamp').hidden = true;
      f.book.value = ''; bookTitle = ''; $('#book-chosen').hidden = true; $('#book-picker').hidden = false; search.value = '';
      f.no.value = ''; f.name.value = ''; dueTouched = false; f.issue.value = iso(new Date()); syncDue(); slip(); f.no.focus();
    });
    slip();
  }

  /* ================= RETURN ================= */
  const app = $('#return-app');
  if (app) {
    const list = $('#r-list'), count = $('#r-count'), q = $('#r-q');
    let ticket = 0;
    const status = () => $('input[name="status"]:checked').value;
    async function load() {
      const mine = ++ticket;
      list.setAttribute('aria-busy', 'true');
      try {
        const r = await api(`${app.dataset.listUrl}?status=${status()}&q=${encodeURIComponent(q.value.trim())}`);
        if (mine !== ticket) return;
        list.innerHTML = r.html;
        count.textContent = `${r.total} record${r.total === 1 ? '' : 's'}`;
      } catch (e) { count.textContent = 'Could not load loans'; window.LV.toast(e.message, 'error'); }
      finally { if (mine === ticket) list.setAttribute('aria-busy', 'false'); }
    }
    q.addEventListener('input', debounce(load, 250));
    $$('input[name="status"]').forEach((r) => r.addEventListener('change', load));
    list.addEventListener('click', async (e) => {
      const btn = e.target.closest('.return-btn'); if (!btn) return;
      const fine = Number(btn.dataset.fine) || 0, late = Number(btn.dataset.late) || 0;
      const bill = fine
        ? `Return bill: ${late} day${late === 1 ? '' : 's'} late × ₹${late ? fine / late : 0} = ₹${fine} fine.`
        : 'Return bill: no fine, returned on time.';
      const ok = await window.LV.confirm({ title: 'Request return?', body: `${btn.dataset.title} from ${btn.dataset.student}. ${bill} The book is marked Returned once an admin approves.`, confirmText: 'Request return' });
      if (!ok) return;
      btn.classList.add('loading'); btn.disabled = true;
      try {
        const r = await api(app.dataset.returnUrl.replace(/0$/, btn.dataset.id), { method: 'POST' });
        window.LV.toast(r.message, 'success');
      } catch (err) { window.LV.toast(err.message, 'error'); }
      load();
    });
    load();
  }
})();
