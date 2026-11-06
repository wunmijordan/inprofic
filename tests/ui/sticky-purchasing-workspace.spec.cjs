const {test,expect}=require('@playwright/test');
test.use({viewport:{width:390,height:844}});
test.beforeEach(async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
  await page.goto('/accounts/login/ui-fixture/admin/');
});
test('mobile operational tables keep a bounded sticky first column and expose later columns',async({page})=>{
  for(const path of ['/inventory/market-stock/','/procurement/']){
    await page.goto(path);
    if(path==='/procurement/')await page.getByText('Supplier directory',{exact:true}).click();
    const tables=page.locator('main table.sticky-table');
    await expect(tables.first()).toBeVisible();
    let checked=0;
    for(const table of await tables.all()){
      const cell=table.locator('tbody tr > td:first-child:not([colspan])').first();
      if(!await cell.count() || !await cell.isVisible())continue;
      const result=await cell.evaluate(cell=>{
        const frame=cell.closest('.sticky-table-frame');
        const before=cell.getBoundingClientRect();
        frame.scrollLeft=frame.scrollWidth;
        const after=cell.getBoundingClientRect();
        const last=cell.parentElement.lastElementChild.getBoundingClientRect();
        return {position:getComputedStyle(cell).position,width:after.width,limit:Math.min(innerWidth*.38,172.8),
          stays:Math.abs(before.left-after.left),scrolled:frame.scrollLeft,
          lastVisible:last.right<=frame.getBoundingClientRect().right+2 && last.right>after.right};
      });
      expect(result.position).toBe('sticky');
      expect(result.width).toBeLessThanOrEqual(result.limit+2);
      expect(result.stays).toBeLessThanOrEqual(2);
      if(result.scrolled>0)expect(result.lastVisible).toBe(true);
      checked++;
    }
    expect(checked).toBeGreaterThan(0);
    for(const cell of await page.locator('main table.sticky-table tbody td[colspan]').all()){
      expect(await cell.evaluate(el=>getComputedStyle(el).position)).not.toBe('sticky');
    }
  }
  await page.getByText('Supplier directory',{exact:true}).click();
  await page.locator('tr.procurement-row').first().scrollIntoViewIfNeeded();
  await page.screenshot({path:'/tmp/inprofic-sticky-purchasing-mobile.png'});
});
test('general-use expenses save from the purchasing workspace and join its shared period text',async({page})=>{
  await page.goto('/procurement/');
  await page.getByRole('link',{name:'+ General-use expense',exact:true}).click();
  const dialog=page.locator('.ui-form-dialog');
  await expect(dialog).toBeVisible();
  const frame=page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await frame.locator('[name="expenses-0-description"]').fill('Combined workspace transport');
  await frame.locator('[name="expenses-0-amount"]').fill('40');
  await frame.locator('[name="expenses-0-payment_status"]').selectOption('unpaid',{force:true});
  await frame.getByRole('button',{name:'Save expenses',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('main table tbody tr').filter({hasText:'Combined workspace transport'})).toHaveCount(1);
  const text=await page.locator('script#spending-share').textContent();
  expect(text).toContain('Combined workspace transport');
  expect(text).toContain('Grand total:');
  await expect(page.getByRole('button',{name:'Share period',exact:true})).toBeVisible();
});
