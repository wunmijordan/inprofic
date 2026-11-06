const {test,expect}=require('@playwright/test');
test.skip(process.env.INPROFIC_UI_RECEIVE_FIXTURE!=='1','Requires the isolated receiving fixture.');
test.beforeEach(async({page})=>{
 await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
 await page.goto('/accounts/login/ui-fixture/admin/');
});
test('one Receive button opens vendor dates in the shared modal',async({page})=>{
 await page.goto('/procurement/');
 await expect(page.locator('.procurement-row input[name="event_date"]')).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Receive',exact:true})).toHaveCount(1);
 await page.getByRole('button',{name:'Receive',exact:true}).click();
 const modal=page.locator('.ui-legacy-dialog');
 await expect(modal).toBeVisible();
 await expect(modal.locator('input[name="event_date"]')).toHaveCount(2);
 await expect(modal.getByRole('button',{name:'Close',exact:true})).toHaveCount(1);
 const alpha=modal.locator('section').filter({has:page.getByRole('heading',{name:'Receipt Vendor Alpha',exact:true})});
 const input=alpha.locator('input[name="event_date"]');
 const max=await input.getAttribute('max');
 const yesterday=new Date(`${max}T12:00:00Z`);yesterday.setUTCDate(yesterday.getUTCDate()-1);
 await input.fill(yesterday.toISOString().slice(0,10));
 await page.setViewportSize({width:390,height:844});
 expect(await modal.evaluate(el=>el.getBoundingClientRect().width<=innerWidth)).toBe(true);
 await alpha.getByRole('button',{name:'Receive vendor items',exact:true}).click();
 await expect(modal).not.toBeVisible();
 await page.getByRole('button',{name:'Receive',exact:true}).click();
 await expect(modal.locator('input[name="event_date"]')).toHaveCount(1);
 await expect(modal.locator('section').filter({has:page.getByRole('heading',{name:'Receipt Vendor Alpha',exact:true})})).toContainText('Received');
});
test('distribution report and snapshot use date opening and closing balances',async({page})=>{
 await page.goto('/inventory/market-stock/');
 // The seed arrives today. Select tomorrow so the shared text includes
 // non-zero opening/closing balances rather than omitting zero text lines.
 const date=await page.locator('input[name="date"]').inputValue();
 const next=new Date(`${date}T12:00:00Z`);next.setUTCDate(next.getUTCDate()+1);
 await page.goto(`/inventory/market-stock/?date=${next.toISOString().slice(0,10)}`);
 const summary=page.locator('#distribution-summary');
 await expect(summary.getByRole('columnheader',{name:'Opening balance',exact:true})).toBeVisible();
 await expect(summary.getByRole('columnheader',{name:'Closing',exact:true})).toBeVisible();
 const payload=await page.locator('#distribution-share').evaluate(el=>JSON.parse(el.textContent));
 expect(payload).toContain('Opening balance:');expect(payload).toContain('Closing balance:');
 expect(payload).not.toContain('Remaining now');
 const download=page.waitForEvent('download');
 await page.getByRole('button',{name:'Share distribution summary',exact:true}).click();
 await page.getByRole('menuitem',{name:'Save image',exact:true}).click();
 await (await download).saveAs('/tmp/inprofic-distribution-opening-closing.png');
});
