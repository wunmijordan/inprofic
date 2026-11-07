(() => {
  document.querySelectorAll('[data-proposal-editor]').forEach(form => {
    if (form.dataset.proposalWired) return;
    form.dataset.proposalWired = '1';
    form.addEventListener('change', event => {
      if (!event.target.matches('input[name$="-DELETE"]')) return;
      const fields = event.target.closest('[data-proposal-line]').querySelector('[data-proposal-fields]');
      fields.hidden = event.target.checked;
      fields.querySelectorAll('input, select, textarea').forEach(field => { field.disabled = event.target.checked; });
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
