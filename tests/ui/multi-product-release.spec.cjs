const {test,expect}=require('@playwright/test');
test('customer market release adds multiple products in one form',async({page})=>{
 await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
 await page.goto('/accounts/login/ui-fixture/admin/');
 await page.goto('/inventory/market-stock/release/');
 await expect(page.getByRole('heading',{name:'Products for this customer',exact:true})).toBeVisible();
 const customer=page.locator('select[name="customer"]');
 const customerValue=await customer.locator('option').evaluateAll(options=>options.find(o=>o.textContent==='Customer 00').value);
 await customer.selectOption(customerValue,{force:true});
 await page.getByRole('button',{name:'Add product',exact:true}).click();
 await expect(page.locator('[data-market-release-row]')).toHaveCount(2);
 for(const [index,name] of ['Fixture Loaf','Fixture Cake'].entries()){
  const row=page.locator('[data-market-release-row]').nth(index);
  const select=row.locator('select');
  const value=await select.locator('option').evaluateAll((options,name)=>options.find(o=>o.textContent===name).value,name);
  await select.selectOption(value,{force:true});
  await row.locator('input[name$="-quantity"]').fill('2');
  await expect(row.locator('[data-release-stock-summary]')).toContainText('will remain');
 }
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await page.getByRole('button',{name:'Release to customer',exact:true}).click();
 await expect(page).toHaveURL(/\/inventory\/market-stock\/$/);
 await expect(page.getByText(/Market Stock released to Customer 00 as Distribution Sale/)).toBeVisible();
});
