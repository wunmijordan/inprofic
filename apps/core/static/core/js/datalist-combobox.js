/* Replaces native <datalist> suggestions with an in-page listbox.
   Mobile browsers draw native datalist suggestions inside the keyboard strip,
   so the list never actually drops down. Opt in with data-datalist-combobox on
   an input that already has a list="" attribute; without JS the native
   datalist keeps working. */
(function () {
  'use strict';
  var GAP = 4, MIN_ROOM = 140, MAX_H = 240;
  var active = null;

  function vv() { return window.visualViewport || null; }
  function viewportBox() {
    var v = vv();
    return v ? {top: v.offsetTop, height: v.height, left: v.offsetLeft, width: v.width}
             : {top: 0, height: window.innerHeight, left: 0, width: window.innerWidth};
  }

  function build(input) {
    var list = document.getElementById(input.getAttribute('list'));
    if (!list) return;
    var options = Array.prototype.map.call(list.options, function (o) {
      return {value: o.value, label: o.textContent || o.value};
    });
    input.removeAttribute('list');            // stop the native keyboard-strip UI
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');

    var box = document.createElement('ul');
    box.className = 'dl-combobox';
    box.setAttribute('role', 'listbox');
    box.id = (input.id || 'dl') + '_listbox';
    box.hidden = true;
    input.setAttribute('aria-controls', box.id);
    document.body.appendChild(box);

    var shown = [], index = -1;

    function render() {
      var q = input.value.trim().toLowerCase();
      shown = options.filter(function (o) {
        return !q || o.value.toLowerCase().indexOf(q) !== -1 || o.label.toLowerCase().indexOf(q) !== -1;
      }).slice(0, 60);
      box.textContent = '';
      shown.forEach(function (o, i) {
        var li = document.createElement('li');
        li.setAttribute('role', 'option');
        li.textContent = o.label;
        li.dataset.i = i;
        box.appendChild(li);
      });
      index = -1;
      return shown.length > 0;
    }

    function place() {
      var r = input.getBoundingClientRect(), v = viewportBox();
      var below = v.top + v.height - r.bottom - GAP;
      var above = r.top - v.top - GAP;
      var up = below < MIN_ROOM && above > below;
      var room = Math.max(80, Math.min(MAX_H, up ? above : below));
      var width = Math.min(r.width, v.width - 16);
      var left = Math.min(Math.max(v.left + 8, r.left), v.left + v.width - width - 8);
      box.style.width = width + 'px';
      box.style.left = left + 'px';
      box.style.maxHeight = room + 'px';
      if (up) { box.style.top = 'auto'; box.style.bottom = (window.innerHeight - r.top + GAP) + 'px'; }
      else { box.style.bottom = 'auto'; box.style.top = (r.bottom + GAP) + 'px'; }
    }

    function open() {
      if (!render()) return close();
      box.hidden = false; place();
      input.setAttribute('aria-expanded', 'true');
      active = {close: close, place: place};
    }
    function close() {
      box.hidden = true; input.setAttribute('aria-expanded', 'false');
      if (active && active.close === close) active = null;
    }
    function choose(i) {
      if (!shown[i]) return;
      input.value = shown[i].value;
      input.dispatchEvent(new Event('input', {bubbles: true}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
      close();
    }
    function highlight(n) {
      var items = box.children;
      if (!items.length) return;
      index = (n + items.length) % items.length;
      Array.prototype.forEach.call(items, function (li, i) { li.classList.toggle('is-active', i === index); });
      items[index].scrollIntoView({block: 'nearest'});
    }

    input.addEventListener('focus', open);
    input.addEventListener('click', open);
    input.addEventListener('input', open);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); if (box.hidden) open(); highlight(index + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); highlight(index - 1); }
      else if (e.key === 'Enter' && !box.hidden && index >= 0) { e.preventDefault(); choose(index); }
      else if (e.key === 'Escape' || e.key === 'Tab') close();
    });
    // pointerdown + preventDefault keeps focus on the input so the keyboard stays up
    box.addEventListener('pointerdown', function (e) {
      var li = e.target.closest('li'); if (!li) return;
      e.preventDefault(); choose(Number(li.dataset.i));
    });
    input.addEventListener('blur', function () { setTimeout(close, 120); });
  }

  function reposition() { if (active) active.place(); }
  window.addEventListener('resize', reposition);
  window.addEventListener('scroll', reposition, true);
  if (vv()) { vv().addEventListener('resize', reposition); vv().addEventListener('scroll', reposition); }

  function init(root) {
    (root || document).querySelectorAll('input[data-datalist-combobox][list]').forEach(build);
  }
  document.addEventListener('DOMContentLoaded', function () { init(document); });
  window.INPROFICDatalistCombobox = init;
})();
