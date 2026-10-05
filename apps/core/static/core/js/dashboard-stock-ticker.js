/* Dashboard Raw materials / Products cards: cycles through available items.
   Item swaps scale out/in; the balance swipes in horizontally beneath it.
   Finished goods flip vertically between Store and Market balances when both
   exist. Balances are refreshed by polling (no page load). */
(function () {
  'use strict';
  var DWELL = 4200, DWELL_FLIP = 5200, FLIP_AT = 2300, OUT_MS = 240, POLL_MS = 15000;
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var nf = new Intl.NumberFormat(undefined, {maximumFractionDigits: 2});

  // Same output as the `num` template filter (always 2 decimals) for the breakdown remainder.
  var nf2 = new Intl.NumberFormat(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});

  function fmt(n) { return nf.format(n); }
  function span(cls, text) {
    var el = document.createElement('span');
    el.className = cls; el.textContent = text;
    return el;
  }
  // "2 bags, 30.00 kg" — whole purchase units then the remainder in usage units
  // (mirrors the Inventory page's raw-material stock breakdown).
  function breakdownNodes(bd) {
    var whole = Number(bd.whole);
    return [
      span('stock-ticker__val', String(whole)), document.createTextNode(' '),
      span('stock-ticker__unit stock-ticker__unit--bd', bd.purchase_unit + (whole === 1 ? '' : 's')),
      document.createTextNode(', '),
      span('stock-ticker__val', nf2.format(Number(bd.remainder))), document.createTextNode(' '),
      span('stock-ticker__unit stock-ticker__unit--bd', bd.usage_unit)
    ];
  }
  function plainNodes(value, unit) {
    return [span('stock-ticker__val', fmt(value)), span('stock-ticker__unit', unit)];
  }
  function sub(b) {
    var ch = Number(b.change) || 0;
    if (!ch) return 'Opening ' + fmt(b.opening) + ' · no change yet';
    return 'Opening ' + fmt(b.opening) + ' · ' + (ch > 0 ? '+' : '\u2212') + fmt(Math.abs(ch)) + ' today';
  }

  function Ticker(root, kind) {
    this.root = root; this.kind = kind; this.items = []; this.idx = -1; this.timers = [];
    this.item = root.querySelector('[data-t-item]');
    this.nameEl = root.querySelector('[data-t-name]');
    this.bal = root.querySelector('[data-t-bal]');
    this.flip = root.querySelector('[data-t-flip]');
    this.faces = [root.querySelector('[data-t-face="a"]'), root.querySelector('[data-t-face="b"]')];
    this.paused = false;
    var self = this;
    root.addEventListener('pointerenter', function (e) { if (e.pointerType === 'mouse') self.paused = true; });
    root.addEventListener('pointerleave', function () { self.paused = false; });
  }
  Ticker.prototype.faceData = function (it) {
    if (this.kind === 'raw') return [{label: 'Balance', b: it, bd: it.breakdown}, null];
    return [{label: 'Store', b: it.store}, it.market ? {label: 'Market', b: it.market} : null];
  };
  Ticker.prototype.paint = function (it) {
    var data = this.faceData(it), unit = it.unit ? ' ' + it.unit : '';
    this.nameEl.textContent = it.name;
    for (var i = 0; i < 2; i++) {
      var f = this.faces[i], d = data[i];
      f.hidden = !d;
      if (!d) continue;
      f.querySelector('[data-t-label]').textContent = d.label;
      var line = f.querySelector('[data-t-line]');
      while (line.firstChild) line.removeChild(line.firstChild);
      (d.bd ? breakdownNodes(d.bd) : plainNodes(d.b.balance, unit)).forEach(function (n) { line.appendChild(n); });
      f.querySelector('[data-t-sub]').textContent = sub(d.b);
    }
    this.hasFlip = !!data[1];
    this.root.classList.toggle('has-flip', this.hasFlip);
  };
  Ticker.prototype.clear = function () { this.timers.forEach(clearTimeout); this.timers = []; };
  Ticker.prototype.later = function (fn, ms) { this.timers.push(setTimeout(fn, ms)); };
  Ticker.prototype.show = function (i, first) {
    var self = this, it = this.items[i]; if (!it) return;
    this.clear(); this.idx = i;
    var enter = function () {
      self.root.classList.remove('is-flipped');
      self.paint(it);
      self.item.classList.remove('is-out'); self.bal.classList.remove('is-in'); self.item.classList.remove('is-in');
      void self.item.offsetWidth;                       // restart animations
      self.item.classList.add('is-in'); self.bal.classList.add('is-in');
      if (self.hasFlip) self.later(function () { self.root.classList.add('is-flipped'); }, FLIP_AT);
      self.later(function () { self.next(); }, self.hasFlip ? DWELL_FLIP : DWELL);
    };
    if (first || reduced) { enter(); return; }
    this.item.classList.add('is-out');
    this.later(enter, OUT_MS);
  };
  Ticker.prototype.next = function () {
    if (this.paused || document.hidden) { var s = this; this.later(function () { s.next(); }, 800); return; }
    if (this.items.length > 1) this.show((this.idx + 1) % this.items.length);
    else this.later(this.next.bind(this), DWELL);
  };
  Ticker.prototype.update = function (list) {
    var current = this.items[this.idx], prevKey = current ? JSON.stringify(current) : null;
    this.items = list || [];
    // The slot keeps its reserved height in every state; only its contents change.
    this.root.classList.remove('is-loading');
    this.root.classList.toggle('is-empty', this.items.length === 0);
    if (!this.items.length) { this.clear(); this.idx = -1; return; }
    if (!current) { this.show(0, true); return; }
    var at = this.items.findIndex(function (x) { return x.id === current.id; });
    if (at === -1) { this.show(Math.min(this.idx, this.items.length - 1)); return; }
    this.idx = at;
    var fresh = this.items[at];
    if (JSON.stringify(fresh) !== prevKey) {            // live change to what is on screen
      this.paint(fresh);
      this.bal.classList.remove('is-updated'); void this.bal.offsetWidth; this.bal.classList.add('is-updated');
    }
  };

  function init() {
    var dataEl = document.getElementById('stock-ticker-data');
    var tickers = {};
    document.querySelectorAll('[data-stock-ticker]').forEach(function (el) {
      tickers[el.dataset.stockTicker] = new Ticker(el, el.dataset.stockTicker);
    });
    function apply(data) {
      if (tickers.raw) tickers.raw.update(data.raw);
      if (tickers.finished) tickers.finished.update(data.finished);
    }
    if (dataEl) { try { apply(JSON.parse(dataEl.textContent)); } catch (e) {} }
    var urlEl = document.querySelector('[data-stock-ticker-url]');
    var url = urlEl && urlEl.dataset.stockTickerUrl;
    if (!url) return;
    var busy = false;
    function poll() {
      if (document.hidden || busy) return;
      busy = true;
      fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'})
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) apply(d); })
        .catch(function () {})
        .then(function () {
          busy = false;
          // First load failed: stop the skeleton so it doesn't pulse forever.
          Object.keys(tickers).forEach(function (k) {
            var t = tickers[k];
            if (t.root.classList.contains('is-loading')) { t.root.classList.remove('is-loading'); t.root.classList.add('is-empty'); }
          });
        });
    }
    poll();                                   // first data arrives right after the page renders
    setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
