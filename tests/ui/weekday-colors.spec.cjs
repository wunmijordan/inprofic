const {test,expect}=require('@playwright/test');
test('weekday color follows production dates across weeks',async({page})=>{
 await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
 await page.goto('/accounts/login/ui-fixture/admin/');
 await page.goto('/settings/');
 await page.locator('select[name="weekday"]').selectOption('0',{force:true});
 await page.locator('input[name="color"]').fill('#123456');
 await page.locator('input[name="expiry_days"]').fill('3');
 await page.getByRole('button',{name:'Add weekday color',exact:true}).click();
 await expect(page.getByRole('cell',{name:'Monday',exact:true})).toBeVisible();
 await page.goto('/orders/add/');
 const date=page.locator('input[name="date"]');
 for(const value of ['2026-11-02','2026-11-09']){
  await date.fill(value);await date.dispatchEvent('change');
  await expect(page.locator('#id_color_tag')).toHaveValue('#123456');
 }
 await date.fill('2026-11-03');await date.dispatchEvent('change');
 await expect(page.locator('#id_color_tag')).toHaveValue('');
 await expect(page.locator('#daily-production-color-copy')).toContainText('No production color is set for this weekday');
});
