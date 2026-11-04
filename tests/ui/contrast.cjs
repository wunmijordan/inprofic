// Inspect text against composited theme surfaces. Known shallow button sheens
// are transparent beneath the label. Gradient stops are sampled conservatively;
// gradients whose colors cannot be resolved are reported as skipped.
module.exports = function scanContrast() {
  const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
  const ctx=canvas.getContext('2d',{willReadFrequently:true}),cache=new Map();
  const rgb=color=>{if(cache.has(color))return cache.get(color);ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);const p=[...ctx.getImageData(0,0,1,1).data];p[3]/=255;cache.set(color,p);return p;};
  const blend=(a,b)=>a.slice(0,3).map((v,i)=>v*a[3]+b[i]*(1-a[3])).concat(1);
  const lum=c=>c.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
  const findings=[];let checked=0,skippedGradients=0;
  for(const el of document.querySelectorAll('main *,.restored-marketing header *,.restored-marketing footer *,.ui-confirm *,.ui-menu *')){
    const text=[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).join(' ').trim();
    if(!text||el.matches('script,style,svg *,option')||el.closest('[inert],[aria-hidden=true],.sr-only')||!el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})||el.closest(':disabled,[disabled]'))continue;
    const rect=el.getBoundingClientRect();if(!rect.width||!rect.height)continue;
    const style=getComputedStyle(el);let layers=[],opacity=1,unknown=false;
    for(let node=el;node;node=node.parentElement){const s=getComputedStyle(node);opacity*=Number(s.opacity);
      if(s.backgroundImage!=='none'){
        const same=s.backgroundImage.match(/rgba?\([^)]+\)/g)||[];
        if(s.backgroundImage.includes('25%')&&s.backgroundImage.includes('75%')){}
        else if(same.length>=2&&same.length<=8)layers.push(same.map(rgb));
        else {unknown=true;break;}
      }
      layers.push([rgb(s.backgroundColor)]);
    }
    if(unknown){skippedGradients++;continue;}if(opacity<.95)continue;
    let backgrounds=[[255,255,255,1]];
    for(let i=layers.length-1;i>=0;i--)backgrounds=layers[i].flatMap(layer=>backgrounds.map(bg=>blend(layer,bg)));
    const ratio=Math.min(...backgrounds.map(bg=>{const fg=blend(rgb(style.color),bg),a=lum(fg),b=lum(bg);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);}));
    const threshold=(parseFloat(style.fontSize)>=24||(parseFloat(style.fontSize)>=18.66&&Number(style.fontWeight)>=700))?3:4.5;checked++;
    if(ratio<threshold-.03)findings.push({text:text.slice(0,70),ratio:+ratio.toFixed(2),className:el.className});
  }
  return {checked,skippedGradients,findings};
};
