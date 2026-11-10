/* Basic dashboard metric selectors. The server owns scoping and calculations. */
document.addEventListener('DOMContentLoaded', () => {
  const periods = {day:'Daily',week:'Weekly',month:'Monthly',quarter:'Quarterly',year:'Yearly'};
  const metrics = {sales:'sales',revenue:'revenue',profit:'profit & loss',product:'product revenue',customer:'customer revenue'};
  document.querySelectorAll('[data-dashboard-metric][data-metric-url]').forEach(card => {
    const metric = card.dataset.dashboardMetric;
    // The shared popover moves these fields into a portal; retain their nodes.
    const period = document.getElementById(`metric-${metric}-period`);
    const date = document.getElementById(`metric-${metric}-date`);
    const subject = document.getElementById(`metric-${metric}-subject`);
    const status = card.querySelector('[data-metric-status]');
    const value = card.querySelector('[data-metric-value]');
    const range = card.querySelector('[data-metric-range]');
    const title = card.querySelector('[data-metric-title]');
    const subjectLabel = card.querySelector('[data-metric-subject-label]');
    const dateLabel = document.querySelector(`#metric-${metric}-filters [data-metric-date-label]`);
    const calendar = document.querySelector(`#metric-${metric}-filters [data-metric-calendar]`);
    const parseDate = iso => new Date(`${iso}T12:00:00`);
    const isoDate = day => `${String(day.getFullYear()).padStart(4,'0')}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`;
    let month = date ? parseDate(date.value) : null;
    const renderCalendar = () => {
      if (!calendar) return;
      const selected = parseDate(date.value || date.max);
      const first = new Date(month); first.setDate(1);
      const last = new Date(first); last.setMonth(first.getMonth()+1); last.setDate(0);
      const end = last.getDate();
      const grid = calendar.querySelector('[data-calendar-days]');
      calendar.querySelector('[data-calendar-month]').textContent = new Intl.DateTimeFormat(undefined,{month:'long',year:'numeric'}).format(first);
      calendar.querySelector('[data-calendar-next]').disabled = first.getFullYear() === parseDate(date.max).getFullYear() && first.getMonth() === parseDate(date.max).getMonth();
      calendar.querySelector('[data-calendar-previous]').disabled = first.getFullYear() <= 1 && first.getMonth() === 0;
      grid.replaceChildren();
      for(let i=0;i<7;i++) {
        const label = document.createElement('span');
        label.textContent = new Intl.DateTimeFormat(undefined,{weekday:'narrow'}).format(new Date(2026,0,5+i));
        grid.append(label);
      }
      const weekStart = new Date(selected); weekStart.setDate(selected.getDate()-(selected.getDay()+6)%7);
      const weekEnd = new Date(weekStart); weekEnd.setDate(weekStart.getDate()+6);
      for(let day=1;day<=end;day++) {
        const current = new Date(first); current.setDate(day);
        const iso = isoDate(current), button = document.createElement('button');
        button.dataset.calendarDate = iso;
        button.type = 'button'; button.textContent = day;
        if(day===1) button.style.gridColumnStart = (first.getDay()+6)%7+1;
        button.disabled = iso > date.max;
        button.setAttribute('aria-label',new Intl.DateTimeFormat(undefined,{dateStyle:'full'}).format(current));
        button.setAttribute('aria-pressed',String(iso===date.value));
        const sameYear = current.getFullYear()===selected.getFullYear();
        button.dataset.inPeriod = String(period.value==='week' ? current>=weekStart && current<=weekEnd
          : period.value==='month' ? sameYear && current.getMonth()===selected.getMonth()
          : period.value==='quarter' ? sameYear && Math.floor(current.getMonth()/3)===Math.floor(selected.getMonth()/3)
          : period.value==='year' ? sameYear : iso===date.value);
        button.addEventListener('click',()=>{
          date.value=iso; date.dispatchEvent(new Event('change',{bubbles:true}));
          calendar.querySelector(`[data-calendar-date="${iso}"]`)?.focus();
        });
        grid.append(button);
      }
    };
    calendar?.querySelector('[data-calendar-previous]').addEventListener('click',()=>{month.setDate(1);month.setMonth(month.getMonth()-1);renderCalendar();});
    calendar?.querySelector('[data-calendar-next]').addEventListener('click',()=>{month.setDate(1);month.setMonth(month.getMonth()+1);renderCalendar();});
    renderCalendar();
    let controller, sequence = 0;
    const load = async () => {
      const current = ++sequence;
      controller?.abort();
      if(date?.value && date.validity.valid) { month=parseDate(date.value);renderCalendar(); }
      if (dateLabel) dateLabel.textContent = period.value === 'day' ? 'Date' : 'Date in period';
      range.textContent = ''; value.textContent = '—';
      if ((date && (!date.value || !date.validity.valid)) || (subject && !subject.value)) {
        status.textContent = subject && !subject.value ? 'No records available to select.' : 'Choose a valid date.';
        status.classList.remove('sr-only');
        card.removeAttribute('aria-busy');
        return;
      }
      controller = new AbortController();
      status.textContent = 'Loading…'; status.classList.add('sr-only');
      card.setAttribute('aria-busy','true');
      const url = new URL(card.dataset.metricUrl, location.origin);
      url.search = new URLSearchParams({metric, ...(period ? {period:period.value, date:date.value} : {}), ...(subject ? {subject:subject.value} : {})});
      try {
        const response = await fetch(url, {signal:controller.signal, headers:{'Accept':'application/json'}});
        if (!response.ok) throw new Error('Summary unavailable');
        const data = await response.json();
        if (current !== sequence) return;
        const money = amount => `${Number(amount)<0 ? '-' : ''}${data.currency}${Math.abs(Number(amount || 0)).toLocaleString('en-NG',{minimumFractionDigits:2,maximumFractionDigits:2})}`;
        title.textContent = !period ? metrics[metric][0].toUpperCase()+metrics[metric].slice(1)
          : metric === 'sales' && period.value === 'day' && date.value === date.max
            ? "Today's sales" : `${periods[period.value]} ${metrics[metric]}`;
        range.textContent = data.label + (data.in_progress ? ' · to date' : '') + (data.scope ? ' · '+data.scope : '');
        value.textContent = money(data.value);
        subjectLabel.hidden = !subject;
        subjectLabel.textContent = subject?.selectedOptions[0]?.textContent || '';
        subjectLabel.title = subjectLabel.textContent;
        status.textContent = '';
      } catch (error) {
        if (error.name !== 'AbortError' && current === sequence) {
          status.textContent = 'Unable to load. Change the selection to retry.';
          status.classList.remove('sr-only'); value.textContent = '—';
        }
      } finally {
        if (current === sequence) card.removeAttribute('aria-busy');
      }
    };
    [period,date,subject].filter(Boolean).forEach(control => control.addEventListener('change', load));
    // Insights remain lazy while their tab is hidden.
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); load(); }
    });
    observer.observe(card);
  });
});
