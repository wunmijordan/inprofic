const {test,expect}=require('@playwright/test');

test.beforeEach(async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
  await page.goto('/accounts/login/ui-fixture/admin/');
  await page.goto('/dashboard/?view=simple');
});

test('nested card selectors remain open for visible desktop and mobile selections',async({page})=>{
  for (const mobile of [false,true]) {
    if(mobile) await page.setViewportSize({width:390,height:844});
    for(const metric of ['sales','revenue','profit','product','customer']) {
      await page.locator(metric==='sales'?'#dashboard-tab-overview':'#dashboard-tab-insights').click();
      const card=page.locator(`[data-dashboard-metric="${metric}"]`);
      const typography = element => {
        const css = getComputedStyle(element);
        return [css.fontFamily,css.fontSize,css.fontWeight,css.letterSpacing,css.textTransform];
      };
      const reference = await page.locator('#dashboard-panel-overview .dashboard-kpi').first().locator('.uppercase').first().evaluate(typography);
      expect(await card.locator('[data-metric-title]').evaluate(typography)).toEqual(reference);
      await card.getByRole('button',{name:/Choose/}).click();
      const parent=page.locator('.ui-filter-content');
      const combo=parent.getByRole('combobox').first();
      await combo.click();
      const choices=page.locator('.ui-combo-popover');
      await expect(choices).toBeVisible();
      await expect(parent).toBeVisible();
      const label=metric==='product'?'Fixture Loaf':metric==='customer'?'Customer 01':mobile?'Month':'Week';
      if(metric==='product' || metric==='customer') await choices.locator('[cmdk-input]').fill(label);
      await choices.getByRole('option',{name:label,exact:true}).click();
      await expect(parent).toBeVisible();
      await expect(choices).not.toBeVisible();
      await expect(combo).toContainText(label);
      await expect(card).not.toHaveAttribute('aria-busy','true');
      if(metric==='product' || metric==='customer') await expect(card.locator('[data-metric-subject-label]')).toHaveText(label);
      // Escape dismisses the child selector first and returns focus to it.
      await combo.click();
      await expect(choices).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(choices).not.toBeVisible();
      await expect(parent).toBeVisible();
      await expect(combo).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(parent).not.toBeVisible();
      if(metric==='customer') {
        await card.getByRole('button',{name:/Choose/}).click();
        await combo.click();
        await expect(choices).toBeVisible();
        await page.mouse.click(5,5);
        await expect(parent).not.toBeVisible();
        await expect(choices).not.toBeVisible();
      }
    }
  }
});

test('compact metric calendar selects periods without growing cards, including mobile',async({page})=>{
  const sales=page.locator('[data-dashboard-metric="sales"]');
  await expect(sales.locator('[data-metric-value]')).toContainText('₦');
  await expect(sales.locator('[data-metric-title]')).toHaveText("Today's sales");
  await expect(sales.locator('h3 svg')).toBeVisible();
  await expect(sales.getByText('Sales by channel')).toHaveCount(0);
  const height=await sales.evaluate(el=>el.getBoundingClientRect().height);
  const trigger=sales.getByRole('button',{name:/Choose period/});
  await trigger.click();
  const popup=page.locator('.ui-filter-content');
  await expect(popup).toBeVisible();
  expect(await sales.evaluate(el=>el.getBoundingClientRect().height)).toBe(height);
  const date=page.locator('#metric-sales-date');
  const today=await date.inputValue();
  await expect(popup.locator('[data-calendar-days] button')).not.toHaveCount(0);
  await popup.locator('[data-calendar-days] button:not(:disabled)').last().click();
  await expect(date).toHaveValue(today);
  const before=new Date(`${today}T12:00:00Z`); before.setUTCDate(before.getUTCDate()-1);
  let response=page.waitForResponse(r=>r.url().includes('/dashboard/metric/') && r.url().includes(`date=${before.toISOString().slice(0,10)}`));
  await date.fill(before.toISOString().slice(0,10));
  expect((await (await response).json()).count).toBe(0);
  await expect(sales.locator('[data-metric-title]')).toHaveText('Daily sales');
  await date.fill(today);
  for(const [period,title] of [['week','Weekly sales'],['month','Monthly sales'],['year','Yearly sales']]) {
    response=page.waitForResponse(r=>r.url().includes(`/dashboard/metric/`) && r.url().includes(`period=${period}`));
    await page.locator('#metric-sales-period').selectOption(period,{force:true});
    expect((await (await response).json()).count).toBe(3);
    await expect(sales.locator('[data-metric-title]')).toHaveText(title);
  }
  await popup.getByRole('button',{name:'Done',exact:true}).click();
  await expect(popup).not.toBeVisible();
  await page.locator('#dashboard-tab-insights').click();
  for(const metric of ['revenue','profit','product','customer']) {
    const card=page.locator(`[data-dashboard-metric="${metric}"]`);
    await card.scrollIntoViewIfNeeded();
    await expect(card.locator('[data-metric-value]')).toContainText('₦');
    await card.getByRole('button',{name:/Choose/}).click();
    if(metric==='product' || metric==='customer') {
      await expect(popup.locator('[data-metric-period],[data-metric-date],[data-metric-calendar]')).toHaveCount(0);
      await expect(card.locator('[data-metric-range]')).toHaveText('All time');
    } else {
      await page.locator(`#metric-${metric}-period`).selectOption('year',{force:true});
      await expect(card.locator('[data-metric-title]')).toContainText('Yearly');
      await expect(card.locator('[data-metric-range]')).toContainText(today.slice(0,4));
    }
    if(metric==='customer') {
      const select=page.locator('#metric-customer-subject');
      const id=await select.locator('option').evaluateAll(options=>options.find(o=>o.textContent==='Customer 01').value);
      await select.selectOption(id,{force:true});
      await expect(card.locator('[data-metric-subject-label]')).toHaveText('Customer 01');
    }
    await page.keyboard.press('Escape');
    await expect(popup).not.toBeVisible();
    await expect(card.getByRole('button',{name:/Choose/})).toBeFocused();
    expect(await card.evaluate(el=>el.getBoundingClientRect().height)).toBeLessThan(200);
  }
  await page.setViewportSize({width:390,height:844});
  const customer=page.locator('[data-dashboard-metric="customer"]');
  await customer.getByRole('button',{name:'Choose customer',exact:true}).click();
  await expect(popup).toBeVisible();
  expect(await popup.evaluate(el=>{const box=el.getBoundingClientRect(); return box.left>=0 && box.right<=innerWidth;})).toBe(true);
  await page.screenshot({path:'/tmp/inprofic-compact-metric-calendar-mobile.png'});
  await page.keyboard.press('Escape');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.locator('#dashboard-tab-overview').click();
  await sales.scrollIntoViewIfNeeded();
  await page.screenshot({path:'/tmp/inprofic-compact-sales-mobile.png'});
  expect(await sales.evaluate(el=>el.getBoundingClientRect().height)).toBeLessThan(200);
  await expect(page).toHaveURL(/\/dashboard\/\?view=simple/);
});

test('monthly and yearly financial charts keep distinct segments in light and dark themes',async({page})=>{
  await page.waitForFunction(()=>typeof Chart!=='undefined');
  // Supply nonzero chart categories through the real modal fetch boundary.
  await page.route('**/dashboard/financial-breakdown/**',async route=>{
    const response=await route.fetch();
    await route.fulfill({response,json:{...await response.json(),
      sales_by_channel:[{label:'Physical Store',value:1000},{label:'Distribution',value:2000},{label:'Online',value:500}],
      outflows:[{label:'Materials',value:500},{label:'Expenses',value:200},{label:'Payroll',value:300}]
    }});
  });
  for(const scope of ['month','year']) {
    await page.evaluate(scope=>window.dispatchEvent(new CustomEvent('financial-scope',{detail:scope})),scope);
    await expect(page.getByRole('heading',{name:scope==='month'?'Month-to-date Financial Summary':'Year-to-date Financial Summary',exact:true})).toBeVisible();
    await page.waitForFunction(scope=>{
      const state=Alpine.$data(document.getElementById('financial-revenue-pie').closest('[x-data]'));
      return state.scope===scope && !state.loading && Boolean(state.data[scope]);
    },scope);
    await page.waitForFunction(()=>Boolean(document.getElementById('financial-revenue-pie')._chartInstance && document.getElementById('financial-outflow-pie')._chartInstance));
    for(const theme of ['light','dark']) {
      await page.evaluate(theme=>document.dispatchEvent(new CustomEvent('inprofic:theme',{detail:theme})),theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme',theme);
      await expect.poll(()=>page.evaluate(()=>['financial-revenue-pie','financial-outflow-pie'].map(id=>{
        const chart=document.getElementById(id)._chartInstance;
        return {segments:new Set(chart.data.datasets[0].backgroundColor).size,color:chart.options.plugins.legend.labels.color,expected:inproficChartTheme().text};
      }))).toEqual([expect.objectContaining({segments:3,color:expect.any(String),expected:expect.any(String)}),expect.objectContaining({segments:3,color:expect.any(String),expected:expect.any(String)})]);
      const labels=await page.evaluate(()=>['financial-revenue-pie','financial-outflow-pie'].map(id=>({actual:document.getElementById(id)._chartInstance.options.plugins.legend.labels.color,expected:inproficChartTheme().text})));
      labels.forEach(label=>expect(label.actual).toBe(label.expected));
    }
    await page.screenshot({path:`/tmp/inprofic-basic-${scope}-charts.png`});
  }
});
