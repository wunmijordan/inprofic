const {test,expect}=require('@playwright/test');
test.skip(process.env.INPROFIC_UI_OUTFLOW_FIXTURE!=='1','Requires the isolated outflow fixture.');
test.beforeEach(async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
  await page.goto('/accounts/login/ui-fixture/admin/');
});
test('customer period invoice uses the storefront document and prints the selected sales',async({page})=>{
  await page.goto('/inventory/market-stock/');
  const day=await page.locator('input[name=date]').inputValue();
  await page.getByRole('link',{name:'View activity for Customer 00',exact:true}).first().click();
  const frame=page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(frame.locator('input[type=date][name=start]')).toHaveValue(day);
  await expect(frame.locator('input[name=include_outstanding]')).not.toBeChecked();
  const popup=page.waitForEvent('popup');
  await frame.getByRole('button',{name:'Invoice this period',exact:true}).click();
  const invoice=await popup;
  await expect(invoice.locator('.receipt-card')).toBeVisible();
  expect(new URL(invoice.url()).searchParams.get('start')).toBe(day);
  expect(new URL(invoice.url()).searchParams.get('end')).toBe(day);
  await expect(invoice.locator('[data-document-customer]')).toHaveText('Customer 00');
  const fullDate = await invoice.evaluate(value => {
    const [year,month,day]=value.split('-').map(Number);
    return new Intl.DateTimeFormat('en-GB',{weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(new Date(year,month-1,day));
  },day);
  await expect(invoice.getByText(fullDate,{exact:true})).toHaveCount(1);
  await expect(invoice.locator('.receipt-card')).not.toContainText(' – ');
  await expect(invoice.locator('.receipt-card')).not.toContainText('Sale #');
  await expect(invoice.locator('.receipt-card')).toContainText('Invoice total');
  await expect(invoice.locator('.receipt-card')).toContainText('Balance due');
  await expect(invoice.getByText('Payment confirmed',{exact:true})).toHaveCount(0);
  await invoice.pdf({path:'/tmp/inprofic-period-invoice.pdf',format:'A4'});
  await invoice.screenshot({path:'/tmp/inprofic-period-invoice.png',fullPage:true});
  await invoice.setViewportSize({width:390,height:844});
  expect(await invoice.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});
test('all other destinations expose period documents with accurate labels',async({page})=>{
  for(const [target,label] of [['Physical Store sales','Invoice'],['Online guest','Invoice'],['Physical Store','Stock transfer'],['Staff issue · Bakery team','Stock issue']]){
    await page.goto('/inventory/market-stock/');
    await page.locator('#distribution-summary').getByRole('link',{name:target,exact:true}).click();
    await expect(page.getByRole('heading',{name:target==='Staff issue · Bakery team'?'Bakery team':target,exact:true})).toBeVisible();
    const popup=page.waitForEvent('popup');
    await page.getByRole('button',{name:`${label} this period`,exact:true}).click();
    const document=await popup;
    await expect(document.locator('.receipt-card')).toBeVisible();
    if(label!=='Invoice'){
      await expect(document.locator('[data-document-status]')).toHaveText('Not applicable');
      await expect(document.getByText('Invoice total',{exact:true})).toHaveCount(0);
    }
    await document.close();
  }
});
test('staff issue edit requires a correction reason and updates the recipient',async({page})=>{
  await page.goto('/inventory/market-stock/');
  const edit=page.getByRole('link',{name:/^Edit issue #/}).first();
  const href=await edit.getAttribute('href');
  await page.goto(href);
  await expect(page.locator('input[name=date]')).toBeDisabled();
  await page.locator('input[name=quantity]').fill('2');
  await page.locator('input[name=recipient]').fill('Morning team');
  await page.getByRole('button',{name:'Save correction',exact:true}).click();
  await expect(page.locator('textarea[name=correction_reason]')).toBeFocused();
  await page.locator('textarea[name=correction_reason]').fill('Corrected recipient and quantity');
  await page.getByRole('button',{name:'Save correction',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Morning team',exact:true})).toBeVisible();
  await expect(page.locator('tbody')).toContainText('2 loaves');
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('inprofic:theme',{detail:'dark'})));
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});
