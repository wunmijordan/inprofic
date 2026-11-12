(() => {
  document.querySelectorAll('[data-expense-request-editor]').forEach(form => {
    function remove(row) {
      row.querySelector('[data-expense-request-delete]').value = 'on';
      const existing = row.querySelector('input[name$="-id"]')?.value;
      row.hidden = !existing;
      if (existing) {
        row.querySelector('[data-expense-request-fields]').hidden = true;
        const note = row.querySelector('[data-expense-request-removal-note]');
        note.hidden = false;
        note.textContent = 'Removed: ' + (row.querySelector('[name$="-description"]')?.value || 'expense item') + ' · give the reason below';
        row.querySelector('[data-expense-request-remove]').hidden = true;
        row.querySelector('[name$="-approval_reason"]')?.focus();
      }
      row.querySelectorAll('[data-expense-request-fields] input,[data-expense-request-fields] select,[data-expense-request-fields] textarea').forEach(field => field.disabled = true);
    }
    form.querySelectorAll('[data-expense-request-line]').forEach(row => {
      if (row.querySelector('[data-expense-request-delete]').value === 'on') remove(row);
    });
    form.addEventListener('click', event => {
      const button = event.target.closest('[data-expense-request-remove]');
      if (!button) return;
      remove(button.closest('[data-expense-request-line]'));
      (form.querySelector('[data-expense-request-line]:not([hidden]) button') || form.querySelector('[data-expense-request-add]'))?.focus();
    });
    form.querySelector('[data-expense-request-add]')?.addEventListener('click', () => {
      const count = form.querySelector('[name="lines-TOTAL_FORMS"]');
      const index = Number(count.value);
      if (index >= 100) return;
      const container = document.createElement('div');
      container.innerHTML = form.querySelector('[data-expense-request-empty]').innerHTML.replaceAll('__prefix__', String(index));
      form.querySelector('[data-expense-request-lines]').append(...container.childNodes);
      count.value = String(index + 1);
    });
  });
})();
