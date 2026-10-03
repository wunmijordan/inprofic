/* Turns <select multiple data-multiselect-dropdown> into a real dropdown with
   tick boxes. The select stays the source of truth (it is hidden, never
   removed), so form posting/validation is unchanged and, without JS, the
   native list still works. Other scripts can hide/disable <option>s and then
   dispatch a "ms-refresh" event on the select to re-render. */
(function () {
  'use strict';
  var uid = 0;

  function shortLabel(text) { return (text || '').split(' \u00b7 ')[0].trim(); }

  function build(select) {
    if (select.dataset.msBound) return;
    select.dataset.msBound = '1';
    var id = 'msdd' + (++uid);
    var placeholder = select.dataset.placeholder || 'Select\u2026';
    var noun = select.dataset.noun || 'selected';

    var root = document.createElement('div'); root.className = 'ms-dd';
    var trigger = document.createElement('button');
    trigger.type = 'button'; trigger.className = 'ms-dd__trigger';
    trigger.setAttribute('aria-haspopup', 'listbox'); trigger.setAttribute('aria-expanded', 'false'); trigger.setAttribute('aria-controls', id);
    var summary = document.createElement('span'); summary.className = 'ms-dd__summary';
    var caret = document.createElement('span'); caret.className = 'ms-dd__caret'; caret.setAttribute('aria-hidden', 'true'); caret.textContent = '\u25be';
    trigger.appendChild(summary); trigger.appendChild(caret);

    var panel = document.createElement('div'); panel.className = 'ms-dd__panel'; panel.id = id; panel.hidden = true;
    var search = document.createElement('input'); search.type = 'search'; search.className = 'ms-dd__search'; search.placeholder = 'Search\u2026'; search.setAttribute('aria-label', 'Search');
    var list = document.createElement('div'); list.className = 'ms-dd__list'; list.setAttribute('role', 'listbox'); list.setAttribute('aria-multiselectable', 'true');
    var foot = document.createElement('div'); foot.className = 'ms-dd__foot';
    var clear = document.createElement('button'); clear.type = 'button'; clear.className = 'ms-dd__clear'; clear.textContent = 'Clear';
    var done = document.createElement('button'); done.type = 'button'; done.className = 'ms-dd__done'; done.textContent = 'Done';
    foot.appendChild(clear); foot.appendChild(done);
    panel.appendChild(search); panel.appendChild(list); panel.appendChild(foot);
    root.appendChild(trigger); root.appendChild(panel);
    select.hidden = true; select.setAttribute('aria-hidden', 'true');
    select.parentNode.insertBefore(root, select.nextSibling);

    function updateSummary() {
      var picked = Array.prototype.filter.call(select.options, function (o) { return o.selected; });
      if (!picked.length) { summary.textContent = placeholder; root.classList.remove('has-value'); return; }
      root.classList.add('has-value');
      var names = picked.slice(0, 3).map(function (o) { return shortLabel(o.textContent); }).join(', ');
      summary.textContent = picked.length + ' ' + noun + ' \u00b7 ' + names + (picked.length > 3 ? '\u2026' : '');
    }

    function render() {
      var q = search.value.trim().toLowerCase();
      var boxes = list.querySelectorAll('input');
      var focusedAt = Array.prototype.indexOf.call(boxes, document.activeElement);   // keep keyboard position across re-renders
      list.textContent = '';
      var shown = 0;
      Array.prototype.forEach.call(select.options, function (opt) {
        if (opt.hidden) return;
        if (q && opt.textContent.toLowerCase().indexOf(q) === -1) return;
        var row = document.createElement('label'); row.className = 'ms-dd__opt'; row.setAttribute('role', 'option');
        var box = document.createElement('input'); box.type = 'checkbox'; box.checked = opt.selected; box.disabled = opt.disabled;
        row.setAttribute('aria-selected', opt.selected ? 'true' : 'false');
        if (opt.disabled) row.classList.add('is-disabled');
        var text = document.createElement('span'); text.textContent = opt.textContent;
        row.appendChild(box); row.appendChild(text); list.appendChild(row);
        box.addEventListener('change', function () {
          opt.selected = box.checked; row.setAttribute('aria-selected', box.checked ? 'true' : 'false');
          select.dispatchEvent(new Event('change', {bubbles: true}));   // lets other scripts react (e.g. base-material filter)
          updateSummary();
        });
        shown++;
      });
      if (!shown) { var empty = document.createElement('div'); empty.className = 'ms-dd__empty'; empty.textContent = q ? 'No matches' : 'Nothing available'; list.appendChild(empty); }
      search.hidden = select.options.length <= 6;
      if (focusedAt > -1) { var again = list.querySelectorAll('input')[focusedAt]; if (again) again.focus(); }
      updateSummary();
    }

    function place() {
      var r = trigger.getBoundingClientRect(), v = window.visualViewport;
      var vh = v ? v.height : window.innerHeight, top = v ? v.offsetTop : 0;
      var below = top + vh - r.bottom - 12, above = r.top - top - 12;
      var up = below < 220 && above > below;
      root.classList.toggle('is-up', up);
      list.style.maxHeight = Math.max(120, Math.min(288, (up ? above : below) - 96)) + 'px';
    }
    function open() { if (!panel.hidden) return; render(); panel.hidden = false; trigger.setAttribute('aria-expanded', 'true'); place(); }
    function close() { panel.hidden = true; trigger.setAttribute('aria-expanded', 'false'); }

    trigger.addEventListener('click', function () { panel.hidden ? open() : close(); });
    trigger.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); open(); var f = list.querySelector('input:not(:disabled)'); if (f) f.focus(); }
    });
    root.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !panel.hidden) { close(); trigger.focus(); } });
    search.addEventListener('input', render);
    clear.addEventListener('click', function () {
      Array.prototype.forEach.call(select.options, function (o) { o.selected = false; });
      select.dispatchEvent(new Event('change', {bubbles: true})); render();
    });
    done.addEventListener('click', function () { close(); trigger.focus(); });
    document.addEventListener('pointerdown', function (e) { if (!root.contains(e.target)) close(); });
    window.addEventListener('resize', function () { if (!panel.hidden) place(); });
    if (window.visualViewport) window.visualViewport.addEventListener('resize', function () { if (!panel.hidden) place(); });
    select.addEventListener('ms-refresh', render);
    render();
  }

  function init(root) { (root || document).querySelectorAll('select[multiple][data-multiselect-dropdown]').forEach(build); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { init(document); }); else init(document);
  window.INPROFICMultiselectDropdown = init;
})();
