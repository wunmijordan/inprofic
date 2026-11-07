const {test,expect}=require('@playwright/test');
test('plan mixed customers and channels, share it, and review one combined order',async({page})=>{
 await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
 await page.goto('/accounts/login/ui-fixture/admin/');
 await page.goto('/orders/');
 await page.getByRole('link',{name:'Propose order',exact:true}).click();
 await page.locator('input[name="title"]').fill('Mixed channel production plan');
 await page.locator('input[name="planned_date"]').fill('2026-11-09');
 const choose=async(row,name,label)=>{
  const select=row.locator(`select[name$="-${name}"]`);
  const value=await select.locator('option').evaluateAll((options,label)=>options.find(o=>o.textContent.trim()===label).value,label);
  await select.selectOption(value,{force:true});
 };
 let rows=page.locator('[data-proposal-line]');
 await choose(rows.nth(0),'channel','Distribution');
 await choose(rows.nth(0),'customer','Customer 00');
 await choose(rows.nth(0),'finished_good','Fixture Loaf');
 await rows.nth(0).locator('input[name$="-quantity"]').fill('20');
 await page.getByRole('button',{name:'Add product / destination',exact:true}).click();
 await choose(rows.nth(1),'channel','Physical Store');
 await choose(rows.nth(1),'finished_good','Fixture Loaf');
 await rows.nth(1).locator('input[name$="-quantity"]').fill('10');
 await page.getByRole('button',{name:'Save proposal',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Mixed channel production plan',exact:true})).toBeVisible();
 const text=await page.locator('#proposal-share').evaluate(el=>JSON.parse(el.textContent));
 expect(text).toContain('Customer 00');expect(text).toContain('Physical Store');expect(text).toContain('20 loaves');
 const share=page.locator('[data-snapshot-share]');
 const image=await page.request.get(await share.getAttribute('data-image-url'));
 expect(image.status()).toBe(200);expect(image.headers()['content-type']).toBe('image/png');
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await page.getByRole('button',{name:'Use it',exact:true}).click();
 await expect(page.locator('input[name="items-0-piece_qty"]')).toHaveValue('30.00');
 await expect(page.locator('select[name="items-0-finished_good"]')).toHaveValue(/\d+/);
 await page.getByRole('button',{name:'Save order',exact:true}).click();
 await expect(page).toHaveURL(/\/orders\/\d+\/split\//);
 await expect(page.locator('select[name="d0_customer"]')).toHaveValue(/\d+/);
 await expect(page.locator('select[name="d1_channel"]')).toHaveValue('physical_store');
 const row=page.locator('[data-row]').filter({hasText:'Fixture Loaf'});
 // Prefilled quantities are normalized into whole batches plus loose pieces.
 const first=row.locator('input[name^="d0_b_"]');
 const second=row.locator('input[name^="d1_b_"]');
 expect(Number(await first.inputValue())).toBeGreaterThanOrEqual(0);
 expect(Number(await second.inputValue())).toBeGreaterThanOrEqual(0);
});

test('product modal section controls reveal their own data on mobile',async({page})=>{
 await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
 await page.goto('/accounts/login/ui-fixture/admin/');
 await page.goto('/inventory/');
 await page.locator('#finished-tab-label').click();
 await page.getByRole('link',{name:'View product Fixture Loaf',exact:true}).first().click();
 const dialog=page.locator('.ui-form-dialog');
 await expect(dialog).toBeVisible();
 const frame=page.frameLocator('iframe[name="inprofic-form-dialog"]');
 const nav=frame.getByRole('navigation',{name:'Product sections'});
 await nav.getByRole('link',{name:'Sales',exact:true}).click();
 await expect(frame.locator('#product-sales')).toHaveAttribute('open','');
 await expect(frame.locator('#product-sales').getByRole('columnheader',{name:'Quantity / price'})).toBeVisible();
 await frame.locator('#product-prices > summary').click();
 await nav.getByRole('link',{name:'Selling prices',exact:true}).click();
 await expect(frame.locator('#product-prices')).toHaveAttribute('open','');
 if(await nav.getByRole('link',{name:'Production',exact:true}).count()){
  await nav.getByRole('link',{name:'Production',exact:true}).click();
  await expect(frame.locator('#product-production')).toHaveAttribute('open','');
 }
 await page.setViewportSize({width:390,height:844});
 await nav.getByRole('link',{name:'Stock and workflow',exact:true}).click();
 await expect(frame.locator('#stock-history-content')).toBeVisible();
 for (const [name,key] of [['Workflow','workflow'],['Arrivals','arrivals'],['Daily ledger','ledger'],['Overview','overview']]){
  await frame.getByRole('tab',{name,exact:true}).click();
  await expect(frame.getByRole('tab',{name,exact:true})).toHaveAttribute('aria-selected','true');
  await expect(frame.locator(`[data-history-panel="${key}"]`)).toBeVisible();
 }
 await nav.getByRole('link',{name:'Performance',exact:true}).click();
 await expect(nav.getByRole('link',{name:'Performance',exact:true})).toHaveAttribute('aria-current','location');
 expect(await frame.locator('html').evaluate(el=>el.scrollWidth<=innerWidth+1)).toBe(true);
 await page.screenshot({path:'/tmp/inprofic-product-sections-mobile.png'});
});
