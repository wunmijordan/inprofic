const {test,expect}=require('@playwright/test');
test('restored marketing preserves typing, connected modules, service walkthroughs and complete FAQ',async({page},testInfo)=>{
  await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/?view=marketing');
  await expect(page.locator('html')).toHaveAttribute('data-ui-ready','true');
  await expect(page.locator('.hero-typing [data-typed-text]')).toBeVisible();
  await expect(page.locator('[data-motion-toggle], [data-demo-play]')).toHaveCount(0);
  await expect(page.locator('[data-module-connector-paths] path')).toHaveCount(18);
  await expect(page.locator('#capabilities .feature-card')).toHaveCount(9);
  // A visitor's pointer pauses the floating card before activation.
  const inventoryCard=page.locator('#capabilities .feature-card').filter({has:page.getByRole('heading',{name:'Inventory',exact:true})});
  await page.locator('#capabilities').scrollIntoViewIfNeeded();
  const target=await inventoryCard.boundingBox();await page.mouse.move(target.x+target.width/2,target.y+target.height/2);
  await expect(inventoryCard).toHaveCSS('animation-play-state','paused');
  await inventoryCard.click();
  await expect(page.locator('.ui-legacy-dialog')).toBeVisible();
  await expect(page.locator('.ui-legacy-dialog')).toContainText('Illustrative data');
  await expect(page.locator('.ui-legacy-dialog .feature-mini-title')).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('module-preview-desktop.png')});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(async()=>{const box=await page.locator('.ui-legacy-dialog').boundingBox();return box&&box.x>=0&&box.x+box.width<=391;}).toBe(true);
  await page.screenshot({path:testInfo.outputPath('module-preview-mobile.png')});
  await page.setViewportSize({width:1280,height:720});
  await page.keyboard.press('Escape');
  await expect(page.locator('.ui-legacy-dialog')).not.toBeVisible();
  await expect(page.locator('#capabilities .feature-card').filter({has:page.getByRole('heading',{name:'Inventory',exact:true})})).toBeFocused();
  const service=page.getByRole('combobox',{name:'Choose a business service for every demo on this page'});
  await service.click();await page.getByRole('option',{name:'Retail',exact:true}).click();
  await expect(page.locator('#general-product-demo')).toContainText('Retail purchasing');
  await expect(page.locator('#general-product-demo [data-demo-module=Production]')).toHaveCount(0);
  await expect(page.locator('.marketing-service-control [data-demo-service]')).toHaveValue('retail');
  await expect(page.locator('[data-workflow-example]')).toHaveAttribute('data-vertical','retail');
  await expect(page.locator('[data-workflow-example]')).toContainText('38 − 3 = 35');
  await expect(page.locator('[data-workflow-rows]')).not.toContainText('Shared Runs');
  await service.click();await page.getByRole('option',{name:'Bakery',exact:true}).click();
  await expect(page.locator('[data-workflow-example]')).toContainText('9.091 kg');
  await expect(page.locator('[data-workflow-rows]')).toContainText('Shared Runs');
  await page.locator('#general-product-demo').scrollIntoViewIfNeeded();
  await page.mouse.move(0,0);await page.evaluate(()=>document.activeElement?.blur());
  const version=await page.locator('#general-product-demo').getAttribute('data-scene-version');
  await expect.poll(()=>page.locator('#general-product-demo').getAttribute('data-scene-version'),{timeout:25000}).not.toBe(version);
  await page.getByRole('button',{name:'Show all questions'}).click();
  await expect(page.locator('#faq details')).toHaveCount(33);
  await expect(page.locator('#faq summary').filter({hasText:'Can one sale contain both stocked and made-to-order items?'})).toBeVisible();
  for(const name of ['ERPNext','Frappe','Sage','Bumpa'])await expect(page.getByText(new RegExp(name))).toHaveCount(0);
  await page.getByRole('button',{name:'Explore INPROFIC',exact:true}).click();
  await page.getByRole('menuitem',{name:'Connected modules'}).click();
  await expect(page).toHaveURL(/#capabilities$/);
  expect(errors).toEqual([]);
  await page.setViewportSize({width:1440,height:950});await page.goto('/?view=marketing');
  await page.screenshot({path:testInfo.outputPath('restored-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:950});
  await page.screenshot({path:testInfo.outputPath('restored-mobile.png'),fullPage:true});
});

test('marketing respects reduced motion and keeps every service and headline within the shared layout',async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
  await page.setViewportSize({width:390,height:844});await page.goto('/?view=marketing');
  await expect(page.locator('html')).toHaveAttribute('data-ui-ready','true');
  await page.evaluate(()=>document.fonts.ready);
  const typed=page.locator('[data-typed-text]');await expect(typed).toHaveText('Stock');
  await typed.evaluate(el=>el.textContent='Production');
  const baselines=await page.evaluate(()=>['.hero-opening>span:first-child','[data-typed-text]'].map(selector=>{
    const probe=document.createElement('i');probe.style.cssText='display:inline-block;width:0;height:0;vertical-align:baseline';
    document.querySelector(selector).append(probe);const baseline=probe.getBoundingClientRect().y;probe.remove();return baseline;
  }));
  expect(Math.abs(baselines[0]-baselines[1])).toBeLessThan(1);
  const icon=await page.locator('.hero-typecursor').boundingBox();expect(icon.x+icon.width).toBeLessThanOrEqual(391);
  expect(await typed.evaluate(el=>getComputedStyle(el).backgroundImage)).toContain('linear-gradient');
  expect(await page.locator('.hero-preview>div').evaluate(el=>getComputedStyle(el).boxShadow)).toBe('none');
  const service=page.getByRole('combobox',{name:'Choose a business service for every demo on this page'});
  for(const [label,value] of [['Bakery','bakery'],['Restaurant & Food Service','restaurant'],['General Production','general'],['Wholesale','wholesale'],['Retail','retail']]){
    await service.click();await page.getByRole('option',{name:label,exact:true}).click();
    await expect(page.locator('[data-workflow-example]')).toHaveAttribute('data-vertical',value);
    await expect(page.locator('#general-product-demo [data-demo-module=Production]')).toHaveCount(['retail','wholesale'].includes(value)?0:1);
  }
  const demo=page.locator('#general-product-demo');await demo.scrollIntoViewIfNeeded();await page.mouse.move(0,0);
  const version=await demo.getAttribute('data-scene-version');await page.waitForTimeout(3500);
  await expect(demo).toHaveAttribute('data-scene-version',version);
  for(const mode of ['light','dark']){
    await page.evaluate(mode=>document.dispatchEvent(new CustomEvent('inprofic:theme',{detail:mode})),mode);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(391);
  }
});

test('live-style scroll entrances and solid cards remain visible, and the typing icon never jumps or overlaps',async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('inprofic:essential-privacy-notice:v1','dismissed'));
  await page.goto('/?view=marketing');await expect(page.locator('html')).toHaveAttribute('data-ui-ready','true');
  await page.evaluate(()=>document.fonts.ready);
  const samples=await page.evaluate(async()=>{
    const samples=[];const started=performance.now();
    while(performance.now()-started<5000){
      const text=document.querySelector('[data-typed-text]'),svg=document.querySelector('.hero-typecursor');
      const a=text.getBoundingClientRect(),b=svg.getBoundingClientRect();
      samples.push({empty:!text.textContent,top:b.top,gap:b.left-a.right,bg:getComputedStyle(svg).backgroundColor});
      await new Promise(resolve=>setTimeout(resolve,35));
    }return samples;
  });
  expect(samples.some(sample=>sample.empty)).toBe(true);
  expect(Math.max(...samples.map(s=>s.top))-Math.min(...samples.map(s=>s.top))).toBeLessThan(1);
  expect(Math.min(...samples.map(s=>s.gap))).toBeGreaterThan(2);
  expect(samples.every(s=>s.bg==='rgba(0, 0, 0, 0)')).toBe(true);
  const section=page.locator('#capabilities');await expect(section).toHaveAttribute('data-reveal','');
  await expect(section).not.toHaveClass(/is-revealed/);
  await section.scrollIntoViewIfNeeded();await expect(section).toHaveClass(/is-revealed/);
  await expect(section).toHaveCSS('opacity','1');
  for(const theme of ['light','dark']){
    await page.evaluate(theme=>document.dispatchEvent(new CustomEvent('inprofic:theme',{detail:theme})),theme);
    const paint=await page.locator('#capabilities .feature-card').first().evaluate(card=>({card:getComputedStyle(card).backgroundColor,canvas:getComputedStyle(document.querySelector('#capabilities')).backgroundColor}));
    expect(paint.card).not.toBe('rgba(0, 0, 0, 0)');expect(paint.card).not.toBe(paint.canvas);
  }
});


test('live palette, shadows, bridge overlap and WhatsApp contact remain intact with recent additions',async({page})=>{
  await page.goto('/?view=marketing');await expect(page.locator('html')).toHaveAttribute('data-ui-ready','true');
  for(const theme of ['light','dark']){
    await page.evaluate(theme=>document.dispatchEvent(new CustomEvent('inprofic:theme',{detail:theme})),theme);
    await expect(page.locator('body')).toHaveCSS('background-color','rgb(250, 246, 239)');
    await expect(page.locator('header')).toHaveCSS('background-color','rgb(5, 7, 51)');
    await expect(page.locator('.ip-demo').first()).toHaveCSS('background-color','rgb(255, 253, 248)');
    const shadow=await page.locator('.feature-card').first().evaluate(el=>getComputedStyle(el).boxShadow);expect(shadow).not.toBe('none');
  }
  const header=await page.locator('header').boundingBox(),bridge=await page.locator('.bridge-card').boundingBox();
  expect(bridge.y).toBeLessThan(header.y+header.height);expect(bridge.y+bridge.height).toBeGreaterThan(header.y+header.height);
  await expect(page.locator('.marketing-whatsapp-icon')).toHaveCSS('background-color','rgb(37, 211, 102)');
  await expect(page.locator('.marketing-whatsapp-icon')).toHaveCSS('border-radius','999px');
  await expect(page.locator('.hero-preview')).toHaveCSS('animation-name','preview-float');
  await expect(page.locator('.feature-card').first()).toHaveCSS('animation-name','capability-card-float');
  await page.emulateMedia({reducedMotion:'reduce'});await expect(page.locator('.hero-preview')).toHaveCSS('animation-name','none');
  await expect(page.locator('#workflows')).not.toHaveAttribute('open');
  await expect(page.locator('#plans [data-workflow-comparison]')).toHaveCount(0);
  await page.getByText('See the connected controls',{exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(391);
});
