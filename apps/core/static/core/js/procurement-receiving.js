document.addEventListener('change', event => {
  if (!event.target.matches('[data-receive-vendor]')) return;
  const date = event.target.closest('[data-vendor-receipt]').querySelector('[data-receive-date]');
  date.disabled = !event.target.checked;
  date.required = event.target.checked;
});
