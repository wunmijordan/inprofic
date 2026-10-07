(function(){
  'use strict';
  const icons={
    email:'<path data-draw d="M3.5 6.5h17v11h-17z"/><path data-draw d="m4 7 8 6 8-6"/>',
    password:'<rect data-draw x="5" y="10" width="14" height="10" rx="2"/><path data-draw d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    phone:'<path data-draw d="M7 3h3l1 5-2 1a14 14 0 0 0 6 6l1-2 5 1v3c0 2-2 4-4 4C9 20 4 15 3 7c0-2 2-4 4-4Z"/>',
    search:'<circle data-draw cx="10.5" cy="10.5" r="6.5"/><path data-draw d="m16 16 5 5"/>',
    calendar:'<rect data-draw x="3" y="5" width="18" height="16" rx="2"/><path data-draw d="M7 3v4M17 3v4M3 9h18"/>',
    clock:'<circle data-draw cx="12" cy="12" r="9"/><path data-draw d="M12 7v5l3 2"/>',
    number:'<path data-draw d="M8 3 6 21M18 3l-2 18M3 9h18M2 15h18"/>',
    money:'<path data-draw stroke="none" d="M0 0h24v24H0z" fill="none"/><path data-draw d="M7 18v-10.948a1.05 1.05 0 0 1 1.968 -.51l6.064 10.916a1.05 1.05 0 0 0 1.968 -.51v-10.948"/><path data-draw d="M5 10h14"/><path data-draw d="M5 14h14"/>',
    user:'<circle data-draw cx="12" cy="8" r="4"/><path data-draw d="M4 21c1-5 4-7 8-7s7 2 8 7"/>',
    account:'<circle data-draw cx="12" cy="12" r="9"/><circle data-draw cx="12" cy="12" r="3"/><path data-draw d="M15 9v5c0 1.1.9 2 2 2 1.7 0 3-1.6 3-4"/>',
    business:'<path data-draw d="M3 10.5 12 3l9 7.5"/><path data-draw d="M5 9.5V21h14V9.5"/><path data-draw d="M9 21v-7h6v7"/>',
    item:'<path data-draw d="m4 7 8-4 8 4-8 4-8-4Z"/><path data-draw d="M4 7v10l8 4 8-4V7M12 11v10"/>',
    location:'<path data-draw d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z"/><circle data-draw cx="12" cy="10" r="2.5"/>',
  };
  function fieldLabel(el){
    const labels=el.labels ? [...el.labels].map(label=>label.textContent||'') : [];
    if(!labels.length){
      const nearest=el.closest('label');
      if(nearest)labels.push(nearest.textContent||'');
    }
    return labels.join(' ').replace(/\s+/g,' ').trim().toLowerCase();
  }
  function iconFor(el){
    const forced=(el.dataset.inputIcon||'').trim().toLowerCase();
    if(forced && icons[forced])return forced;
    const name=(el.name||'').toLowerCase(), type=(el.type||'').toLowerCase(), ph=(el.placeholder||'').toLowerCase(), label=fieldLabel(el);
    if(type==='email'||name.includes('email'))return 'email';
    if(type==='password'||name.includes('password'))return 'password';
    if(type==='tel'||name.includes('phone')||name.includes('mobile'))return 'phone';
    if(type==='search'||name.includes('search')||ph.startsWith('search'))return 'search';
    if(type==='date'||name.endsWith('_date')||name==='date')return 'calendar';
    if(type==='time'||name.includes('time'))return 'clock';
    if(name.includes('address')||name.includes('location'))return 'location';
    if(name.includes('price')||name.includes('amount')||name.includes('cost')||name.includes('revenue')||name.includes('fee')||name.includes('budget')||name.includes('total'))return 'money';
    if(type==='number'||name.includes('quantity')||name.includes('qty')||name.includes('count'))return 'number';

    // The face icon is deliberately reserved for actual people's names.  A
    // generic field called "name" is normally a product/material/category/etc.
    // in INPROFIC, so label context must explicitly identify a person.
    const humanNameField=/^(full_?name|fullname|first_?name|last_?name|customer_?name|contact_?name|recipient_?name|employee_?name|staff_?name|member_?name|rider_?name|driver_?name)$/;
    const humanNameLabel=/\b(full name|first name|last name|customer name|contact name|recipient name|employee name|staff name|member name|rider name|driver name)\b/;
    if(humanNameField.test(name)||humanNameLabel.test(label))return 'user';
    if(name.includes('username')||label.includes('username'))return 'account';

    const businessLike=/\b(business|company|merchant|store|workspace|organisation|organization|supplier|vendor)\b/;
    if(businessLike.test(name.replaceAll('_',' '))||businessLike.test(label))return 'business';
    const itemLike=/\b(product|raw material|material|finished good|item|category|service|plan|unit|role|package|pack|option|variant|title|warehouse|sku|code|name)\b/;
    if(itemLike.test(name.replaceAll('_',' '))||itemLike.test(label))return 'item';
    return '';
  }
  function svg(markup, cls, state, options={}){
    const ns='http://www.w3.org/2000/svg'; const node=document.createElementNS(ns,'svg');
    node.setAttribute('viewBox','0 0 24 24'); node.setAttribute('fill','none'); node.setAttribute('stroke',options.stroke||'currentColor'); node.setAttribute('stroke-width',options.strokeWidth||'1.8'); node.setAttribute('stroke-linecap','round'); node.setAttribute('stroke-linejoin','round'); node.setAttribute('aria-hidden','true'); node.setAttribute('class',cls);
    if(state)node.dataset.state=state; node.innerHTML=markup; return node;
  }
  function eligible(el){
    if(!el||el.dataset.noFormEnhance!==undefined||el.closest('[data-no-form-enhance]'))return false;
    if(el.matches('input[type="hidden"],input[type="checkbox"],input[type="radio"],input[type="file"],input[type="color"],button'))return false;
    return el.matches('input,select,textarea');
  }
  function stateFor(el, force){
    const wrap=el.closest('.inprofic-control-wrap'); if(!wrap)return;
    const dirty=force||el.dataset.inproficDirty==='1';
    wrap.classList.remove('is-valid','is-invalid');
    const msg=wrap.parentElement?.querySelector(':scope > .inprofic-client-message');
    if(!dirty || el.disabled || el.readOnly){if(msg)msg.dataset.visible='false';return;}
    const invalid=!el.checkValidity();
    if(invalid){wrap.classList.add('is-invalid');if(msg){msg.textContent=el.validationMessage||'Check this field.';msg.dataset.visible='true';}}
    else {if((el.value||'').trim()!=='' || el.required)wrap.classList.add('is-valid');if(msg)msg.dataset.visible='false';}
  }
  function enhance(el){
    if(!eligible(el)||el.dataset.inproficEnhanced==='1')return;
    el.dataset.inproficEnhanced='1';
    const parent=el.parentElement; const wrap=document.createElement('span'); wrap.className='inprofic-control-wrap';
    parent.insertBefore(wrap,el); wrap.appendChild(el);
    const key=iconFor(el); if(key){
      wrap.dataset.icon=key;
      const iconOptions=key==='money'?{stroke:'rgb(0, 209, 45)',strokeWidth:'2'}:{};
      wrap.appendChild(svg(icons[key],'inprofic-input-icon',null,iconOptions));
    }
    wrap.classList.add('has-validation-mark');
    wrap.appendChild(svg('<path d="m5 12 4 4L19 6"/>','inprofic-validation-mark','valid'));
    wrap.appendChild(svg('<path d="M7 7l10 10M17 7 7 17"/>','inprofic-validation-mark','invalid'));
    const msg=document.createElement('span');msg.className='inprofic-client-message';msg.setAttribute('aria-live','polite');parent.insertBefore(msg,wrap.nextSibling);
    ['input','change'].forEach(type=>el.addEventListener(type,()=>{el.dataset.inproficDirty='1';stateFor(el,false);}));
    el.addEventListener('blur',()=>{el.dataset.inproficDirty='1';stateFor(el,false);});
    if(el.closest('.field-has-error')){el.dataset.inproficDirty='1';wrap.classList.add('is-invalid');}
  }
  function helpBubbleFor(tip){
    return tip&&(tip.__inproficHelpBubble||tip.querySelector('[data-form-help-bubble]'))||null;
  }
  function mountPortalHelpBubble(tip){
    if(!tip||tip.dataset.formHelpPortal!=='true')return helpBubbleFor(tip);
    const bubble=helpBubbleFor(tip);
    if(!bubble)return null;
    if(!tip.__inproficHelpBubble){
      tip.__inproficHelpMarker=document.createComment('help-tip-bubble');
      bubble.parentNode.insertBefore(tip.__inproficHelpMarker,bubble);
      tip.__inproficHelpBubble=bubble;
    }
    document.body.appendChild(bubble);
    return bubble;
  }
  function restorePortalHelpBubble(tip,bubble){
    if(!tip||tip.dataset.formHelpPortal!=='true'||!bubble)return;
    const marker=tip.__inproficHelpMarker;
    if(marker?.parentNode)marker.parentNode.insertBefore(bubble,marker.nextSibling);
    else bubble.remove();
    tip.__inproficHelpBubble=null;
    tip.__inproficHelpMarker=null;
  }
  function closeHelpTip(tip){
    if(!tip)return;
    const trigger=tip.querySelector('[data-form-help-trigger]');
    const bubble=helpBubbleFor(tip);
    tip.classList.remove('is-open');
    if(trigger)trigger.setAttribute('aria-expanded','false');
    if(bubble)bubble.hidden=true;
    restorePortalHelpBubble(tip,bubble);
  }
  function placeHelpTip(tip){
    const trigger=tip&&tip.querySelector('[data-form-help-trigger]');
    const bubble=helpBubbleFor(tip);
    if(!trigger||!bubble)return;
    const isPortal=tip.dataset.formHelpPortal==='true';
    const vp=isPortal?null:window.visualViewport;
    const vw=vp?vp.width:window.innerWidth, vh=vp?vp.height:window.innerHeight;
    const ox=vp?vp.offsetLeft:0, oy=vp?vp.offsetTop:0, m=8;
    bubble.style.maxWidth=(vw-m*2)+'px';
    const t=trigger.getBoundingClientRect();
    const bw=bubble.offsetWidth, bh=bubble.offsetHeight;
    const left=Math.max(ox+m,Math.min(t.left+t.width/2-bw/2,ox+vw-bw-m));
    let top=t.bottom+8;
    if(top+bh>oy+vh-m&&t.top-8-bh>=oy+m)top=t.top-8-bh;
    bubble.style.left=left+'px';
    bubble.style.top=top+'px';
    // An ancestor with transform/filter becomes the containing block for
    // position:fixed; measure and correct so placement is still viewport-true.
    const r=bubble.getBoundingClientRect();
    if(!isPortal&&(Math.abs(r.left-left)>1||Math.abs(r.top-top)>1)){
      bubble.style.left=(left-(r.left-left))+'px';
      bubble.style.top=(top-(r.top-top))+'px';
    }
  }
  function placeVisibleHelpTips(){
    document.querySelectorAll('[data-form-help-tip].is-open').forEach(placeHelpTip);
  }
  window.addEventListener('resize',placeVisibleHelpTips);
  window.addEventListener('scroll',placeVisibleHelpTips,true);
  if(window.visualViewport){
    window.visualViewport.addEventListener('resize',placeVisibleHelpTips);
    window.visualViewport.addEventListener('scroll',placeVisibleHelpTips);
  }
  function openHelpTip(tip){
    if(!tip)return;
    document.querySelectorAll('[data-form-help-tip].is-open').forEach(other=>{
      if(other!==tip)closeHelpTip(other);
    });
    const trigger=tip.querySelector('[data-form-help-trigger]');
    const bubble=mountPortalHelpBubble(tip);
    tip.classList.add('is-open');
    if(trigger)trigger.setAttribute('aria-expanded','true');
    if(bubble)bubble.hidden=false;
    placeHelpTip(tip);
  }
  // A <label> without for="" activates its FIRST labelable descendant. When a
  // help-tip button precedes a toggle/checkbox inside the same label, the button
  // wins, so tapping the switch only opened the tip. Point the label at the real
  // control explicitly.
  function retargetWrappingLabel(tip){
    const label=tip.closest('label');
    if(!label||label.hasAttribute('for'))return;
    const control=Array.from(label.querySelectorAll('input:not([type=hidden]),select,textarea'))
      .find(el=>!tip.contains(el));
    if(!control)return;
    if(!control.id)control.id='ctl_'+Math.random().toString(36).slice(2,9);
    label.htmlFor=control.id;
  }
  function enhanceHelpTip(tip){
    if(!tip||tip.dataset.formHelpBound==='1')return;
    const trigger=tip.querySelector('[data-form-help-trigger]');
    const bubble=tip.querySelector('[data-form-help-bubble]');
    if(!trigger||!bubble)return;
    tip.dataset.formHelpBound='1';
    retargetWrappingLabel(tip);
    if(tip.dataset.formHelpPortal==='true'){
      tip.addEventListener('mouseenter',()=>openHelpTip(tip));
      tip.addEventListener('mouseleave',event=>{if(!bubble.contains(event.relatedTarget)&&!trigger.matches(':focus'))closeHelpTip(tip);});
      bubble.addEventListener('mouseleave',event=>{if(!tip.contains(event.relatedTarget)&&!trigger.matches(':focus'))closeHelpTip(tip);});
      tip.addEventListener('focusin',()=>openHelpTip(tip));
      tip.addEventListener('focusout',event=>{if(!tip.contains(event.relatedTarget))closeHelpTip(tip);});
    }else{
      // Desktop hover/focus reveal is CSS-driven; place it once it is displayed.
      ['mouseenter','focusin'].forEach(type=>tip.addEventListener(type,()=>requestAnimationFrame(()=>placeHelpTip(tip))));
    }
    bubble.hidden=true;
    trigger.setAttribute('aria-expanded','false');
    trigger.addEventListener('click',event=>{
      event.preventDefault();
      event.stopPropagation();
      if(tip.classList.contains('is-open'))closeHelpTip(tip);
      else openHelpTip(tip);
    });
    trigger.addEventListener('keydown',event=>{
      if(event.key==='Escape'){
        event.preventDefault();
        closeHelpTip(tip);
        trigger.blur();
      }
    });
  }
  function createHelpTip(labelText='More information'){
    const tip=document.createElement('span');
    tip.className='form-help-tip normal-case tracking-normal';
    tip.dataset.formHelpTip='';

    const trigger=document.createElement('button');
    trigger.type='button';
    trigger.className='form-help-tip__trigger';
    trigger.dataset.formHelpTrigger='';
    trigger.setAttribute('aria-label',labelText ? `Help for ${labelText}` : 'More information');
    trigger.setAttribute('aria-expanded','false');
    trigger.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><circle cx="10" cy="10" r="8"></circle><path d="M10 9v5"></path><path d="M10 6.25h.01"></path></svg>';

    const bubble=document.createElement('span');
    bubble.className='form-help-tip__bubble normal-case tracking-normal';
    bubble.dataset.formHelpBubble='';
    bubble.setAttribute('role','tooltip');
    bubble.hidden=true;

    tip.append(trigger,bubble);
    return tip;
  }
  function labelTextForControl(control){
    if(!control)return '';
    const explicit=control.id ? document.querySelector(`label[for="${CSS.escape(control.id)}"]`) : null;
    const label=explicit||control.closest('label');
    if(!label)return '';
    return (label.cloneNode(true).textContent||'').replace(/\s+/g,' ').trim();
  }
  function fieldContainerForControl(control){
    if(!control)return null;
    return control.closest('.cf-field,.cf-toggle-field')||control.closest('label')||control.parentElement;
  }
  function findControlForHelp(help){
    const requested=(help.dataset.formHelpFor||'').replace(/^#/,'').trim();
    if(requested){
      const direct=document.getElementById(requested);
      if(direct)return direct;
    }
    const describedBy=help.id||'';
    if(describedBy.endsWith('_helptext')){
      const inputId=describedBy.slice(0,-9);
      const related=document.getElementById(inputId);
      if(related)return related;
    }
    const previousField=help.previousElementSibling;
    if(previousField?.matches?.('.cf-field,.cf-toggle-field')){
      return previousField.querySelector('input,select,textarea');
    }
    const parentField=help.parentElement?.querySelector?.(':scope > .cf-field input,:scope > .cf-field select,:scope > .cf-field textarea,:scope > .cf-toggle-field input');
    return parentField||null;
  }
  function helpAnchorFor(control,container){
    if(container?.matches?.('.cf-field')){
      return container.querySelector(':scope > div:first-child')||container.querySelector('label')?.parentElement||container;
    }
    if(container?.matches?.('.cf-toggle-field')){
      return container.querySelector(':scope > div:first-child > div:first-child')||container.querySelector('label')?.parentElement||container;
    }
    const explicit=control?.id ? document.querySelector(`label[for="${CSS.escape(control.id)}"]`) : null;
    if(explicit){
      const title=explicit.querySelector('[data-form-label-title]');
      return title||explicit;
    }
    return control?.closest('label')||container;
  }
  function appendHelpContent(tip,help){
    const bubble=tip?.querySelector('[data-form-help-bubble]');
    if(!bubble||!help)return;
    const hasContent=[...bubble.childNodes].some(node=>
      node.nodeType!==Node.TEXT_NODE || (node.textContent||'').trim()
    );
    const detail=document.createElement('span');
    detail.className=hasContent?'form-help-tip__detail form-help-tip__detail--continued':'form-help-tip__detail';
    while(help.firstChild)detail.appendChild(help.firstChild);
    bubble.appendChild(detail);
    const originalId=help.id||'';
    if(originalId){
      if(!bubble.id){
        bubble.id=originalId;
        tip.querySelector('[data-form-help-trigger]')?.setAttribute('aria-controls',originalId);
      }else{
        detail.id=originalId;
      }
    }
  }
  function buildNativeHelpTip(help){
    if(!help||help.dataset.inproficHelpEnhanced==='1'||help.closest('[data-form-help-tip]'))return;
    help.dataset.inproficHelpEnhanced='1';

    const requestedAnchor=(help.dataset.formHelpAnchor||'').replace(/^#/,'').trim();
    const customAnchor=requestedAnchor ? document.getElementById(requestedAnchor) : null;
    const control=findControlForHelp(help);
    const container=fieldContainerForControl(control);
    let tip=customAnchor?.querySelector?.('[data-form-help-tip]')||container?.querySelector?.('[data-form-help-tip]')||null;

    if(!tip&&customAnchor){
      tip=createHelpTip((customAnchor.textContent||'').replace(/\s+/g,' ').trim());
      customAnchor.appendChild(tip);
    }

    if(!tip&&control){
      tip=createHelpTip(labelTextForControl(control));
      const anchor=helpAnchorFor(control,container);
      if(anchor)anchor.appendChild(tip);
    }

    if(tip){
      appendHelpContent(tip,help);
      help.remove();
      enhanceHelpTip(tip);
      return;
    }

    // Last-resort fallback for custom form markup with no discoverable control.
    // It is still a compact icon/popover instead of visible helper copy.
    tip=createHelpTip();
    appendHelpContent(tip,help);
    help.replaceWith(tip);
    enhanceHelpTip(tip);
  }
  function enhanceHelpTips(root=document){
    if(root.matches?.('[data-form-help-tip]'))enhanceHelpTip(root);
    root.querySelectorAll?.('[data-form-help-tip]').forEach(enhanceHelpTip);
    if(root.matches?.('form .helptext, form .help-text, form [data-form-help-text]'))buildNativeHelpTip(root);
    root.querySelectorAll?.('form .helptext, form .help-text, form [data-form-help-text]').forEach(buildNativeHelpTip);
  }
  function enhanceForms(root=document){
    enhanceHelpTips(root);
    root.querySelectorAll('form input,form select,form textarea').forEach(enhance);
    root.querySelectorAll('form').forEach(form=>{
      if(form.dataset.inproficValidationBound==='1')return; form.dataset.inproficValidationBound='1';
      form.addEventListener('submit',event=>{
        form.querySelectorAll('input,select,textarea').forEach(el=>{if(eligible(el)){el.dataset.inproficDirty='1';stateFor(el,true);}});
        if(!form.checkValidity()){event.preventDefault();event.stopPropagation();const first=form.querySelector(':invalid');first?.focus({preventScroll:true});first?.scrollIntoView({behavior:'smooth',block:'center'});}
      },true);
    });
  }
  document.addEventListener('DOMContentLoaded',()=>{
    enhanceForms();
    document.addEventListener('click',event=>{
      if(event.target.closest?.('[data-form-help-tip],[data-form-help-bubble]'))return;
      document.querySelectorAll('[data-form-help-tip].is-open').forEach(closeHelpTip);
    });
    document.addEventListener('keydown',event=>{
      if(event.key==='Escape')document.querySelectorAll('[data-form-help-tip].is-open').forEach(closeHelpTip);
    });
  });
  window.INPROFICEnhanceForms=enhanceForms;
  new MutationObserver(entries=>entries.forEach(entry=>entry.addedNodes.forEach(node=>{
    if(node.nodeType===1)enhanceForms(node);
  }))).observe(document.documentElement,{subtree:true,childList:true});
})();
