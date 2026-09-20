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
    // Naira rather than a generic dollar glyph.  Two cross-bars plus the
    // diagonal N remain legible at the 16px input-icon size.
    money:'<path data-draw d="M7 20V4l10 16V4M4 10h16M4 14h16"/>',
    user:'<circle data-draw cx="12" cy="8" r="4"/><path data-draw d="M4 21c1-5 4-7 8-7s7 2 8 7"/>',
    account:'<circle data-draw cx="12" cy="12" r="9"/><circle data-draw cx="12" cy="12" r="3"/><path data-draw d="M15 9v5c0 1.1.9 2 2 2 1.7 0 3-1.6 3-4"/>',
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

    const itemLike=/\b(product|raw material|material|finished good|item|category|service|plan|unit|role|package|pack|option|variant|title|business|company|supplier|warehouse|sku|code|name)\b/;
    if(itemLike.test(name.replaceAll('_',' '))||itemLike.test(label))return 'item';
    return '';
  }
  function svg(markup, cls, state){
    const ns='http://www.w3.org/2000/svg'; const node=document.createElementNS(ns,'svg');
    node.setAttribute('viewBox','0 0 24 24'); node.setAttribute('fill','none'); node.setAttribute('stroke','currentColor'); node.setAttribute('stroke-width','1.8'); node.setAttribute('aria-hidden','true'); node.setAttribute('class',cls);
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
    const key=iconFor(el); if(key){wrap.dataset.icon=key;wrap.appendChild(svg(icons[key],'inprofic-input-icon'));}
    wrap.classList.add('has-validation-mark');
    wrap.appendChild(svg('<path d="m5 12 4 4L19 6"/>','inprofic-validation-mark','valid'));
    wrap.appendChild(svg('<path d="M7 7l10 10M17 7 7 17"/>','inprofic-validation-mark','invalid'));
    const msg=document.createElement('span');msg.className='inprofic-client-message';msg.setAttribute('aria-live','polite');parent.insertBefore(msg,wrap.nextSibling);
    ['input','change'].forEach(type=>el.addEventListener(type,()=>{el.dataset.inproficDirty='1';stateFor(el,false);}));
    el.addEventListener('blur',()=>{el.dataset.inproficDirty='1';stateFor(el,false);});
    if(el.closest('.field-has-error')){el.dataset.inproficDirty='1';wrap.classList.add('is-invalid');}
  }
  function enhanceForms(root=document){
    root.querySelectorAll('form input,form select,form textarea').forEach(enhance);
    root.querySelectorAll('form').forEach(form=>{
      if(form.dataset.inproficValidationBound==='1')return; form.dataset.inproficValidationBound='1';
      form.addEventListener('submit',event=>{
        form.querySelectorAll('input,select,textarea').forEach(el=>{if(eligible(el)){el.dataset.inproficDirty='1';stateFor(el,true);}});
        if(!form.checkValidity()){event.preventDefault();event.stopPropagation();const first=form.querySelector(':invalid');first?.focus({preventScroll:true});first?.scrollIntoView({behavior:'smooth',block:'center'});}
      },true);
    });
  }
  document.addEventListener('DOMContentLoaded',()=>enhanceForms());
  window.INPROFICEnhanceForms=enhanceForms;
  new MutationObserver(entries=>entries.forEach(entry=>entry.addedNodes.forEach(node=>{if(node.nodeType===1)enhanceForms(node.matches?.('form')?node:node);}))).observe(document.documentElement,{subtree:true,childList:true});
})();
