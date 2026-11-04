/* Choose transparent brand artwork against the actual composited surface.
   A theme name alone is insufficient for pale tenant palettes in dark mode. */
(() => {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const rgba = color => {
    ctx.clearRect(0,0,1,1); ctx.fillStyle = color; ctx.fillRect(0,0,1,1);
    const value = [...ctx.getImageData(0,0,1,1).data]; value[3] /= 255; return value;
  };
  const luminance = color => color.slice(0,3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum,v,i) => sum + v * [.2126,.7152,.0722][i],0);
  function update() {
    document.querySelectorAll('img[data-brand-logo-light][data-brand-logo-dark]').forEach(img => {
      const layers = [];
      for (let node = img.parentElement; node; node = node.parentElement) layers.push(rgba(getComputedStyle(node).backgroundColor));
      let color = [255,255,255,1];
      layers.reverse().forEach(layer => { color = layer.slice(0,3).map((v,i) => v * layer[3] + color[i] * (1-layer[3])).concat(1); });
      const lightSurface = luminance(color) >= .6;
      const source = lightSurface ? img.dataset.brandLogoLight : img.dataset.brandLogoDark;
      if (img.getAttribute('src') !== source) img.setAttribute('src',source);
      img.dataset.brandSurface = lightSurface ? 'light' : 'dark';
    });
  }
  let queued = false;
  const schedule = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; update(); }); } };
  const boot = () => {
    update(); new MutationObserver(schedule).observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['class','style','hidden','open','data-theme']});
    matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change',schedule);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',boot); else boot();
})();
