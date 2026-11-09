const {test, expect} = require('@playwright/test');

test('expense dialog title stays concise before and after validation in both themes', async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('inprofic:essential-privacy-notice:v1', 'dismissed'));
  await page.goto('/accounts/login/ui-fixture/admin/');
  for (const theme of ['light', 'dark']) {
    await page.goto('/procurement/');
    await page.evaluate(theme => document.dispatchEvent(new CustomEvent('inprofic:theme', {detail: theme})), theme);
    await page.getByRole('link', {name: 'Record general-use expenses', exact: true}).click();
    const title = page.locator('.ui-dialog-heading h2');
    await expect(title).toHaveText('Record general-use expenses');
    const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
    await expect(frame.locator('.workspace-content h2').first()).toContainText('Record general-use expenses');
    await expect(title).toHaveText('Record general-use expenses');
    await expect(title).not.toContainText('These entries');
    await frame.locator('.workspace-content form[method="post"]').evaluate(form => { form.noValidate = true; form.submit(); });
    await expect(frame.locator('.expense-field-error,.errorlist').first()).toBeVisible();
    await expect(title).toHaveText('Record general-use expenses');
    await page.setViewportSize({width:390,height:844});
    expect(await title.evaluate(el => el.getBoundingClientRect().right <= innerWidth)).toBe(true);
    await page.locator('.ui-form-dialog').getByRole('button', {name: 'Close', exact: true}).click();
    await expect(page.locator('.ui-form-dialog')).toHaveCount(0);
  }
});
