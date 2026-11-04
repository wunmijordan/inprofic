const { test, expect } = require('@playwright/test');

async function admin(page) {
  await page.goto('/accounts/login/ui-fixture/admin/');
  await expect(page.locator('html')).toHaveAttribute('data-ui-ready','true');
}
async function theme(page, mode) {
  await page.evaluate(mode => document.dispatchEvent(new CustomEvent('inprofic:theme',{detail:mode})),mode);
  await expect(page.locator('html')).toHaveAttribute('data-theme',mode);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}
async function openAccount(page) {
  await page.goto('/finance/');
  await page.getByRole('link',{name:'+ Cash account',exact:true}).click();
  await expect(page.locator('.ui-form-dialog')).toBeVisible();
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(frame.locator('input[name="name"]')).toBeVisible();
  return frame;
}

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => {
    if (window !== window.top || sessionStorage.getItem('ui-test-initialized')) return;
    sessionStorage.setItem('ui-test-initialized','true');
    localStorage.setItem('inprofic-theme','light');
    localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed');
  });
});

test('real form dialogs save to Django and keep failed validation visible', async ({page}) => {
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await admin(page);const frame=await openAccount(page);
  await expect(frame.locator('.desktop-sidebar')).not.toBeVisible();
  await expect(frame.locator('.inprofic-control-wrap .ui-combo-trigger')).toBeVisible();
  await frame.locator('input[name="name"]').fill('Modal UI Till');
  await frame.locator('input[name="opening_balance"]').fill('0');
  // Exercise server-side field validation before a successful retry.
  await frame.locator('input[name="opening_balance"]').evaluate(input=>{input.type='text';input.value='invalid';});
  await frame.locator('form').filter({has:frame.locator('input[name="name"]')}).evaluate(form=>{form.noValidate=true;form.requestSubmit();});
  await expect(frame.locator('.errorlist,.field-has-error').first()).toBeVisible();
  await expect(page.locator('.ui-form-dialog')).toBeVisible();
  await frame.locator('input[name="name"]').fill('Modal UI Till');
  await frame.locator('input[name="opening_balance"]').fill('0');
  await frame.locator('form').filter({has:frame.locator('input[name="name"]')}).evaluate(form=>form.requestSubmit());
  await expect(page.locator('.ui-form-dialog')).not.toBeVisible();
  await expect(page.getByText('Modal UI Till',{exact:true})).toBeVisible();
  await expect(page.getByText('PRIVATE OTHER TENANT')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('dirty close asks before discarding, traps focus and restores the action', async ({page}) => {
  await admin(page);const frame=await openAccount(page);
  await frame.locator('input[name="name"]').fill('Unsaved value');
  await frame.locator('input[name="name"]').press('Escape');
  await expect(page.getByRole('alertdialog')).toBeVisible();
  await page.getByRole('button',{name:'Keep working'}).click();
  await expect(frame.locator('input[name="name"]')).toHaveValue('Unsaved value');
  await page.locator('.ui-form-dialog').getByRole('button',{name:'Close',exact:true}).click();
  await page.getByRole('button',{name:'Discard changes'}).click();
  await expect(page.locator('.ui-form-dialog')).not.toBeVisible();
  await expect(page.getByRole('link',{name:'+ Cash account',exact:true})).toBeFocused();
});

test('searchable selection posts the native value and handles dynamic formsets', async ({page}) => {
  await admin(page);await page.goto('/orders/add/');
  await expect(page.locator('.ui-combo-trigger:visible').first()).toBeVisible();
  // Use a standalone test select in the real form to verify option refresh,
  // multi-selection, reset and clone cleanup independently of pricing rules.
  await page.locator('main form').last().evaluate(form=>{
    const holder=document.createElement('div');holder.id='combo-fixture';
    holder.innerHTML='<label for="ui-choice">Fixture choice</label><select id="ui-choice" name="ui-choice"><option value="">Choose</option><option value="a">Alpha</option><option value="b">Beta</option></select>';
    form.append(holder);
  });
  const combo=page.locator('#combo-fixture').getByRole('combobox',{name:'Fixture choice',exact:true});
  await combo.click();await page.getByPlaceholder('Search options…').fill('Beta');
  await page.getByRole('option',{name:'Beta',exact:true}).click();
  await expect(page.locator('#ui-choice')).toHaveValue('b');
  await page.locator('#ui-choice').evaluate(select=>{select.add(new Option('Gamma','c'));select.dispatchEvent(new Event('ms-refresh'));});
  await combo.click();await page.getByRole('option',{name:'Gamma',exact:true}).click();await expect(page.locator('#ui-choice')).toHaveValue('c');
  await page.locator('#combo-fixture').evaluate(holder=>{const clone=holder.cloneNode(true);clone.id='combo-clone';clone.querySelector('select').id='ui-choice-clone';clone.querySelector('label').htmlFor='ui-choice-clone';holder.after(clone);});
  await expect(page.locator('#combo-clone [data-ui-combo-root]')).toHaveCount(1);
  await page.locator('#ui-choice').evaluate(select=>select.form.reset());
  await expect(combo).toContainText('Choose');
});

test('native details dialog uses Radix and keeps its close contract', async ({page}) => {
  await page.goto('/?view=marketing');
  await page.getByText('See the connected controls',{exact:true}).click();
  await page.getByRole('button',{name:/See what happens next/}).click();
  await expect(page.locator('.ui-legacy-dialog')).toBeVisible();
  await page.getByRole('button',{name:'Close example',exact:true}).click();
  await expect(page.locator('.ui-legacy-dialog')).not.toBeVisible();
  await expect(page.getByRole('button',{name:/See what happens next/})).toBeFocused();
});

test('confirmation replaces simple native confirm without sending a cancelled POST',async({page})=>{
  await admin(page);await page.goto('/finance/');
  await page.locator('form[action^="/finance/accounts/"][action$="/delete/"]').first().getByRole('button',{name:'Delete',exact:true}).click();
  await expect(page.getByRole('alertdialog')).toBeVisible();
  await page.getByRole('button',{name:'Keep working'}).click();
  await expect(page.getByText('Fixture Till',{exact:true})).toBeVisible();
});

test('glass belongs to explicit visual buttons, never to clickability or status containers',async({page})=>{
  await admin(page);
  await page.locator('main').evaluate(main=>{const probe=document.createElement('section');probe.id='glass-probes';probe.innerHTML=`<a id="plain-link" class="rounded-lg border bg-white px-4 py-2" href="#">Ordinary navigation</a><button id="text-button" type="button">Plain text control</button><div id="status-probe" class="workspace-back">Ledger status</div><a id="card-probe" role="button" class="rounded-xl bg-white border p-5" href="#">Clickable card</a><a id="button-probe" data-button-surface class="rounded-lg bg-[#D14900] px-4 py-2" href="#">Visual action</a>`;main.append(probe);});
  for(const id of ['plain-link','text-button','status-probe','card-probe'])expect(await page.locator('#'+id).evaluate(el=>getComputedStyle(el).backgroundImage)).toBe('none');
  expect(await page.locator('#button-probe').evaluate(el=>getComputedStyle(el).backgroundImage)).toContain('linear-gradient');
  for(const mode of ['light','dark']){await theme(page,mode);for(const id of ['plain-link','text-button','status-probe','card-probe'])expect(await page.locator('#'+id).evaluate(el=>getComputedStyle(el).backgroundImage)).toBe('none');}
});

test('logos follow actual backgrounds in desktop, mobile, auth and install prompts',async({page})=>{
  await admin(page);const desktop=page.locator('.desktop-sidebar img[alt="INPROFIC"]');
  await expect(desktop).toHaveAttribute('src',/wordmark-on-dark/);
  await page.evaluate(()=>document.documentElement.style.setProperty('--tenant-background','#fff4da'));
  await expect(desktop).toHaveAttribute('src',/wordmark-on-light/);
  await theme(page,'dark');await expect(desktop).toHaveAttribute('src',/wordmark-on-light/);
  await page.setViewportSize({width:390,height:844});await expect(page.locator('#mobile-nav-shell img[alt="INPROFIC"]')).toHaveAttribute('src',/wordmark-on-light/);
  await page.goto('/accounts/login/');await theme(page,'dark');await expect(page.locator('main img[alt="INPROFIC"]')).toHaveAttribute('src',/wordmark-on-dark/);
  await theme(page,'light');await expect(page.locator('main img[alt="INPROFIC"]')).toHaveAttribute('src',/wordmark-on-light/);
  const logos=page.locator('.pwa-modal img[data-brand-logo-light]');expect(await logos.count()).toBeGreaterThan(0);await expect(logos.first()).toHaveAttribute('data-brand-surface',/light|dark/);
});

test('marketing and in-app matrix share new feature rows and anonymous workflow comparisons',async({page})=>{
  await page.goto('/');await expect(page.getByRole('button',{name:'Theme',exact:true})).toHaveCount(0);await expect(page.getByRole('heading',{level:1})).toContainText('in one clear Workspace');
  await page.locator('[data-plan-feature-matrix]>summary').click();
  for(const text of ['Purchased stock to sale','Shared production runs','Recipe variance review','Double-entry books & statements','Controlled ledger cut-over','WhatsApp-ready Daily Brief & Weekly Summary','Shareable purchase request snapshots','Shareable stock distribution & recorded returns','Provider-ready e-invoices'])await expect(page.locator('.pfm__label').filter({hasText:text}).first()).toBeAttached();
  await expect(page.locator('#plans [data-workflow-comparison]')).toHaveCount(0);
  await page.getByText('See the connected controls',{exact:true}).click();
  await expect(page.locator('#workflows [data-workflow-example]')).toBeVisible();
  await expect(page.locator('#workflows [data-workflow-comparison]')).toBeVisible();
  await admin(page);await page.goto('/users/plans/');
  await expect(page.locator('[data-workflow-comparison]')).toBeVisible();
  await expect(page.getByText('PRIVATE OTHER TENANT')).toHaveCount(0);
});

test('viewer sees no create action; full-page URLs retain server authorization',async({page})=>{
  await page.goto('/accounts/login/ui-fixture/viewer/');await page.goto('/finance/');
  await expect(page.getByRole('link',{name:'+ Cash account',exact:true})).toHaveCount(0);
  const response=await page.goto('/finance/accounts/add/');expect(response.status()).toBe(403);
});

test('theme text contrast and mobile marketing layout remain readable',async({page},testInfo)=>{
  const scan=require('./contrast.cjs');
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:950});await page.goto('/?view=marketing');
    for(const mode of ['light','dark']){
      await theme(page,mode);
      const result=await page.evaluate(scan);expect(result.checked).toBeGreaterThan(20);expect(result.findings).toEqual([]);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
      await page.screenshot({path:testInfo.outputPath(`marketing-${width}-${mode}.png`),fullPage:true});
    }
  }
  await page.setViewportSize({width:1440,height:1000});await admin(page);
  for(const route of ['/inventory/','/finance/','/brief/','/finance/books/','/finance/tax/']){
    await page.goto(route);
    for(const mode of ['light','dark']){await theme(page,mode);const result=await page.evaluate(scan);expect(result.findings,`${route} ${mode}`).toEqual([]);}
  }
});

test('appearance popover keeps live theme and accent choices usable',async({page},testInfo)=>{
  await admin(page);await page.getByRole('button',{name:'Appearance',exact:true}).click();
  const popover=page.locator('.ui-appearance-content[data-state=open]');await expect(popover).toBeVisible();
  await popover.getByRole('button',{name:'Dark',exact:true}).click();await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  await popover.locator('[data-accent-choice=inproficNavy]').click();await expect(page.locator('html')).toHaveAttribute('data-accent-preference','inproficNavy');
  await page.screenshot({path:testInfo.outputPath('workspace-dark-appearance.png')});
  await page.keyboard.press('Escape');await expect(popover).not.toBeVisible();
  const frame=await openAccount(page);await expect(frame.locator('input[name="name"]')).toBeVisible();
  await expect(frame.locator('html')).toHaveAttribute('data-theme','dark');
  await expect(frame.locator('html')).toHaveAttribute('data-accent-preference','inproficNavy');
  await page.screenshot({path:testInfo.outputPath('workspace-form-dialog.png')});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(async()=>{
    const bounds=await page.locator('.ui-form-dialog').boundingBox();
    return bounds && bounds.x>=-1 && bounds.y>=-1 && bounds.x+bounds.width<=391;
  }).toBe(true);
  await expect(frame.locator('input[name="name"]')).toBeVisible();
  await expect(page.locator('.ui-form-dialog').getByRole('button',{name:'Close',exact:true})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('workspace-form-mobile.png')});
});

test('form dialogs use one surface, subtle blur and accessible motion',async({page})=>{
  await admin(page);
  const frame=await openAccount(page);
  await expect(page.getByRole('link',{name:'Open full page',exact:true})).toHaveCount(0);
  for(const mode of ['light','dark']){
    await theme(page,mode);
    const panels=frame.locator('[data-ui-form-panel]');
    expect(await panels.count()).toBeGreaterThan(0);
    for(const panel of await panels.all()){
      const style=await panel.evaluate(el=>({shadow:getComputedStyle(el).boxShadow,border:getComputedStyle(el).borderTopWidth}));
      expect(style.shadow).toBe('none');expect(style.border).toBe('0px');
    }
    expect(await page.locator('.ui-modal-overlay').evaluate(el=>getComputedStyle(el).backdropFilter)).toBe('blur(2px)');
  }
  await page.emulateMedia({reducedMotion:'reduce'});
  expect(await page.locator('.ui-form-dialog').evaluate(el=>getComputedStyle(el).animationName)).toBe('none');
  await page.locator('.ui-form-dialog').getByRole('button',{name:'Close',exact:true}).click();
  await expect(page.locator('.ui-form-dialog')).not.toBeVisible();
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.getByRole('link',{name:'+ Cash account',exact:true}).click();
  await expect(page.locator('.ui-form-dialog')).toHaveAttribute('data-state','open');
  expect(await page.locator('.ui-form-dialog').evaluate(el=>getComputedStyle(el).animationName)).toBe('ui-modal-enter');
  await page.locator('.ui-form-dialog').getByRole('button',{name:'Close',exact:true}).click();
  await expect(page.locator('.ui-form-dialog')).toHaveAttribute('data-state','closed');
  await expect(page.locator('.ui-form-dialog')).not.toBeVisible();
});


test('workspace preserves the authored Appearance icon and typography after enhancement', async ({page}) => {
  await admin(page);
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height:900});
    const appearance = page.getByRole('button', {name:'Appearance', exact:true});
    await expect(appearance).toBeVisible();
    await expect(appearance.locator('svg')).toHaveAttribute('stroke', '#d13b00');
    await expect(appearance).toHaveText('');
    expect(await page.locator('body').evaluate(el => getComputedStyle(el).fontFamily)).toContain('Sora');
    expect(await page.locator('main h1').first().evaluate(el => getComputedStyle(el).fontFamily)).toContain('Fraunces');
    expect(await page.locator('main .font-mono').first().evaluate(el => getComputedStyle(el).fontFamily)).toContain('IBM Plex Mono');
    await expect(page.getByRole('link', {name:'Ledger setup',exact:true})).toHaveCount(0);
    await appearance.click();
    await expect(page.getByText('Workspace theme', {exact:true})).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(appearance).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});


test('Live and Backlog actions stay inside the period card on mobile and desktop', async ({page}) => {
  await admin(page);
  await page.goto('/ledger/');
  const date = page.locator('input[name="cutover_date"]');
  await date.fill(await date.getAttribute('max'));
  await page.getByRole('button', {name:'Start cut-over', exact:true}).click();
  try {
    for (const width of [1440, 390]) {
      await page.setViewportSize({width,height:900});
      const card = page.locator('[data-ledger-status]');
      await expect(card.locator('.ledger-period-marquee')).toBeVisible();
      const enter = card.getByRole('button',{name:'Enter backlog',exact:true});
      await expect(enter).toBeVisible();
      const textBox = await card.locator('.ledger-period-marquee').boundingBox();
      const buttonBox = await enter.boundingBox();
      expect(buttonBox.x).toBeGreaterThanOrEqual(textBox.x + textBox.width);
      await enter.click();
      await card.getByRole('button',{name:'Return to Live',exact:true}).click();
      await expect(card.getByRole('button',{name:'Enter backlog',exact:true})).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  } finally {
    await page.goto('/ledger/');
    const token = await page.locator('[name="csrfmiddlewaretoken"]').first().inputValue();
    await page.request.post('/ledger/action/', {form:{csrfmiddlewaretoken:token,action:'revert_split'}});
  }
});
