const {test,expect}=require('@playwright/test');
test.beforeEach(async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
  await page.goto('/accounts/login/ui-fixture/admin/');
});
test('Inventory product details share a responsive modal and retain period filtering',async({page})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/inventory/');
  await page.locator('#finished-tab-label').click();
  await page.getByRole('link',{name:'View product Fixture Loaf',exact:true}).first().click();
  const dialog=page.locator('.ui-form-dialog');
  await expect(dialog).toBeVisible();
  const frame=page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(frame.getByRole('heading',{name:'Fixture Loaf',exact:true})).toBeVisible();
  await expect(frame.locator('#product-prices')).toContainText('Current selling prices');
  await expect(frame.locator('#stock-history-title')).toHaveText('Stock history and workflow');
  const end=await frame.locator('input[name="end"]').inputValue();
  await frame.locator('input[name="start"]').fill(end);
  await Promise.all([
    page.frame({name:'inprofic-form-dialog'}).waitForURL(url => url.searchParams.get('start') === end && url.searchParams.get('end') === end, {waitUntil:'load'}),
    frame.getByRole('button',{name:'Show period',exact:true}).click(),
  ]);
  await expect(frame.locator('input[name="start"]')).toHaveValue(end);
  await frame.locator('html').evaluate(async () => {
    if (document.activeViewTransition) await document.activeViewTransition.finished.catch(() => {});
    await Promise.all(document.getAnimations().filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})));
  });
  await page.setViewportSize({width:390,height:844});
  expect(await dialog.evaluate(el=>el.getBoundingClientRect().width<=innerWidth+1)).toBe(true);
  await expect(frame.locator('#stock-history-error')).not.toBeVisible();
  await page.screenshot({path:'/tmp/inprofic-product-review-mobile.png'});
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
  expect(errors).toEqual([]);
});
test('dashboard product search opens the same product detail modal',async({page})=>{
  await page.goto('/dashboard/');
  await page.locator('[data-dashboard-search] input[type="search"]').fill('Loaf');
  await page.locator('.dashboard-search-result').filter({hasText:'Loaf'}).first().click();
  await expect(page.locator('.ui-form-dialog')).toBeVisible();
  const frame=page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(frame.getByRole('heading',{name:'Fixture Loaf',exact:true})).toBeVisible();
  await expect(frame.locator('#product-prices')).toContainText('Current selling prices');
});
test('saving a special price keeps the historical review layer open before any correction',async({page})=>{
  await page.goto('/sales/customers/');
  const row=page.locator('tr').filter({has:page.getByRole('link',{name:'View activity for Customer 00',exact:true})});
  await row.getByRole('link',{name:'Edit',exact:true}).click();
  const dialog=page.locator('.ui-form-dialog');
  await expect(dialog).toBeVisible();
  const frame=page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await frame.locator('input[name="prices-0-price"]').fill('190');
  await frame.getByRole('button',{name:'Save Customer',exact:true}).click();
  await expect(frame.getByRole('heading',{name:'Correct distribution prices — Customer 00',exact:true})).toBeVisible();
  await expect(dialog).toBeVisible();
  await expect(frame.getByRole('link',{name:'All existing distributions',exact:true})).toBeVisible();
  await expect(frame.getByRole('button',{name:'Apply reviewed correction',exact:true})).toBeVisible();
});
