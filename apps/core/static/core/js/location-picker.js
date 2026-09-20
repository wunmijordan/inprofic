(() => {
  const TILE = 256;
  const EARTH_KM = 6371.0088;
  const clampLat = value => Math.max(-85.0511288, Math.min(85.0511288, value));
  const clampZoom = value => Math.max(2, Math.min(18, value));
  const project = (lat, lng, zoom) => {
    const scale = TILE * (2 ** zoom);
    const bounded = clampLat(lat) * Math.PI / 180;
    return {x:(lng + 180) / 360 * scale,y:(1 - Math.log(Math.tan(bounded) + (1 / Math.cos(bounded))) / Math.PI) / 2 * scale};
  };
  const unproject = (x, y, zoom) => {
    const scale = TILE * (2 ** zoom); const lng = ((x / scale) * 360) - 180;
    const n = Math.PI - (2 * Math.PI * y / scale);
    return {lat:180 / Math.PI * Math.atan(Math.sinh(n)),lng:((lng + 540) % 360) - 180};
  };
  const haversine = (a, b) => {
    const rad = value => value * Math.PI / 180;
    const dLat = rad(b.lat - a.lat), dLng = rad(b.lng - a.lng);
    const value = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2;
    return EARTH_KM * 2 * Math.asin(Math.sqrt(value));
  };
  const destination = (point, distanceKm, bearing) => {
    const angular = distanceKm / EARTH_KM, angle = bearing * Math.PI / 180;
    const lat = point.lat * Math.PI / 180, lng = point.lng * Math.PI / 180;
    const targetLat = Math.asin(Math.sin(lat) * Math.cos(angular) + Math.cos(lat) * Math.sin(angular) * Math.cos(angle));
    const targetLng = lng + Math.atan2(Math.sin(angle) * Math.sin(angular) * Math.cos(lat), Math.cos(angular) - Math.sin(lat) * Math.sin(targetLat));
    return {lat:targetLat * 180 / Math.PI,lng:((targetLng * 180 / Math.PI + 540) % 360) - 180};
  };
  const angularDelta = (first, second) => Math.abs((first - second + 180) % 360 - 180);
  const reachAt = (area, bearing) => {
    const directions = [[45,area.ne],[135,area.se],[225,area.sw],[315,area.nw]];
    const nearest = directions.reduce((best, row) => angularDelta(bearing,row[0]) < angularDelta(bearing,best[0]) ? row : best);
    return area.radius + nearest[1] * Math.max(0, 1 - angularDelta(bearing, nearest[0]) / 45);
  };

  function initialize(root) {
    if (root.dataset.mapReady === 'true') return;
    const latitude = document.querySelector(root.dataset.latitudeInput);
    const longitude = document.querySelector(root.dataset.longitudeInput);
    const radiusInput = root.dataset.radiusInput ? document.querySelector(root.dataset.radiusInput) : null;
    const areaInput = root.dataset.areaInput ? document.querySelector(root.dataset.areaInput) : null;
    const addressInput = root.dataset.addressInput ? document.querySelector(root.dataset.addressInput) : null;
    const resolveUrl = root.dataset.resolveUrl || '';
    const extensionInputs = Object.fromEntries(['ne','se','sw','nw'].map(key => [key, document.getElementById(`id_extension_${key}_km`)]));
    const toggle = root.querySelector('[data-map-toggle]'), panel = root.querySelector('[data-map-panel]');
    const canvas = root.querySelector('[data-map-canvas]'), tiles = root.querySelector('[data-map-tiles]');
    const overlays = root.querySelector('[data-map-overlays]'), status = root.querySelector('[data-map-status]');
    if (!latitude || !longitude || !toggle || !panel || !canvas || !tiles || !overlays) return;
    const areas = [...root.querySelectorAll('[data-map-area]')].map(node => ({
      id:node.dataset.areaId,name:node.dataset.areaName,lat:Number(node.dataset.areaLatitude),lng:Number(node.dataset.areaLongitude),
      radius:Number(node.dataset.areaRadius)||0,ne:Number(node.dataset.areaNe)||0,se:Number(node.dataset.areaSe)||0,sw:Number(node.dataset.areaSw)||0,nw:Number(node.dataset.areaNw)||0,
    })).filter(area => Number.isFinite(area.lat) && Number.isFinite(area.lng));
    const initialLatitude = Number(latitude.value), initialLongitude = Number(longitude.value);
    const hasInitialCoordinates = Number.isFinite(initialLatitude) && Number.isFinite(initialLongitude) && latitude.value !== '' && longitude.value !== '';
    let center = {lat:hasInitialCoordinates ? initialLatitude : 0,lng:hasInitialCoordinates ? initialLongitude : 0};
    let zoom = hasInitialCoordinates ? 13 : 2, drag = null, updatingInputs = false, coverageDrag = null, addressTimer = null, resolveController = null, resolveSequence = 0;

    const setStatus = (message, isError=false) => {
      if (!status) return;
      status.textContent = message || '';
      status.classList.toggle('text-rose-700', isError);
      status.classList.toggle('font-semibold', isError);
      status.classList.toggle('text-stone-500', !isError);
    };
    const openPanel = () => {
      if (!panel.hidden) return;
      panel.hidden = false;
      toggle.setAttribute('aria-expanded','true');
      toggle.textContent = 'Hide map';
      requestAnimationFrame(render);
    };
    const editableArea = () => radiusInput ? {
      lat:Number(latitude.value),lng:Number(longitude.value),radius:Number(radiusInput.value)||0,
      ne:Number(extensionInputs.ne?.value)||0,se:Number(extensionInputs.se?.value)||0,sw:Number(extensionInputs.sw?.value)||0,nw:Number(extensionInputs.nw?.value)||0,
    } : null;
    const screenPoint = (point, left, top) => { const projected = project(point.lat,point.lng,zoom); return {x:projected.x-left,y:projected.y-top}; };
    function coveragePoints(area,left,top) {
      const points=[];
      for(let bearing=0;bearing<360;bearing+=5){const point=screenPoint(destination(area,reachAt(area,bearing),bearing),left,top);points.push(`${point.x.toFixed(1)},${point.y.toFixed(1)}`);}
      return points.join(' ');
    }
    function renderOverlays(left,top,width,height) {
      overlays.replaceChildren();
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('width',width);svg.setAttribute('height',height);svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
      const selectedId=areaInput?.value||'';
      areas.forEach(area=>{
        const selected=area.id===selectedId;
        const polygon=document.createElementNS('http://www.w3.org/2000/svg','polygon');
        polygon.setAttribute('points',coveragePoints(area,left,top));
        polygon.setAttribute('fill',selected?'rgba(209,73,0,.23)':'rgba(209,73,0,.10)');
        polygon.setAttribute('stroke','var(--ui-accent,var(--brand,#d14900))');
        polygon.setAttribute('stroke-width',selected?'3':'1.5');
        polygon.setAttribute('stroke-dasharray',selected?'':'5 4');
        svg.append(polygon);
        const centre=screenPoint(area,left,top);
        const dot=document.createElementNS('http://www.w3.org/2000/svg','circle');
        dot.setAttribute('cx',centre.x);dot.setAttribute('cy',centre.y);dot.setAttribute('r',selected?'4':'3');
        dot.setAttribute('fill','var(--ui-accent,var(--brand,#d14900))');dot.setAttribute('stroke','#fff');dot.setAttribute('stroke-width','1.5');svg.append(dot);
        const label=document.createElementNS('http://www.w3.org/2000/svg','text');
        label.setAttribute('x',centre.x+7);label.setAttribute('y',centre.y-7);label.setAttribute('font-size',selected?'12':'10');label.setAttribute('font-weight',selected?'700':'600');label.setAttribute('fill','#292524');label.setAttribute('paint-order','stroke');label.setAttribute('stroke','#fff');label.setAttribute('stroke-width','3');label.setAttribute('stroke-linejoin','round');label.textContent=area.name;svg.append(label);
      });
      const editable=editableArea();
      if(editable&&Number.isFinite(editable.lat)&&Number.isFinite(editable.lng)&&editable.radius>0){const polygon=document.createElementNS('http://www.w3.org/2000/svg','polygon');polygon.setAttribute('points',coveragePoints(editable,left,top));polygon.setAttribute('fill','rgba(209,73,0,.18)');polygon.setAttribute('stroke','var(--ui-accent,var(--brand,#d14900))');polygon.setAttribute('stroke-width','3');svg.append(polygon);}
      overlays.append(svg);
      if(editable&&Number.isFinite(editable.lat)&&Number.isFinite(editable.lng)&&editable.radius>0){const handles={radius:[90,editable.radius],ne:[45,editable.radius+editable.ne],se:[135,editable.radius+editable.se],sw:[225,editable.radius+editable.sw],nw:[315,editable.radius+editable.nw]};Object.entries(handles).forEach(([key,[bearing,distance]])=>{const node=key==='radius'?root.querySelector('[data-radius-handle]'):root.querySelector(`[data-extension-handle="${key}"]`);if(!node)return;const point=screenPoint(destination(editable,distance,bearing),left,top);node.style.left=`${point.x}px`;node.style.top=`${point.y}px`;node.hidden=false;});}
    }
    function render() {
      const width=canvas.clientWidth,height=canvas.clientHeight;if(!width||!height)return;
      const point=project(center.lat,center.lng,zoom),left=point.x-width/2,top=point.y-height/2;
      const firstX=Math.floor(left/TILE),lastX=Math.floor((left+width)/TILE),firstY=Math.floor(top/TILE),lastY=Math.floor((top+height)/TILE),tileCount=2**zoom;tiles.replaceChildren();
      for(let y=firstY;y<=lastY;y+=1){if(y<0||y>=tileCount)continue;for(let x=firstX;x<=lastX;x+=1){const image=document.createElement('img'),wrappedX=((x%tileCount)+tileCount)%tileCount;image.src=`https://tile.openstreetmap.org/${zoom}/${wrappedX}/${y}.png`;image.alt='';image.loading='lazy';image.referrerPolicy='origin';image.style.left=`${Math.round(x*TILE-left)}px`;image.style.top=`${Math.round(y*TILE-top)}px`;tiles.append(image);}}
      renderOverlays(left,top,width,height);
    }
    function setInputs(message) {
      if(!Number.isFinite(center.lat)||!Number.isFinite(center.lng))return;
      updatingInputs=true;latitude.value=center.lat.toFixed(7);longitude.value=center.lng.toFixed(7);
      latitude.dispatchEvent(new Event('change',{bubbles:true}));longitude.dispatchEvent(new Event('change',{bubbles:true}));updatingInputs=false;
      setStatus(message||`Selected ${center.lat.toFixed(5)}, ${center.lng.toFixed(5)}`);
    }
    function emitResolved(data){root.dispatchEvent(new CustomEvent('locationpicker:resolved',{bubbles:true,detail:data}));}
    function csrfToken(){return root.closest('form')?.querySelector('[name="csrfmiddlewaretoken"]')?.value||'';}
    async function resolveLocation(mode){
      if(!resolveUrl)return;
      const address=addressInput?.value.trim()||'';
      if(mode==='address'&&address.length<4)return;
      const payload=new FormData();const csrf=csrfToken();if(csrf)payload.append('csrfmiddlewaretoken',csrf);if(areaInput?.value)payload.append('area_id',areaInput.value);
      if(mode==='address'){payload.append('address',address);setStatus('Locating the address on the map…');}
      else{payload.append('latitude',center.lat.toFixed(7));payload.append('longitude',center.lng.toFixed(7));if(address)payload.append('address',address);setStatus('Finding the address for this map pin…');}
      const sequence=++resolveSequence;
      if(resolveController)resolveController.abort();
      resolveController=new AbortController();
      try{
        const response=await fetch(resolveUrl,{method:'POST',body:payload,headers:{Accept:'application/json'},signal:resolveController.signal});const data=await response.json();
        if(sequence!==resolveSequence)return;
        if(!response.ok)throw new Error(data.detail||'Location could not be validated.');
        center={lat:Number(data.latitude),lng:Number(data.longitude)};zoom=17;openPanel();setInputs();render();
        if(addressInput&&data.address){addressInput.value=data.address;addressInput.dispatchEvent(new Event('change',{bubbles:true}));}
        setStatus(data.validated_by==='map_pin'?(data.address_resolved?'Map pin located and address updated.':'Map pin is valid, but no precise street address was returned.'):'Address located. The map pin is synchronized to this destination.',!data.address_resolved&&data.validated_by==='map_pin');
        emitResolved(data);
      }catch(error){
        if(error.name==='AbortError'||sequence!==resolveSequence)return;
        setStatus(error.message||'Location could not be validated.',true);emitResolved({error:true,detail:error.message||'Location could not be validated.'});
      }finally{if(sequence===resolveSequence)resolveController=null;}
    }
    function moveByPixels(dx,dy,message,reverse=true){const point=project(center.lat,center.lng,zoom);center=unproject(point.x+dx,point.y+dy,zoom);setInputs(message);render();if(reverse&&resolveUrl)resolveLocation('pin');}
    function syncFromInputs(){if(updatingInputs)return;const lat=Number(latitude.value),lng=Number(longitude.value);if(!Number.isFinite(lat)||!Number.isFinite(lng))return;center={lat:clampLat(lat),lng:((lng+540)%360)-180};render();}
    function pointFromEvent(event){const bounds=canvas.getBoundingClientRect(),world=project(center.lat,center.lng,zoom);return unproject(world.x+(event.clientX-bounds.left-bounds.width/2),world.y+(event.clientY-bounds.top-bounds.height/2),zoom);}
    function updateCoverage(event){const editable=editableArea();if(!editable||!coverageDrag)return;const distance=Math.min(500,haversine(editable,pointFromEvent(event)));const input=coverageDrag==='radius'?radiusInput:extensionInputs[coverageDrag];if(!input)return;input.value=(coverageDrag==='radius'?Math.max(.1,distance):Math.max(0,distance-editable.radius)).toFixed(2);input.dispatchEvent(new Event('change',{bubbles:true}));setStatus(coverageDrag==='radius'?`Coverage radius: ${input.value} km`:`${coverageDrag.toUpperCase()} extra reach: ${input.value} km`);render();}
    toggle.addEventListener('click',()=>{const opening=panel.hidden;panel.hidden=!opening;toggle.setAttribute('aria-expanded',String(opening));toggle.textContent=opening?'Hide map':'Choose on map';if(opening)requestAnimationFrame(render);});
    root.querySelector('[data-map-zoom-in]').addEventListener('click',()=>{zoom=clampZoom(zoom+1);render();});root.querySelector('[data-map-zoom-out]').addEventListener('click',()=>{zoom=clampZoom(zoom-1);render();});
    root.querySelector('[data-map-locate]').addEventListener('click',()=>{if(!navigator.geolocation){setStatus('Current location is not available in this browser.',true);return;}setStatus('Requesting your current location…');navigator.geolocation.getCurrentPosition(position=>{center={lat:position.coords.latitude,lng:position.coords.longitude};zoom=17;openPanel();setInputs('Current location selected.');render();if(resolveUrl)resolveLocation('pin');},()=>{setStatus('Location access was unavailable. Click the map to place the pin manually.',true);},{enableHighAccuracy:true,timeout:10000});});
    canvas.addEventListener('pointerdown',event=>{if(event.target.closest('[data-radius-handle],[data-extension-handle]'))return;drag={id:event.pointerId,x:event.clientX,y:event.clientY,moved:false};canvas.setPointerCapture(event.pointerId);});
    canvas.addEventListener('pointermove',event=>{if(coverageDrag){updateCoverage(event);return;}if(!drag||drag.id!==event.pointerId)return;if(Math.abs(event.clientX-drag.x)+Math.abs(event.clientY-drag.y)>5)drag.moved=true;});
    canvas.addEventListener('pointerup',event=>{if(coverageDrag){updateCoverage(event);coverageDrag=null;return;}if(!drag||drag.id!==event.pointerId)return;canvas.releasePointerCapture(event.pointerId);if(drag.moved)moveByPixels(drag.x-event.clientX,drag.y-event.clientY,'Map pin moved. Updating the address…');else{const bounds=canvas.getBoundingClientRect();moveByPixels(event.clientX-bounds.left-bounds.width/2,event.clientY-bounds.top-bounds.height/2,'Map pin selected. Updating the address…');}drag=null;});
    canvas.addEventListener('pointercancel',()=>{drag=null;coverageDrag=null;});
    root.querySelectorAll('[data-radius-handle],[data-extension-handle]').forEach(handle=>handle.addEventListener('pointerdown',event=>{event.preventDefault();event.stopPropagation();coverageDrag=handle.dataset.extensionHandle||'radius';handle.setPointerCapture(event.pointerId);}));
    canvas.addEventListener('keydown',event=>{const movement={ArrowUp:[0,-32],ArrowDown:[0,32],ArrowLeft:[-32,0],ArrowRight:[32,0]}[event.key];if(movement){event.preventDefault();moveByPixels(movement[0],movement[1],'Map pin moved. Updating the address…');}else if(event.key==='+'||event.key==='='){event.preventDefault();zoom=clampZoom(zoom+1);render();}else if(event.key==='-'){event.preventDefault();zoom=clampZoom(zoom-1);render();}});
    latitude.addEventListener('change',syncFromInputs);longitude.addEventListener('change',syncFromInputs);radiusInput?.addEventListener('change',render);Object.values(extensionInputs).forEach(input=>input?.addEventListener('change',render));
    const ADDRESS_IDLE_DELAY_MS=2000;
    function cancelAddressLookup(){
      clearTimeout(addressTimer);
      addressTimer=null;
      // Any correction/edit invalidates both the scheduled lookup and an older
      // request already in flight, so stale suggestions can never rewrite the
      // customer's newer address.
      resolveSequence+=1;
      if(resolveController){resolveController.abort();resolveController=null;}
    }
    function scheduleAddressLookup(){
      if(!resolveUrl)return;
      cancelAddressLookup();
      if((addressInput?.value.trim()||'').length<4)return;
      addressTimer=setTimeout(()=>{addressTimer=null;resolveLocation('address');},ADDRESS_IDLE_DELAY_MS);
    }
    addressInput?.addEventListener('input',scheduleAddressLookup);
    areaInput?.addEventListener('change',()=>{const area=areas.find(row=>row.id===areaInput.value);if(!area){render();return;}center={lat:area.lat,lng:area.lng};zoom=13;const reach=Math.max(area.radius+area.ne,area.radius+area.se,area.radius+area.sw,area.radius+area.nw);while(zoom>2&&reach/(156.54303392*Math.cos(area.lat*Math.PI/180)/(2**zoom))>canvas.clientWidth*.36)zoom-=1;openPanel();render();setStatus(`${area.name} coverage is outlined. Enter the exact address or place the pin inside it.`);if(resolveUrl&&addressInput?.value.trim().length>=4)scheduleAddressLookup();});
    window.addEventListener('resize',()=>{if(!panel.hidden)render();},{passive:true});root.dataset.mapReady='true';
  }
  const boot=()=>document.querySelectorAll('[data-location-picker]').forEach(initialize);if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
