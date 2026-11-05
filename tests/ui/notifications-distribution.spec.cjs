const {test,expect} = require('@playwright/test');

test.beforeEach(async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
});

test('market distribution shows customer region and group on desktop and mobile',async({page},testInfo)=>{
  await page.goto('/accounts/login/ui-fixture/admin/');
  await page.goto('/inventory/market-stock/');
  await expect(page.locator('#distribution-summary').getByText('Region: Lagos · Group: Distributors',{exact:true})).toBeVisible();
  await expect(page.locator('#distribution-settlement-heading')).toBeVisible();
  await page.getByText('Review by customer and invoice',{exact:true}).click();
  await expect(page.locator('section[aria-labelledby="distribution-settlement-heading"]').getByText('Region: Lagos · Group: Distributors',{exact:true})).toBeVisible();
  const payload = await page.locator('#distribution-share').evaluate(el=>JSON.parse(el.textContent));
  expect(payload).toContain('Region: Lagos'); expect(payload).toContain('Group: Distributors');
  const download = page.waitForEvent('download');
  await page.getByRole('button',{name:'Share distribution summary',exact:true}).click();
  await page.getByRole('menuitem',{name:'Save image',exact:true}).click();
  await (await download).saveAs(testInfo.outputPath('distribution-labels.png'));
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('distribution-labels-mobile.png'),fullPage:true});
});

test('device panel reports delivery state and the specific inventory summary',async({page})=>{
  await page.addInitScript(()=>{
    Object.defineProperty(Notification,'permission',{configurable:true,get:()=> 'granted'});
    const subscription = {endpoint:'https://push.example/ui-device',toJSON:()=>({endpoint:'https://push.example/ui-device',keys:{p256dh:'test-key',auth:'test-auth'}})};
    Object.defineProperty(navigator.serviceWorker,'ready',{configurable:true,value:Promise.resolve({pushManager:{getSubscription:async()=>subscription}})});
  });
  await page.route('**/commerce/notifications/push/config/**',route=>route.fulfill({json:{configured:true,public_key:'test-public',active_devices:1,
    inventory:{enabled:true,due:2,snoozed:1,message:'2 raw materials are low; 1 product is getting low.'},
    delivery:{pending:1,failed:0,expired:0,last_sent:'2026-11-05T10:00:00Z',summaries:[{event_type:'inventory_alert',message:'2 raw materials are low; 1 product is getting low.'}]}}}));
  await page.route('**/commerce/notifications/push/subscribe/**',route=>route.fulfill({json:{subscribed:true}}));
  await page.goto('/accounts/login/ui-fixture/admin/');
  await page.goto('/inventory/alert-settings/');
  const panel=page.locator('[data-operational-push-panel]');
  await expect(panel.locator('[data-push-status]')).toContainText('2 raw materials are low; 1 product is getting low.');
  await expect(panel.locator('[data-push-status]')).toContainText('Waiting to send: 1');
  await expect(panel.locator('[data-push-status]')).toContainText('Last push accepted:');
  await expect(panel.locator('[data-push-status]')).toContainText('Acknowledged or snoozed stock alerts: 1');
});

test('read-only staff can reach their own device settings from the alert tray',async({page})=>{
  await page.goto('/accounts/login/ui-fixture/viewer/');
  await page.goto('/inventory/');
  await expect(page.locator('#commerce-notification-dock')).toBeVisible();
  await page.locator('#commerce-notification-expand').click();
  await page.getByRole('link',{name:'Device notification settings',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Device notifications',exact:true})).toBeVisible();
  await expect(page.locator('[data-operational-push-panel]')).toBeVisible();
});
