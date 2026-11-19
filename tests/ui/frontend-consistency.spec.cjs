const {test, expect} = require('@playwright/test');
test.use({serviceWorkers:'block'});
test.setTimeout(300000);

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('inprofic:essential-privacy-notice:v1', 'dismissed'));
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.goto('/accounts/login/ui-fixture/admin/');
});

test('all distribution form actions stay on the list, with stable seamless dialogs', async ({page}) => {
  await page.goto('/inventory/market-stock/');
  const origin = page.url();
  for (const name of ['Staff / gifting issue', 'Sell in Physical Store', 'Sell online', 'Release to customer', 'Transfer to Physical Store', 'Record return']) {
    const link = page.getByRole('link', {name, exact:true});
    const href = await link.getAttribute('href');
    let release;
    const match = url => url.pathname === new URL(href, origin).pathname;
    await page.route(match, async route => { await new Promise(resolve => { release = resolve; }); await route.continue(); });
    await link.click();
    const dialog = page.locator('.ui-form-dialog');
    await expect(dialog).toBeVisible();
    await expect.poll(() => !!release).toBe(true);
    const before = await dialog.boundingBox();
    release();
    const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
    await expect(frame.locator('.workspace-content form[method="post"]').first()).toBeVisible({timeout:60000});
    await expect(frame.locator('.workspace-content [data-ui-dialog-duplicate]').first()).toBeHidden();
    await expect(frame.getByRole('link', {name:'Cancel', exact:true})).toHaveCount(0);
    const stockDate = frame.locator('[data-readable-date]');
    if (await stockDate.count()) await expect(stockDate).toHaveText(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), \d{1,2} [A-Za-z]+ \d{4}$/);
    const after = await dialog.boundingBox();
    expect(after.width).toBe(before.width);
    expect(after.height).toBe(before.height);
    expect(await frame.locator('form[data-ui-form-panel]').first().evaluate(el => {
      const s = getComputedStyle(el);
      return s.borderTopWidth === '0px' && s.boxShadow === 'none' && s.backgroundColor === 'rgba(0, 0, 0, 0)';
    })).toBe(true);
    expect(page.url()).toBe(origin);
    await expect(dialog.getByRole('button', {name:'Close', exact:true})).toHaveCount(1);
    await page.setViewportSize({width:390,height:844});
    const box = await dialog.boundingBox();
    expect(box.width).toBeLessThanOrEqual(390);
    expect(box.height).toBe(844);
    expect(await frame.locator('html').evaluate(el => el.scrollWidth <= innerWidth + 1)).toBe(true);
    await dialog.getByRole('button', {name:'Close', exact:true}).click();
    await expect(dialog).toHaveCount(0);
    await expect(link).toBeFocused();
    await page.unroute(match);
    await page.setViewportSize({width:1280,height:720});
  }
  await page.screenshot({path:'test-results/frontend-distribution-desktop.png'});
});

test('weekday date controls preserve values, limits, resets and cloned rows', async ({page}) => {
  await page.goto('/inventory/market-stock/');
  const date = page.locator('input[type=date]').first();
  await expect(date.locator('..')).toHaveClass(/inprofic-date-control/);
  const initial = await date.inputValue();
  const readable = date.locator('..').locator('.inprofic-date-display');
  const dateLabel = await readable.boundingBox();
  expect(dateLabel.y + dateLabel.height).toBeLessThanOrEqual((await date.boundingBox()).y);
  await date.evaluate(el => { el.min='2026-01-01'; el.max='2026-12-31'; el.value='2026-11-18'; });
  await expect(date.locator('..').locator('.inprofic-date-display')).toHaveText('Wednesday, 18 November 2026');
  expect(await date.locator('..').evaluate(el => el.getBoundingClientRect().width)).toBeLessThanOrEqual(216);
  await date.evaluate(el => { const row = el.parentElement.cloneNode(true); row.id='cloned-date'; row.querySelector('input').id='cloned-input'; row.querySelector('input').name='cloned-date'; el.parentElement.after(row); });
  const clone = page.locator('#cloned-input');
  await expect(page.locator('#cloned-date .inprofic-date-display')).toHaveCount(1);
  await clone.fill('2026-11-19');
  await clone.blur();
  await expect(page.locator('#cloned-date .inprofic-date-display')).toHaveText('Thursday, 19 November 2026');
  await expect(date).toHaveValue('2026-11-18');
  await expect(clone).toHaveAttribute('min','2026-01-01');
  await date.evaluate(el => el.form.reset());
  await expect(date).toHaveValue(initial);
  const resetText = await date.evaluate(el => new Intl.DateTimeFormat('en-GB',{weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(new Date(el.value + 'T12:00:00')));
  await expect(date.locator('..').locator('.inprofic-date-display')).toHaveText(resetText);
  await date.evaluate(el => { const input=document.createElement('input'); input.type='datetime-local'; input.id='scheduled-time'; input.name='scheduled_time'; input.setAttribute('aria-label','Scheduled time'); input.value='2026-11-19T09:30'; el.form.append(input); });
  const scheduled=page.locator('#scheduled-time');
  await expect(scheduled.locator('..').locator('.inprofic-date-display')).toHaveText('Thursday, 19 November 2026');
  await expect(scheduled).toHaveValue('2026-11-19T09:30');
});

test('greeting and icon rail retain permitted navigation across reload and mobile', async ({page}) => {
  await expect(page.locator('main h1').first()).toHaveText(/^Hello UI, Good (Morning|Afternoon|Evening)\s*$/);
  const sidebar = page.locator('.desktop-sidebar');
  expect(await sidebar.locator('nav a').evaluateAll(links => links.every(link => !!link.querySelector('svg')))).toBe(true);
  expect(await sidebar.locator('[data-nav-category] summary').evaluateAll(items => items.every(item => !!item.querySelector('.nav-icon')))).toBe(true);
  const destinations = await sidebar.locator('nav a').count();
  await page.getByRole('button',{name:'Collapse navigation',exact:true}).click();
  await expect(page.locator('html')).toHaveAttribute('data-sidebar','collapsed');
  await expect(sidebar.locator('.sidebar-mark')).toBeVisible();
  await expect(sidebar.locator('.sidebar-wordmark')).toBeHidden();
  await expect(sidebar.locator('nav a:visible')).toHaveCount(destinations);
  await page.reload();
  await expect(page.getByRole('button',{name:'Expand navigation',exact:true})).toBeVisible();
  await page.screenshot({path:'test-results/frontend-sidebar-collapsed.png'});
  await page.setViewportSize({width:390,height:844});
  await expect(sidebar).toBeHidden();
  await page.getByRole('button',{name:'Toggle navigation',exact:true}).click();
  await expect(page.locator('#mobile-nav-panel')).toHaveAttribute('aria-hidden','false');
  await expect(page.locator('.mobile-sidebar-nav').getByRole('link',{name:'Dashboard',exact:true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('short forms remain compact on phones and business settings stays standalone', async ({page}) => {
  await page.goto('/finance/');
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('link',{name:'+ Cash account',exact:true}).click();
  const dialog=page.locator('.ui-form-dialog');
  await expect(dialog).toHaveAttribute('data-dialog-size','compact');
  const box=await dialog.boundingBox();
  expect(box.height).toBeLessThan(844);
  expect(box.width).toBeLessThan(390);
  await page.screenshot({path:'test-results/frontend-account-mobile.png'});
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  await page.setViewportSize({width:1280,height:720});
  await page.getByRole('link',{name:'Business Settings',exact:true}).first().click();
  await expect(page).toHaveURL(/business\/settings\/$/);
  await expect(dialog).toHaveCount(0);
  await expect(page.locator('#business-settings-form input[name="name"]')).toBeVisible();
});

test('inline editors in other menu workspaces use the shared body and retain their server POST', async ({page}) => {
  await page.goto('/finance/books/accounts/');
  await page.getByRole('button',{name:'Add an account',exact:true}).click();
  const dialog = page.locator('.ui-legacy-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('heading',{name:'Add an account',exact:true})).toHaveCount(1);
  await dialog.locator('input[name=code]').fill('invalid');
  await dialog.locator('input[name=name]').fill('Modal office supplies');
  await dialog.getByRole('button',{name:'Add',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('numeric code');
  await dialog.locator('input[name=code]').fill('6399');
  await dialog.locator('input[name=name]').fill('Modal office supplies');
  await dialog.getByRole('button',{name:'Add',exact:true}).click();
  await expect(page.locator('main')).toContainText('Modal office supplies');
  await page.getByRole('button',{name:'Rename: Modal office supplies',exact:true}).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('input[name=name]')).toHaveValue('Modal office supplies');
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  await expect(page.getByText('Modal office supplies',{exact:true})).toBeVisible();
  await page.goto('/finance/tax/');
  await page.getByRole('button',{name:'Edit tax settings',exact:true}).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('input[name=vat_registration_date]')).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  expect((await dialog.boundingBox()).height).toBe(844);
  await page.screenshot({path:'test-results/frontend-tax-mobile.png'});
});

test('form entry points across operational and administration menus use modals', async ({page}) => {
  for (const [workspace, formPath] of [
    ['/orders/','/orders/add/'], ['/runs/','/runs/add/'],
    ['/procurement/','/procurement/add/'], ['/sales/','/sales/add/'],
    ['/sales/customers/','/sales/customers/add/'], ['/users/','/users/add/'],
    ...(process.env.INPROFIC_UI_ALL_MODULES_FIXTURE === '1' ? [['/commerce/','/commerce/settings/'], ['/delivery/','/delivery/settings/']] : []),
  ]) {
    await page.goto(workspace);
    const link = page.locator(`a[href="${formPath}"]`).first();
    await expect(link).toBeVisible();
    await link.click();
    const dialog=page.locator('.ui-form-dialog');
    await expect(dialog).toBeVisible();
    const frame=page.frameLocator('iframe[name="inprofic-form-dialog"]');
    await expect(frame.locator('.workspace-content form[method="post"]').first()).toBeVisible({timeout:60000});
    await expect(frame.locator('.workspace-content [data-ui-dialog-duplicate]').first()).toBeHidden();
    expect(new URL(page.url()).pathname).toBe(workspace);
    await dialog.getByRole('button',{name:'Close',exact:true}).click();
    await expect(dialog).toHaveCount(0);
  }
});

test('forms become interactive at DOM readiness without waiting for unrelated images', async ({page}) => {
  await page.goto('/finance/');
  let releaseImage;
  await page.route('**/ui-delayed-image.png', route => { releaseImage = () => route.fulfill({status:204,body:''}); });
  await page.route(url => url.pathname === '/finance/accounts/add/', async route => {
    const response = await route.fetch();
    await route.fulfill({response,body:(await response.text()).replace('</body>','<img src="/ui-delayed-image.png" alt=""></body>')});
  });
  await page.getByRole('link',{name:'+ Cash account',exact:true}).click();
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(frame.locator('input[name=name]')).toBeVisible();
  await expect.poll(() => !!releaseImage).toBe(true);
  expect(await frame.locator('html').evaluate(() => document.readyState)).not.toBe('complete');
  await expect(page.getByText('Loading form…',{exact:true})).toHaveCount(0);
  await expect(frame.locator('.desktop-sidebar,#commerce-notification-dock')).toHaveCount(0);
  await releaseImage();
  await page.locator('.ui-form-dialog').getByRole('button',{name:'Close',exact:true}).click();
});

test('distribution invoice choices include short dates for returns and payments', async ({page}) => {
  await page.goto('/inventory/market-stock/');
  await page.getByRole('link',{name:'Record return',exact:true}).click();
  const frame=page.frameLocator('iframe[name="inprofic-form-dialog"]');
  const choices=frame.locator('select[name=sale_item] option:not([value=""])');
  await expect(choices.first()).toContainText(/Sale #\d+ · \d{1,2} [A-Za-z]{3} \d{4}/);
  await page.locator('.ui-form-dialog').getByRole('button',{name:'Close',exact:true}).click();
  await page.getByRole('link',{name:'Record customer payment',exact:true}).click();
  const customers=frame.locator('select[name=customer]');
  const customer=await customers.locator('option').evaluateAll(options=>options.find(option=>option.value).value);
  await customers.selectOption(customer,{force:true});
  await expect(frame.locator('select[name=sale] option:not([value=""])').first()).toContainText(/Sale #\d+ · \d{1,2} [A-Za-z]{3} \d{4}/);
});

test('production approval keeps externally associated flexible amounts inside the review modal', async ({page}) => {
  test.skip(process.env.INPROFIC_UI_ALL_MODULES_FIXTURE !== '1', 'Requires the isolated flexible-production fixture.');
  await page.goto('/orders/87001/');
  const trigger=page.getByRole('button',{name:/Approve.*release/}).first();
  await trigger.click();
  const frame=page.frameLocator('iframe[name="inprofic-form-dialog"]');
  const flexible=frame.locator('input[form="approve-order-form"]').first();
  await expect(flexible).toBeVisible();
  await flexible.fill('25');
  expect(await flexible.evaluate(input=>Array.from(input.form.elements).includes(input))).toBe(true);
  const date=frame.locator('#approve-order-form input[name=event_date]');
  const today=await date.getAttribute('max');
  await date.fill(today);
  await frame.locator('#approve-order-form').getByRole('button',{name:/Approve/}).click();
  await expect(page.locator('.ui-form-dialog')).toHaveCount(0);
  await expect(page.locator('main')).toContainText('Approved');
});
