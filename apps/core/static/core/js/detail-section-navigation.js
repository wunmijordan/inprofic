/* Shared anchor navigation for detail pages with collapsible data sections. */
(() => {
  const navigations = [...document.querySelectorAll('[data-detail-navigation]')];
  if (!navigations.length) return;
  function reveal(hash, focus) {
    if (!hash || !hash.startsWith('#')) return;
    let id;
    try { id = decodeURIComponent(hash.slice(1)); } catch (_) { return; }
    const target = document.getElementById(id);
    if (!target) return;
    for (let node = target; node; node = node.parentElement) {
      if (node.tagName === 'DETAILS') node.open = true;
    }
    navigations.forEach(nav => nav.querySelectorAll('a[href^="#"]').forEach(link => {
      if (link.hash === hash) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }));
    if (focus) {
      const heading = target.querySelector('summary, h2, h3') || target;
      if (!heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1');
      heading.focus({preventScroll: true});
    }
    target.scrollIntoView({block: 'start', behavior: 'instant'});
  }
  navigations.forEach(nav => nav.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (!document.getElementById(link.hash.slice(1))) return;
    event.preventDefault();
    history.replaceState(null, '', link.hash);
    reveal(link.hash, true);
  }));
  window.addEventListener('hashchange', () => reveal(location.hash, false));
  if (location.hash) requestAnimationFrame(() => reveal(location.hash, false));
})();
