(() => {
  const shareHeading = document.querySelector('[data-proposal-share-ready]');
  if (shareHeading) {
    const openShare = () => {
      const trigger = shareHeading.querySelector('[data-ui-snapshot-menu] button');
      if (!trigger) return false;
      trigger.focus();
      trigger.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true}));
      const url = new URL(location.href);
      url.searchParams.delete('share');
      history.replaceState(null, '', url.pathname + url.search + url.hash);
      return true;
    };
    if (!openShare()) {
      const observer = new MutationObserver(() => { if (openShare()) observer.disconnect(); });
      observer.observe(shareHeading, {childList: true, subtree: true});
    }
  }
  document.querySelectorAll('[data-proposal-editor]').forEach(form => {
    if (form.dataset.proposalWired) return;
    form.dataset.proposalWired = '1';
    function removeLine(row) {
      row.querySelector('[data-proposal-delete]').value = 'on';
      row.hidden = true;
      row.querySelectorAll('[data-proposal-fields] input, [data-proposal-fields] select, [data-proposal-fields] textarea')
        .forEach(field => { field.disabled = true; });
    }
    form.querySelectorAll('[data-proposal-line]').forEach(row => {
      if (row.querySelector('[data-proposal-delete]').value === 'on') removeLine(row);
    });
    form.addEventListener('click', event => {
      const button = event.target.closest('[data-proposal-remove]');
      if (!button) return;
      removeLine(button.closest('[data-proposal-line]'));
      const next = form.querySelector('[data-proposal-line]:not([hidden]) [data-proposal-remove]') ||
        form.querySelector('[data-proposal-add]');
      next.focus();
    });
    const count = form.querySelector('[name="lines-TOTAL_FORMS"]');
    form.querySelector('[data-proposal-add]').addEventListener('click', () => {
      const index = Number(count.value);
      if (index >= 100) return;
      const wrapper = document.createElement('div');
      wrapper.innerHTML = form.querySelector('[data-proposal-empty]').innerHTML.replaceAll('__prefix__', String(index));
      form.querySelector('[data-proposal-lines]').append(...wrapper.childNodes);
      count.value = String(index + 1);
      const row = form.querySelector('[data-proposal-lines]').lastElementChild;
      row.querySelector('select, input')?.focus();
    });
  });
})();
