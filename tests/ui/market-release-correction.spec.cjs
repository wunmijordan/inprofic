const {test,expect}=require('@playwright/test');
test.skip(process.env.INPROFIC_UI_PORTION_FIXTURE!=='1','Requires selling-portion release fixture.');
async function choose(root,select,name){
  await select.locator('xpath=following-sibling::span[@data-ui-combo-root]').getByRole('combobox').click();
  await root.locator('.ui-combo-popover').getByRole('option',{name,exact:true}).click();
}
test('correct a multi-product release from Where stock went using the shared mobile modal',async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
  await page.goto('/accounts/login/ui-fixture/admin/');
  await page.goto('/inventory/market-stock/release/');
  await choose(page,page.locator('select[name="customer"]'),'Customer 00');
  const row=page.locator('[data-market-release-row]').first();
  await choose(page,row.locator('select[name$="-finished_good"]'),'Fixture Loaf · pack');
  await row.locator('input[name$="-quantity"]').fill('10');
  await page.getByRole('button',{name:'Release to customer',exact:true}).click();
  await expect(page).toHaveURL(/\/inventory\/market-stock\/$/);
  await page.setViewportSize({width:390,height:844});
  const href=await page.locator('#distribution-summary').getByRole('link',{name:/Edit release #/}).evaluateAll(links=>links.map(a=>a.getAttribute('href')).sort((a,b)=>Number(b.match(/releases\/(\d+)/)[1])-Number(a.match(/releases\/(\d+)/)[1]))[0]);
  const edit=page.locator(`#distribution-summary a[href="${href}"]`).first();
  await edit.click();
  await expect(page.locator('.ui-form-dialog')).toBeVisible();
  const form=page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(form.getByRole('heading',{name:/Edit customer release/})).toBeVisible();
  const quantity=form.locator('input[name$="-quantity"]').first();
  await expect.poll(async()=>Number(await quantity.inputValue())).toBe(10);
  await expect(form.locator('[data-release-stock-summary]').first()).toContainText('15 packs');
  await quantity.fill('12');
  await expect(form.locator('[data-release-stock-summary]').first()).toContainText('3 packs will remain');
  await form.locator('textarea[name="reason"]').fill('Two extra packs were released, not recorded');
  expect(await form.locator('body').evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await form.getByRole('button',{name:'Save correction',exact:true}).click();
  await expect(page.locator('.ui-form-dialog')).not.toBeVisible();
  const shared=await page.locator('#distribution-share').evaluate(el=>JSON.parse(el.textContent));
  expect(shared).toContain('12 packs');
  expect(shared).toContain('Closing balance: 3 packs');
  expect(shared).toContain('₦3,000.00');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:'/tmp/inprofic-release-correction-mobile.png'});
});
