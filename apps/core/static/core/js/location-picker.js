(() => {
  const TILE = 256;
  const clampLat = value => Math.max(-85.0511288, Math.min(85.0511288, value));
  const clampZoom = value => Math.max(2, Math.min(18, value));
  const project = (lat, lng, zoom) => {
    const scale = TILE * (2 ** zoom);
    const bounded = clampLat(lat) * Math.PI / 180;
    return {
      x: (lng + 180) / 360 * scale,
      y: (1 - Math.log(Math.tan(bounded) + (1 / Math.cos(bounded))) / Math.PI) / 2 * scale,
    };
  };
  const unproject = (x, y, zoom) => {
    const scale = TILE * (2 ** zoom);
    const lng = ((x / scale) * 360) - 180;
    const n = Math.PI - (2 * Math.PI * y / scale);
    return {lat: 180 / Math.PI * Math.atan(Math.sinh(n)), lng: ((lng + 540) % 360) - 180};
  };

  function initialize(root) {
    if (root.dataset.mapReady === 'true') return;
    const latitude = document.querySelector(root.dataset.latitudeInput);
    const longitude = document.querySelector(root.dataset.longitudeInput);
    const toggle = root.querySelector('[data-map-toggle]');
    const panel = root.querySelector('[data-map-panel]');
    const canvas = root.querySelector('[data-map-canvas]');
    const tiles = root.querySelector('[data-map-tiles]');
    const status = root.querySelector('[data-map-status]');
    if (!latitude || !longitude || !toggle || !panel || !canvas || !tiles) return;

    const initialLatitude = Number(latitude.value);
    const initialLongitude = Number(longitude.value);
    const hasInitialCoordinates = Number.isFinite(initialLatitude) && Number.isFinite(initialLongitude)
      && latitude.value !== '' && longitude.value !== '';
    let center = {
      lat: hasInitialCoordinates ? initialLatitude : 0,
      lng: hasInitialCoordinates ? initialLongitude : 0,
    };
    let zoom = hasInitialCoordinates ? 13 : 2;
    let drag = null;
    let updatingInputs = false;

    function coordinatesValid() {
      return Number.isFinite(center.lat) && Number.isFinite(center.lng);
    }
    function setInputs(message) {
      if (!coordinatesValid()) return;
      updatingInputs = true;
      latitude.value = center.lat.toFixed(7);
      longitude.value = center.lng.toFixed(7);
      latitude.dispatchEvent(new Event('change', {bubbles: true}));
      longitude.dispatchEvent(new Event('change', {bubbles: true}));
      updatingInputs = false;
      status.textContent = message || `Selected ${center.lat.toFixed(5)}, ${center.lng.toFixed(5)}`;
    }
    function render() {
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (!width || !height) return;
      const point = project(center.lat, center.lng, zoom);
      const left = point.x - width / 2;
      const top = point.y - height / 2;
      const firstX = Math.floor(left / TILE);
      const lastX = Math.floor((left + width) / TILE);
      const firstY = Math.floor(top / TILE);
      const lastY = Math.floor((top + height) / TILE);
      const tileCount = 2 ** zoom;
      tiles.replaceChildren();
      for (let y = firstY; y <= lastY; y += 1) {
        if (y < 0 || y >= tileCount) continue;
        for (let x = firstX; x <= lastX; x += 1) {
          const image = document.createElement('img');
          const wrappedX = ((x % tileCount) + tileCount) % tileCount;
          image.src = `https://tile.openstreetmap.org/${zoom}/${wrappedX}/${y}.png`;
          image.alt = '';
          image.loading = 'lazy';
          image.referrerPolicy = 'origin';
          image.style.left = `${Math.round(x * TILE - left)}px`;
          image.style.top = `${Math.round(y * TILE - top)}px`;
          tiles.append(image);
        }
      }
    }
    function moveByPixels(dx, dy, message) {
      const point = project(center.lat, center.lng, zoom);
      center = unproject(point.x + dx, point.y + dy, zoom);
      setInputs(message);
      render();
    }
    function syncFromInputs() {
      if (updatingInputs) return;
      const lat = Number(latitude.value);
      const lng = Number(longitude.value);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
      center = {lat: clampLat(lat), lng: ((lng + 540) % 360) - 180};
      render();
    }

    toggle.addEventListener('click', () => {
      const opening = panel.hidden;
      panel.hidden = !opening;
      toggle.setAttribute('aria-expanded', String(opening));
      toggle.textContent = opening ? 'Hide map' : 'Choose on map';
      if (opening) requestAnimationFrame(render);
    });
    root.querySelector('[data-map-zoom-in]').addEventListener('click', () => { zoom = clampZoom(zoom + 1); render(); });
    root.querySelector('[data-map-zoom-out]').addEventListener('click', () => { zoom = clampZoom(zoom - 1); render(); });
    root.querySelector('[data-map-locate]').addEventListener('click', () => {
      if (!navigator.geolocation) { status.textContent = 'Current location is not available in this browser.'; return; }
      status.textContent = 'Requesting your current location…';
      navigator.geolocation.getCurrentPosition(position => {
        center = {lat: position.coords.latitude, lng: position.coords.longitude};
        zoom = 16;
        setInputs('Current location selected. You can fine-tune the pin on the map.');
        render();
      }, () => { status.textContent = 'Location access was unavailable. Click the map to place the pin manually.'; }, {enableHighAccuracy: true, timeout: 10000});
    });
    canvas.addEventListener('pointerdown', event => {
      drag = {id: event.pointerId, x: event.clientX, y: event.clientY, moved: false};
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      if (Math.abs(event.clientX - drag.x) + Math.abs(event.clientY - drag.y) > 5) drag.moved = true;
    });
    canvas.addEventListener('pointerup', event => {
      if (!drag || drag.id !== event.pointerId) return;
      canvas.releasePointerCapture(event.pointerId);
      if (drag.moved) moveByPixels(drag.x - event.clientX, drag.y - event.clientY, 'Map moved. The centre pin is the selected location.');
      else {
        const bounds = canvas.getBoundingClientRect();
        moveByPixels(event.clientX - bounds.left - bounds.width / 2, event.clientY - bounds.top - bounds.height / 2);
      }
      drag = null;
    });
    canvas.addEventListener('pointercancel', () => { drag = null; });
    canvas.addEventListener('keydown', event => {
      const movement = {
        ArrowUp: [0, -32],
        ArrowDown: [0, 32],
        ArrowLeft: [-32, 0],
        ArrowRight: [32, 0],
      }[event.key];
      if (movement) {
        event.preventDefault();
        moveByPixels(movement[0], movement[1], 'Map moved. The centre pin is the selected location.');
      } else if (event.key === '+' || event.key === '=') {
        event.preventDefault();
        zoom = clampZoom(zoom + 1);
        render();
      } else if (event.key === '-') {
        event.preventDefault();
        zoom = clampZoom(zoom - 1);
        render();
      }
    });
    latitude.addEventListener('change', syncFromInputs);
    longitude.addEventListener('change', syncFromInputs);
    window.addEventListener('resize', () => { if (!panel.hidden) render(); }, {passive: true});
    root.dataset.mapReady = 'true';
  }

  const boot = () => document.querySelectorAll('[data-location-picker]').forEach(initialize);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
