const {test, expect} = require('@playwright/test');
test.skip(process.env.INPROFIC_UI_RELEASE_FIXTURE !== '1', 'Requires isolated market stock lots.');
test.beforeEach(async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('inprofic:essential-privacy-notice:v1', 'dismissed'));
  await page.goto('/accounts/login/ui-fixture/admin/');
});

test('issue market products to staff through the global modal and share the record', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/inventory/market-stock/');
  await page.getByRole('link', {name: 'Staff / gifting issue', exact: true}).click();
  await expect(page.locator('.ui-form-dialog')).toBeVisible();
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  const product = frame.locator('select[name="finished_good"]');
  const id = await product.locator('option').evaluateAll(options => options.find(o => o.textContent.includes('Fixture Loaf')).value);
  await product.selectOption(id, {force: true});
  await frame.locator('input[name="quantity"]').fill('9999');
  await frame.locator('input[name="recipient"]').fill('Morning team');
  await frame.getByRole('button', {name: 'Record issue', exact: true}).click();
  await expect(frame.getByRole('alert')).toContainText('Only');
  await frame.locator('input[name="quantity"]').fill('2');
  await frame.getByRole('button', {name: 'Record issue', exact: true}).click();
  await expect(page.locator('.ui-form-dialog')).toHaveCount(0);
  await expect(page.locator('#distribution-summary')).toContainText('Morning team');
  const text = await page.locator('#distribution-share').evaluate(el => JSON.parse(el.textContent));
  expect(text).toContain('Staff issue · Morning team');
  expect(text).toContain('2 loaves');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const download = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Share distribution summary', exact: true}).click();
  await page.getByRole('menuitem', {name: 'Save image', exact: true}).click();
  await (await download).saveAs('/tmp/inprofic-staff-gifting-snapshot.png');
});

test('gift physical-store products from their inventory row', async ({page}) => {
  await page.goto('/inventory/market-stock/transfer/');
  const select = page.locator('select[name="finished_good"]');
  const id = await select.locator('option').evaluateAll(options => options.find(o => o.textContent.includes('Fixture Loaf')).value);
  await select.selectOption(id, {force: true});
  await page.locator('input[name="quantity"]').fill('3');
  await page.locator('[name="reason"]').fill('Physical stock for community gifts');
  await page.locator('form').filter({has: select}).getByRole('button', {name: /Transfer/}).click();
  await page.goto('/inventory/');
  await page.locator('#finished-tab-label').click();
  await page.getByRole('link', {name: 'Staff / gifting issue · Fixture Loaf', exact: true}).click();
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(frame.locator('select[name="finished_good"]')).toHaveValue(id);
  await frame.locator('select[name="reason"]').selectOption('charity', {force: true});
  await frame.locator('input[name="quantity"]').fill('1');
  await frame.locator('input[name="recipient"]').fill('Community kitchen');
  await frame.getByRole('button', {name: 'Record issue', exact: true}).click();
  await expect(page.locator('.ui-form-dialog')).toHaveCount(0);
  await page.locator('#finished-tab-label').click();
  await page.getByRole('link', {name: 'Staff / gifting issue · Fixture Loaf', exact: true}).click();
  await expect(frame.locator('select[name="finished_good"] option:checked')).toContainText('2 loaves available');
});
