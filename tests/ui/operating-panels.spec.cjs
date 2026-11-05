const {test, expect} = require('@playwright/test');
test.beforeEach(async ({page}) => { await page.addInitScript(() => localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed')); });
async function login(page) { await page.goto('/accounts/login/ui-fixture/admin/'); }

async function createRequest(page) {
  await page.goto('/procurement/');
  await page.locator('a[href="/procurement/add/"]').click();
  await expect(page.locator('.ui-form-dialog')).toBeVisible();
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(page.locator('.ui-dialog-heading').getByRole('button',{name:'Share request',exact:true})).toBeVisible();
  const choice = await frame.locator('select[name="items-0-item"] option').evaluateAll(options => options.find(o => o.textContent.includes('Fixture Flour')).value);
  await frame.locator('select[name="items-0-item"]').selectOption(choice, {force:true});
  await frame.locator('input[name="items-0-qty"]').fill('2');
  await frame.locator('input[name="items-0-unit_cost"]').fill('100');
  await page.locator('.ui-dialog-heading').getByRole('button',{name:'Share request',exact:true}).click();
  await page.getByRole('menuitem',{name:'Save and share',exact:true}).click();
  await expect(frame.locator('[data-request-preview]')).toBeVisible();
  await expect(frame.locator('#purchase-request-form')).toHaveCount(0);
  await expect(page.locator('.ui-form-dialog')).toBeVisible();
}

test('assistant opens inside the responsive alert tray and preserves record-backed answers', async ({page}) => {
  await page.setViewportSize({width:390,height:844}); await login(page);
  await page.getByRole('button',{name:'Toggle navigation',exact:true}).click();
  await page.locator('#mobile-nav-panel').getByRole('button',{name:'Assistant',exact:true}).click();
  await expect(page.locator('#mobile-nav-button')).toHaveAttribute('aria-expanded','false');
  const dock = page.locator('#commerce-notification-dock');
  await expect(dock).toBeVisible();
  await expect(page.locator('[data-alert-channel="assistant"]')).toHaveAttribute('aria-pressed','true');
  const frame = page.frameLocator('[data-assistant-src]');
  await expect(frame.locator('#asst-input')).toBeVisible();
  await frame.locator('[data-quick="sales_today"]').click();
  await expect(frame.locator('.asst-msg.is-assistant').last()).toContainText('ledger');
  await page.screenshot({path:'/tmp/inprofic-session-captures/assistant-mobile.png'});
  await page.evaluate(() => document.dispatchEvent(new CustomEvent('inprofic:theme',{detail:'dark'})));
  await expect(frame.locator('html')).toHaveAttribute('data-theme','dark');
  await page.screenshot({path:'/tmp/inprofic-session-captures/assistant-mobile-dark.png'});
  expect(await dock.evaluate(el => el.getBoundingClientRect().right <= innerWidth)).toBeTruthy();
  await page.setViewportSize({width:1440,height:950});
  await page.locator('#commerce-notification-minimize').click();
  const currentUrl = page.url();
  await page.locator('.desktop-sidebar').getByRole('button',{name:'Assistant',exact:true}).click();
  await expect(page.locator('[data-alert-channel="assistant"]')).toHaveAttribute('aria-pressed','true');
  await expect(frame.locator('#asst-input')).toBeVisible();
  expect(page.url()).toBe(currentUrl);
});

test('Executive sections scroll with the page and accept report deep links', async ({page}) => {
  await login(page); await page.goto('/dashboard/complete/?group=sales&section=sales_summary');
  await expect(page.locator('#complete-dashboard-content h2')).toHaveText('Sales Summary');
  await expect(page.locator('[data-section="sales_summary"]')).toHaveAttribute('aria-current','page');
  expect(await page.locator('#complete-dashboard-content').evaluate(el => getComputedStyle(el).overflowY)).toBe('visible');
  await expect(page.getByRole('link',{name:'Basic',exact:true})).toBeVisible();
  await page.screenshot({path:'/tmp/inprofic-session-captures/executive-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'/tmp/inprofic-session-captures/executive-mobile.png'});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('Executive section navigation keeps the visible group dropdown synchronized', async ({page}) => {
  await login(page); await page.goto('/dashboard/complete/');
  const groups = await page.locator('#complete-dashboard-manifest').evaluate(el => JSON.parse(el.textContent));
  const [first, second] = groups;
  const lastSection = first.sections[first.sections.length - 1];
  await page.goto(`/dashboard/complete/?group=${first.key}&section=${lastSection.key}`);
  const dropdown = page.getByRole('combobox', {name:'Group', exact:true});
  const content = page.locator('#complete-dashboard-content');
  await expect(dropdown).toContainText(first.label);
  await content.getByRole('button', {name:'Next section', exact:true}).click();
  await expect(page.locator('#complete-dashboard-group')).toHaveValue(second.key);
  await expect(page.locator(`[data-section="${second.sections[0].key}"]`)).toHaveAttribute('aria-current','page');
  await expect(dropdown).toContainText(second.label);
  await page.setViewportSize({width:390,height:844});
  await content.getByRole('button', {name:'Previous section', exact:true}).click();
  await expect(page.locator('#complete-dashboard-group')).toHaveValue(first.key);
  await expect(page.locator(`[data-section="${lastSection.key}"]`)).toHaveAttribute('aria-current','page');
  await expect(dropdown).toContainText(first.label);
});

test('procurement add validates inside the modal and saves before sharing', async ({page}) => {
  await login(page); await page.goto('/procurement/');
  await createRequest(page);
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await frame.locator('[data-request-preview]').evaluate(async () => { await Promise.all(document.getAnimations().filter(a => a.playState === 'running' && a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))); });
  await page.screenshot({path:'/tmp/inprofic-session-captures/procurement-share.png',animations:'disabled'});
  const downloading = page.waitForEvent('download');
  await page.locator('.ui-dialog-heading').getByRole('button',{name:'Share request',exact:true}).click();
  await page.getByRole('menuitem',{name:'Save image',exact:true}).click();
  const file = await downloading; await file.saveAs('/tmp/inprofic-session-captures/purchase-snapshot.png');
  await page.evaluate(() => document.dispatchEvent(new CustomEvent('inprofic:theme',{detail:'dark'})));
  await expect(frame.locator('html')).toHaveAttribute('data-theme','dark');
  const darkDownload = page.waitForEvent('download');
  await page.locator('.ui-dialog-heading').getByRole('button',{name:'Share request',exact:true}).click();
  await page.getByRole('menuitem',{name:'Save image',exact:true}).click();
  await (await darkDownload).saveAs('/tmp/inprofic-session-captures/purchase-snapshot-dark.png');
});

test('procurement review stays in a modal and shows rejected quantities', async ({page}) => {
  await login(page); await page.goto('/procurement/');
  await createRequest(page); await page.goto('/procurement/');
  const review = page.getByRole('link',{name:'Review request',exact:true}).first();
  await review.click(); await expect(page.locator('.ui-form-dialog')).toBeVisible();
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(frame.getByRole('button',{name:'Approve request',exact:true})).toBeVisible();
  await expect(frame.locator('[data-snapshot-share]')).toHaveCount(0);
  await frame.locator('.pa-input').first().fill('1');
  await frame.getByRole('button',{name:'Approve request',exact:true}).click();
  await expect(frame.locator('.pa-error').first()).toContainText('reason');
  await expect(page.locator('.ui-form-dialog')).toBeVisible();
});


test('Market Stock page renders with sharing dropdowns beside each summary heading', async ({page}) => {
  await login(page); await page.goto('/inventory/market-stock/');
  await expect(page.locator('#distribution-summary')).toBeVisible();
  await expect(page.locator('#distribution-returns')).toBeVisible();
  await expect(page.getByText('Possible returns',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'Where stock went',exact:true})).toBeVisible();
  const share = page.getByRole('button',{name:'Share distribution summary',exact:true});
  await share.click(); await expect(page.getByRole('menuitem',{name:'Copy text',exact:true})).toBeVisible();
  await page.keyboard.press('Escape'); await expect(share).toBeFocused();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Share recorded returns',exact:true}).click();
  await expect(page.getByRole('menuitem',{name:'Save image',exact:true})).toBeVisible();
  const menu = await page.getByRole('menu').boundingBox(); expect(menu.x + menu.width).toBeLessThanOrEqual(390);
});


test('list sharing opens a details preview and editing keeps sharing in the modal heading', async ({page}) => {
  await login(page); await createRequest(page); await page.goto('/procurement/');
  await page.getByRole('link',{name:'Share request',exact:true}).first().click();
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(frame.locator('[data-request-preview]')).toBeVisible();
  await expect(frame.locator('#purchase-request-form,fieldset')).toHaveCount(0);
  const close = page.locator('.ui-form-dialog').getByRole('button',{name:'Close',exact:true});
  await expect(close).toBeVisible();
  expect(await close.evaluate(el => getComputedStyle(el).color)).toBe(await page.locator('.ui-form-dialog').evaluate(el => getComputedStyle(el).color));
  await page.screenshot({path:'/tmp/inprofic-session-captures/procurement-preview-list.png',animations:'disabled'});
  await page.locator('.ui-dialog-heading').getByRole('button',{name:'Share request',exact:true}).click();
  await expect(page.getByRole('menuitem',{name:'Save and share',exact:true})).toHaveCount(0);
  await expect(page.getByRole('menuitem',{name:'Copy text',exact:true})).toBeVisible();
  await page.keyboard.press('Escape');
  await page.goto('/procurement/');
  await page.getByRole('link',{name:'Edit request',exact:true}).first().click();
  await expect(frame.locator('#purchase-request-form')).toBeVisible();
  await page.locator('.ui-dialog-heading').getByRole('button',{name:'Share request',exact:true}).click();
  await expect(page.getByRole('menuitem',{name:'Save and share',exact:true})).toBeVisible();
  await expect(page.getByRole('menuitem',{name:'Share snapshot',exact:true})).toBeVisible();
  await page.keyboard.press('Escape');
  await page.locator('.ui-form-dialog').getByRole('button',{name:'Close',exact:true}).click();
  await expect(page.locator('.ui-form-dialog')).not.toBeVisible();
});

test('global switches retain their track and use an accent thumb when active', async ({page}) => {
  await login(page); await page.goto('/inventory/product/add/');
  await page.emulateMedia({reducedMotion:'reduce'});
  const input = page.locator('#portion-config-section .cf-switch input[type="checkbox"]:enabled').first();
  const switch_ = input.locator('..');
  const track = switch_.locator('.cf-switch-track'), thumb = switch_.locator('.cf-switch-thumb');
  for (const mode of ['light','dark']) {
    await page.evaluate(mode => document.dispatchEvent(new CustomEvent('inprofic:theme',{detail:mode})),mode);
    await input.uncheck({force:true});
    const inactive = await track.evaluate(el => getComputedStyle(el).backgroundColor);
    const offThumb = await thumb.evaluate(el => getComputedStyle(el).backgroundColor);
    const offIcon = await thumb.evaluate(el => getComputedStyle(el,'::after').maskImage);
    expect(offIcon).not.toBe('none');
    await input.focus(); await page.keyboard.press('Space'); await expect(input).toBeChecked();
    expect(await track.evaluate(el => getComputedStyle(el).backgroundColor)).toBe(inactive);
    await expect.poll(() => thumb.evaluate(el => getComputedStyle(el).backgroundColor)).not.toBe(offThumb);
    expect(await thumb.evaluate(el => getComputedStyle(el,'::after').maskImage)).not.toBe(offIcon);
    await page.screenshot({path:`/tmp/inprofic-session-captures/switch-${mode}.png`,animations:'disabled'});
  }
});

test('Daily Brief, Weekly Summary and distribution snapshots all download paper-style PNGs', async ({page}) => {
  await login(page);
  for (const [route, filename, label] of [
    ['/brief/', 'daily-brief', null],
    ['/brief/?view=weekly', 'weekly-summary', null],
    ['/inventory/market-stock/', 'distribution', 'Share distribution summary'],
    ['/inventory/market-stock/', 'recorded-returns', 'Share recorded returns'],
  ]) {
    await page.goto(route);
    const download = page.waitForEvent('download');
    if (label) {
      await page.getByRole('button',{name:label,exact:true}).click();
      await page.getByRole('menuitem',{name:'Save image',exact:true}).click();
    } else await page.getByRole('button',{name:'Save image',exact:true}).click();
    await (await download).saveAs(`/tmp/inprofic-session-captures/${filename}-paper.png`);
  }
});

test('full executive report text is identical in image sharing and Copy text', async ({page}) => {
  await login(page);
  for (const [route, name] of [['/brief/', 'daily-executive'], ['/brief/?view=weekly', 'weekly-executive']]) {
    await page.goto(route);
    await expect(page.locator('#brief-text')).toContainText('Full business report');
    const expected = await page.locator('#brief-share').evaluate(el => JSON.parse(el.textContent));
    for (const section of ['Material stock', 'Product stock', 'Sales activity', 'Profit and loss', 'Cash movement', 'Purchasing', 'Production']) expect(expected).toContain(section);
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'canShare', {configurable:true, value:() => true});
      Object.defineProperty(navigator, 'share', {configurable:true, value:async payload => {
        window.__sharedReport = {text:payload.text, files:payload.files.map(file => ({type:file.type, size:file.size}))};
      }});
      Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:async text => { window.__copiedReport = text; }}});
    });
    await page.getByRole('button',{name:'Share snapshot',exact:true}).click();
    await expect.poll(() => page.evaluate(() => window.__sharedReport?.text), {timeout:60000}).toBe(expected);
    expect(await page.evaluate(() => window.__sharedReport.files[0].type)).toBe('image/png');
    expect(await page.evaluate(() => window.__sharedReport.files[0].size)).toBeGreaterThan(1000);
    await page.getByRole('button',{name:'Copy text',exact:true}).click();
    expect(await page.evaluate(() => window.__copiedReport)).toBe(expected);
    const downloading = page.waitForEvent('download');
    await page.getByRole('button',{name:'Save image',exact:true}).click();
    await (await downloading).saveAs(`/tmp/inprofic-session-captures/${name}.png`);
    await page.setViewportSize({width:390,height:844});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});

test('report section switches save daily choices independently from weekly', async ({page}) => {
  await login(page); await page.goto('/brief/');
  await page.getByText('Choose report contents', {exact:true}).click();
  const form = page.locator('form').filter({has:page.locator('input[name="action"][value="report_sections"]')});
  const switches = form.locator('.cf-switch input[type="checkbox"]');
  expect(await switches.count()).toBeGreaterThan(5);
  await switches.evaluateAll(inputs => inputs.forEach(input => { input.checked = input.value === 'products'; }));
  await form.getByRole('button', {name:'Save report contents',exact:true}).click();
  const shared = await page.locator('#brief-share').evaluate(el => JSON.parse(el.textContent));
  expect(shared).toContain('Product stock');
  expect(shared).not.toContain('Profit and loss');
  expect(shared).not.toContain('Cash movement');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/inprofic-session-captures/brief-selections-mobile.png'});
  await page.goto('/brief/?view=weekly');
  const weekly = await page.locator('#brief-share').evaluate(el => JSON.parse(el.textContent));
  expect(weekly).toContain('Profit and loss');
  // Restore the shared fixture user's defaults for other tests.
  await page.goto('/brief/'); await page.getByText('Choose report contents', {exact:true}).click();
  await page.locator('input[name="section"]').evaluateAll(inputs => inputs.forEach(input => { input.checked = true; }));
  await page.locator('form').filter({has:page.locator('input[name="action"][value="report_sections"]')}).getByRole('button', {name:'Save report contents',exact:true}).click();
});

test('post-distribution review stays within the mobile page and shares financial analysis', async ({page}) => {
  await login(page); await page.goto('/inventory/market-stock/');
  await expect(page.getByRole('heading',{name:'Post-distribution review',exact:true})).toBeVisible();
  await expect(page.getByText('Invoice outstanding', {exact:true}).first()).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await page.getByText('Review by customer and invoice', {exact:true}).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/inprofic-session-captures/distribution-review-mobile.png',fullPage:true});
  const payload = await page.locator('#distribution-share').evaluate(el => JSON.parse(el.textContent));
  expect(payload).toContain('Expected value after returns');
  expect(payload).toContain('Expected balance');
  expect(payload).toContain('Invoice outstanding');
});
