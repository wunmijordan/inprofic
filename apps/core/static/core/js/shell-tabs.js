/* Inventory-tab persistence script, moved out of base.html (cached by the browser).
   The business key arrives via window.INPROFIC_SHELL.bizKey. */
function shellBizKey(){var c=window.INPROFIC_SHELL||{};return c.bizKey||'anonymous';}
    document.addEventListener("DOMContentLoaded", function () {
      const shell = document.getElementById('mobile-nav-shell');
      const button = document.getElementById('mobile-nav-button');
      const panel = document.getElementById('mobile-nav-panel');
      if (!shell || !button || !panel) return;

      const setOpen = (open) => {
        panel.dataset.open = open ? 'true' : 'false';
        panel.setAttribute('aria-hidden', open ? 'false' : 'true');
        panel.inert = !open;
        button.setAttribute('aria-expanded', open ? 'true' : 'false');
      };

      button.addEventListener('click', () => {
        setOpen(button.getAttribute('aria-expanded') !== 'true');
      });
      document.addEventListener('click', (event) => {
        if (button.getAttribute('aria-expanded') === 'true' && !shell.contains(event.target)) {
          setOpen(false);
        }
      });
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && button.getAttribute('aria-expanded') === 'true') {
          setOpen(false);
          button.focus();
        }
      });
      panel.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => setOpen(false)));
    });

    document.addEventListener("DOMContentLoaded", function () {
      // 1. Get DOM references (only executable on the inventory page)
      const rawRadio = document.getElementById('radio-raw');
      const finishedRadio = document.getElementById('radio-finished');
      const rawPanel = document.getElementById('raw-panel');
      const finishedPanel = document.getElementById('finished-panel');
      const rawLabel = document.getElementById('raw-tab-label');
      const finishedLabel = document.getElementById('finished-tab-label');

      // Guard script execution if the user is viewing a page without these tabs (e.g. Dashboard)
      if (!rawRadio && !finishedRadio) return;

      const activeClasses = ['opacity-100', 'translate-y-0'];
      const hiddenClasses = ['opacity-0', 'translate-y-2', 'pointer-events-none'];

      function animateIn(panel) {
        if (!panel) return;
        panel.classList.remove('hidden');
        setTimeout(() => {
          panel.classList.remove(...hiddenClasses);
          panel.classList.add(...activeClasses);
        }, 20);
      }

      function animateOut(panel) {
        if (!panel) return;
        panel.classList.remove(...activeClasses);
        panel.classList.add(...hiddenClasses);
        setTimeout(() => {
          if (panel.classList.contains('opacity-0')) {
            panel.classList.add('hidden');
          }
        }, 300);
      }

      function setActiveTab(tabKey) {
        const isRaw = tabKey === 'raw';
        
        if (rawRadio) rawRadio.checked = isRaw;
        if (finishedRadio) finishedRadio.checked = !isRaw;

        if (isRaw) {
          animateOut(finishedPanel);
          animateIn(rawPanel);
        } else {
          animateOut(rawPanel);
          animateIn(finishedPanel);
        }

        if (rawLabel) {
          rawLabel.classList.toggle('text-[#8f172d]', isRaw);
          rawLabel.classList.toggle('border-[#8f172d]', isRaw);
        }
        if (finishedLabel) {
          finishedLabel.classList.toggle('text-[#8f172d]', !isRaw);
          finishedLabel.classList.toggle('border-[#8f172d]', !isRaw);
        }

        // Persist choice so manual user reloads on inventory.html preserve state
        localStorage.setItem('activeInventoryTab:' + shellBizKey(), tabKey);
      }

      function determineInitialState() {
        // Priority 1: Check if a specific hash exists in the incoming URL string
        const hash = window.location.hash;
        if (hash === '#raw') return 'raw';
        if (hash === '#finished') return 'finished';

        // Priority 2: Fall back to memory persistence layer if reloading organically
        const storageKey = 'activeInventoryTab:' + shellBizKey();
        const savedState = localStorage.getItem(storageKey);
        if (savedState === 'raw' || savedState === 'finished') return savedState;

        return rawRadio?.dataset.defaultTab || 'raw';
      }

      // Execute rendering cycle on load
      setActiveTab(determineInitialState());

      // Listen to manual local radio tab adjustments on-screen
      rawRadio.addEventListener('change', () => setActiveTab('raw'));
      finishedRadio.addEventListener('change', () => setActiveTab('finished'));

      // Watch for tab updates or card navigation changes within the same page
      window.addEventListener('hashchange', () => {
        const hash = window.location.hash;
        if (hash === '#raw') setActiveTab('raw');
        if (hash === '#finished') setActiveTab('finished');
      });
    });

    // Submission validation: give immediate, visible feedback for required
    // or otherwise invalid fields instead of silently leaving the user to
    // guess why a form did not save. Server-side Django validation remains
    // authoritative; this is a visual/UX layer only.
    document.addEventListener("DOMContentLoaded", function () {
      const forms = document.querySelectorAll('form[method="post"]');
      forms.forEach((form) => {
        form.addEventListener("submit", function (event) {
          let firstInvalid = null;
          form.querySelectorAll('.client-field-error').forEach((el) => el.remove());
          form.querySelectorAll('.field-client-invalid').forEach((el) => el.classList.remove('field-client-invalid'));

          form.querySelectorAll('input, select, textarea').forEach((field) => {
            if (field.disabled || field.type === 'hidden') return;
            const style = window.getComputedStyle(field);
            if (style.display === 'none' || style.visibility === 'hidden') return;
            if (!field.checkValidity()) {
              field.classList.add('field-client-invalid');
              const host = field.closest('label, td, div') || field.parentElement;
              if (host && !host.querySelector('.client-field-error')) {
                const message = document.createElement('span');
                message.className = 'client-field-error block text-[11px] text-rose-600 mt-0.5';
                message.textContent = field.validationMessage || 'Please complete this field.';
                host.appendChild(message);
              }
              if (!firstInvalid) firstInvalid = field;
            }
          });

          if (firstInvalid) {
            event.preventDefault();
            firstInvalid.focus({ preventScroll: true });
            firstInvalid.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        });
      });
    });

    // Generic "add another row" helper for Django formsets. New rows must
    // never inherit a previously entered product/price/cost from the row used
    // as the visual template. Number fields follow the Production Order form
    // convention and start at 0. A newly-added row can also be removed safely
    // without changing Django's form indexes.
    function markExistingFormsetRowDeleted(button) {
      const row = button?.closest('tr');
      if (!row) return;
      const deleteInput = row.querySelector('input[name$="-DELETE"]');
      if (!deleteInput) return;
      deleteInput.checked = true;
      // Keep the row's saved ID and values enabled so Django can identify the
      // record being removed; DELETE tells the formset to ignore the values.
      row.classList.add('hidden');
    }

    function addFormsetRow(prefix, containerId) {
      const totalForms = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
      const container = document.getElementById(containerId);
      if (!totalForms || !container) return null;

      const formCount = parseInt(totalForms.value || '0', 10);
      const template = container.querySelector('[data-formset-row]');
      if (!template) return null;

      const newRow = template.cloneNode(true);
      newRow.removeAttribute('data-formset-row');

      newRow.querySelectorAll('input, select, textarea, label').forEach((el) => {
        ['name', 'id', 'for'].forEach((attr) => {
          const value = el.getAttribute(attr);
          if (value) el.setAttribute(attr, value.replace(/-\d+-/, `-${formCount}-`));
        });

        if (!el.matches('input, select, textarea')) return;
        if (el.type === 'checkbox' || el.type === 'radio') {
          el.checked = false;
        } else if (el.tagName === 'SELECT') {
          el.selectedIndex = 0;
        } else if (el.type === 'number') {
          el.value = el.dataset.formsetDefault ?? '0';
        } else if (el.type === 'file') {
          // Browsers do not permit setting file input values programmatically.
        } else {
          el.value = '';
        }
        el.disabled = false;
      });

      // A cloned row must be wired as a NEW row. Event-binding markers from
      // the template row would otherwise prevent dynamic price/unit handlers
      // from attaching to the clone.
      newRow.querySelectorAll('[data-wired]').forEach((el) => el.removeAttribute('data-wired'));
      newRow.removeAttribute('data-wired');

      // Do not carry calculated/display-only values or server/client errors
      // from the row used as the template.
      newRow.querySelectorAll('.price-display, .current-cost-display, .unit-display').forEach((el) => {
        el.textContent = '—';
      });
      newRow.querySelectorAll('.client-field-error').forEach((el) => el.remove());
      newRow.querySelectorAll('div.text-rose-600, span.text-rose-600').forEach((el) => {
        if (!el.querySelector('input, select, textarea')) el.remove();
      });

      const actionCell = newRow.querySelector('td:last-child');
      if (actionCell) {
        // can_delete fields are sometimes only rendered for saved rows. Ensure
        // every dynamically-added row has its own DELETE field so it can be
        // safely ignored by Django when the user removes it.
        let deleteInput = newRow.querySelector(`input[name="${prefix}-${formCount}-DELETE"]`);
        if (!deleteInput) {
          deleteInput = document.createElement('input');
          deleteInput.type = 'checkbox';
          deleteInput.name = `${prefix}-${formCount}-DELETE`;
          deleteInput.id = `id_${prefix}-${formCount}-DELETE`;
          actionCell.appendChild(deleteInput);
        }
        deleteInput.checked = false;
        deleteInput.classList.add('hidden');
        const deleteLabel = deleteInput.closest('label');
        if (deleteLabel) deleteLabel.classList.add('hidden');

        actionCell.querySelectorAll('[data-remove-new-formset-row], [data-existing-formset-remove]').forEach((el) => el.remove());
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.dataset.removeNewFormsetRow = '1';
        removeButton.className = 'px-2 py-1 rounded text-xs text-rose-700 hover:bg-rose-50';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => {
          deleteInput.checked = true;
          newRow.querySelectorAll('input, select, textarea, button').forEach((control) => {
            if (control !== deleteInput && control !== removeButton) control.disabled = true;
          });
          newRow.classList.add('hidden');
        });
        actionCell.appendChild(removeButton);
      }

      container.appendChild(newRow);
      totalForms.value = String(formCount + 1);
      return newRow;
    }
  
