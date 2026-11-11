const {test,expect}=require('@playwright/test');
test.skip(process.env.INPROFIC_UI_PORTION_FIXTURE!=='1','Requires selling-portion fixture.');
async function choose(page,select,name) {
  await select.locator('xpath=following-sibling::span[@data-ui-combo-root]').getByRole('combobox').click();
  await page.locator('.ui-combo-popover').getByRole('option',{name,exact:true}).click();
}
test('release uses packs in selection, availability, prices and shared quantities',async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
  await page.goto('/accounts/login/ui-fixture/admin/');
  await page.goto('/inventory/market-stock/release/');
  await choose(page,page.locator('select[name="customer"]'),'Customer 00');
  const row=page.locator('[data-market-release-row]').first();
  await choose(page,row.locator('select[name$="-finished_good"]'),'Fixture Loaf · pack');
  await row.locator('input[name$="-quantity"]').fill('10');
  const summary=row.locator('[data-release-stock-summary]');
  await expect(summary).toContainText('15 packs');
  await expect(summary).toContainText('10 packs');
  await expect(summary).toContainText('5 packs will remain');
  await expect(summary).toContainText('₦2,500.00');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.getByRole('button',{name:'Release to customer',exact:true}).click();
  await expect(page).toHaveURL(/\/inventory\/market-stock\/$/);
  const shared=await page.locator('#distribution-share').evaluate(el=>JSON.parse(el.textContent));
  expect(shared).toContain('10 packs × ₦250.00 per pack');
  expect(shared).toContain('Closing balance: 5 packs');
  await page.goto('/orders/add/');
  const production=page.locator('#order-rows tr[data-row]').first();
  await choose(page,production.locator('select[name$="-finished_good"]'),'Fixture Loaf · pack');
  await production.locator('input[name$="-piece_qty"]').fill('10');
  await expect(production.locator('.price-display')).toContainText('/ pack');
  await page.getByRole('button',{name:'+ Add Product',exact:true}).click();
  const second=page.locator('#order-rows tr[data-row]').nth(1);
  await choose(page,second.locator('select[name$="-finished_good"]'),'Fixture Loaf · pack');
  await expect(second.locator('.price-display')).toContainText('/ pack');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:'/tmp/inprofic-selling-portions-production.png'});
});
