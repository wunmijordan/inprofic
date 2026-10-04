/* Shell alerts/notifications script, moved out of base.html so the browser caches it
   instead of re-downloading ~40 KB of identical JavaScript inside every page.
   Per-page values (static asset URLs) arrive via window.INPROFIC_SHELL.assets. */
(function(){window.shellAsset=function(path){var c=window.INPROFIC_SHELL||{};return (c.assets&&c.assets[path])||'';};})();
    document.addEventListener('DOMContentLoaded', function () {
      const dock = document.getElementById('commerce-notification-dock');
      if (!dock) return;
      const hasCommerce = dock.dataset.hasCommerce === '1';
      const hasInventory = dock.dataset.hasInventory === '1';
      const hasFinance = dock.dataset.hasFinance === '1';
      let commerceAvailable = hasCommerce;
      let inventoryAvailable = hasInventory;
      let financeAvailable = hasFinance;
      const trayBody = document.getElementById('notification-tray-body');
      const expand = document.getElementById('commerce-notification-expand');
      const minimize = document.getElementById('commerce-notification-minimize');
      const totalCount = document.getElementById('notification-tray-total-count');
      const totalPlural = document.getElementById('notification-tray-total-plural');
      const dragHandle = document.getElementById('commerce-notification-drag-handle');
      const channelButtons = Array.from(dock.querySelectorAll('[data-alert-channel]'));
      const panels = Array.from(dock.querySelectorAll('[data-alert-panel]'));
      const commerceCount = document.getElementById('commerce-notification-count');
      const inventoryCount = document.getElementById('inventory-alert-count');
      const financeCount = document.getElementById('finance-alert-count');
      const financeList = document.getElementById('finance-alert-list');
      const financeSummary = document.getElementById('finance-alert-summary');
      const commerceList = document.getElementById('commerce-notification-list');
      const commerceReadAll = document.getElementById('commerce-notification-read-all');
      const rawList = document.getElementById('inventory-alert-raw-list');
      const finishedList = document.getElementById('inventory-alert-finished-list');
      const rawCount = document.getElementById('inventory-raw-count');
      const finishedCount = document.getElementById('inventory-finished-count');
      const inventoryTabs = Array.from(dock.querySelectorAll('[data-inventory-tab]'));
      let expanded = false;
      let activeChannel = window.localStorage.getItem('inprofic-alert-tray-channel') || (hasCommerce ? 'commerce' : (hasInventory ? 'inventory' : 'finance'));
      let activeInventoryTab = window.localStorage.getItem('inprofic-inventory-alert-tab') || 'raw';
      let commerceData = { enabled: false, unread_count: 0, notifications: [], sound_enabled: false, sound_repeat_minutes: 0, sound_tune: 'double_ping' };
      let inventoryData = { enabled: false, count: 0, raw_count: 0, finished_count: 0, alerts: [], sound_enabled: false, sound_repeat_minutes: 0, sound_tune: 'urgent_pulse' };
      let financeData = { enabled: false, count: 0, invoice_count: 0, receivable_count: 0, payable_count: 0, balancing_count: 0, alerts: [] };
      let commerceTimer = null;
      let inventoryTimer = null;
      let financeTimer = null;
      let socket = null;
      let socketConnected = false;
      let reconnectTimer = null;
      let reconnectAttempt = 0;
      let pageClosing = false;
      let refreshSequence = 0;
      let latestCommerceId = null;
      let commerceInitialized = false;
      let previousCommerceIds = new Set();
      let previousInventoryIds = new Set();
      let suppressNextCommerceDesktop = false;

      const soundState = {
        commerce: { active: false, enabled: false, repeat: 0, signature: '', tune: 'double_ping', nextDueAt: 0, burstTimers: [] },
        inventory: { active: false, enabled: false, repeat: 0, signature: '', tune: 'urgent_pulse', nextDueAt: 0, burstTimers: [] },
      };
      let audioContext = null;
      let audioUnlocked = false;
      let audioGestureSeen = false;
      let alertKeepAlive = null;
      let alarmHeartbeat = null;
      const pendingSounds = new Set();
      const customAlertAudioFiles = Object.freeze({
        audio_chime_1: shellAsset('core/audio/alerts/chime1.mp3'),
        audio_chime_2: shellAsset('core/audio/alerts/chime2.mp3'),
        audio_chime_3: shellAsset('core/audio/alerts/chime3.mp3'),
        audio_chime_4: shellAsset('core/audio/alerts/chime4.mp3'),
        audio_chime_5: shellAsset('core/audio/alerts/chime5.mp3'),
        audio_chime_6: shellAsset('core/audio/alerts/chime6.wav'),
        audio_chime_7: shellAsset('core/audio/alerts/chime7.wav'),
        audio_chime_8: shellAsset('core/audio/alerts/chime8.wav'),
      });
      const customAlertBuffers = new Map();
      const customAlertBufferLoads = new Map();
      const customAlertPlayers = new Map();

      const positionKey = 'inprofic-alert-tray-left';
      const storedDockLeft = window.localStorage.getItem(positionKey);
      const legacyDockLeft = window.localStorage.getItem('inprofic-commerce-dock-left');
      let mobileDockMoved = false;
      function dockWidth() {
        const measured = dock.getBoundingClientRect().width;
        if (measured > 0) return measured;
        if (window.innerWidth <= 767 && !expanded) return Math.min(224, window.innerWidth - 24);
        return Math.min(352, window.innerWidth - 24);
      }
      function clampDockLeft(left) {
        const margin = 12;
        return Math.max(margin, Math.min(left, window.innerWidth - dockWidth() - margin));
      }
      function centerMobileDock() {
        dock.style.right = 'auto';
        dock.style.left = '50%';
        dock.style.transform = 'translateX(-50%)';
      }
      function restoreDockPosition() {
        if (window.innerWidth <= 767) {
          centerMobileDock();
          return;
        }
        const raw = storedDockLeft !== null ? storedDockLeft : legacyDockLeft;
        const stored = raw === null ? NaN : Number(raw);
        if (!Number.isFinite(stored)) {
          dock.style.left = '';
          dock.style.right = '.75rem';
          dock.style.transform = '';
          return;
        }
        dock.style.right = 'auto';
        dock.style.transform = '';
        dock.style.left = `${clampDockLeft(stored)}px`;
      }
      function reconcileDockPosition() {
        if (window.innerWidth <= 767 && !mobileDockMoved) {
          centerMobileDock();
        } else if (dock.style.left) {
          const rect = dock.getBoundingClientRect();
          dock.style.transform = '';
          dock.style.left = `${clampDockLeft(rect.left)}px`;
        }
      }
      if (dragHandle) {
        let dragStartX = 0;
        let dragStartLeft = 0;
        let dragTargetLeft = 0;
        let dragFrame = 0;
        const paintDrag = () => {
          dragFrame = 0;
          dock.style.transform = `translate3d(${dragTargetLeft - dragStartLeft}px,0,0)`;
        };
        dragHandle.addEventListener('pointerdown', function (event) {
          if (event.target.closest('button, a, input, select, textarea')) return;
          const rect = dock.getBoundingClientRect();
          dragStartX = event.clientX;
          dragStartLeft = rect.left;
          dragTargetLeft = rect.left;
          dock.style.right = 'auto';
          dock.style.left = `${rect.left}px`;
          dock.style.transform = 'translate3d(0,0,0)';
          dock.style.transition = 'none';
          dragHandle.setPointerCapture(event.pointerId);
        });
        dragHandle.addEventListener('pointermove', function (event) {
          if (!dragHandle.hasPointerCapture(event.pointerId)) return;
          dragTargetLeft = clampDockLeft(dragStartLeft + event.clientX - dragStartX);
          if (!dragFrame) dragFrame = requestAnimationFrame(paintDrag);
        });
        const finishDrag = function (event) {
          if (!dragHandle.hasPointerCapture(event.pointerId)) return;
          dragHandle.releasePointerCapture(event.pointerId);
          if (dragFrame) { cancelAnimationFrame(dragFrame); dragFrame = 0; }
          const finalLeft = clampDockLeft(dragTargetLeft);
          dock.style.transform = '';
          dock.style.left = `${finalLeft}px`;
          dock.style.transition = '';
          if (window.innerWidth <= 767) mobileDockMoved = true;
          window.localStorage.setItem(positionKey, String(Math.round(finalLeft)));
        };
        dragHandle.addEventListener('pointerup', finishDrag);
        dragHandle.addEventListener('pointercancel', finishDrag);
      }
      window.addEventListener('resize', function () {
        reconcileDockPosition();
      });
      dock.addEventListener('transitionend', function (event) {
        if (event.propertyName === 'width') reconcileDockPosition();
      });
      restoreDockPosition();

      function csrfToken() {
        const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
      }
      function channelIsAvailable(channel) {
        return channel === 'commerce' ? commerceAvailable : (channel === 'inventory' ? inventoryAvailable : financeAvailable);
      }
      function selectChannel(channel, userChoice) {
        if (!channelIsAvailable(channel)) return;
        activeChannel = channel;
        if (userChoice) window.localStorage.setItem('inprofic-alert-tray-channel', channel);
        channelButtons.forEach(button => {
          const active = button.dataset.alertChannel === channel;
          button.classList.toggle('shadow-sm', active);
          button.classList.toggle('text-stone-600', !active);
          button.style.backgroundColor = active ? 'var(--tenant-background)' : '';
          button.style.color = active ? 'var(--tenant-background-contrast)' : '';
          button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        panels.forEach(panel => {
          const active = panel.dataset.alertPanel === channel && expanded;
          panel.classList.toggle('hidden', !active);
          panel.classList.toggle('flex', active);
        });
      }
      function setExpanded(value) {
        expanded = !!value;
        dock.dataset.expanded = expanded ? 'true' : 'false';
        trayBody.classList.toggle('hidden', !expanded);
        trayBody.classList.toggle('flex', expanded);
        trayBody.setAttribute('aria-hidden', expanded ? 'false' : 'true');
        expand.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        minimize.classList.toggle('rotate-180', !expanded);
        minimize.setAttribute('aria-label', expanded ? 'Minimize activity alerts' : 'Expand activity alerts');
        selectChannel(activeChannel, false);
        requestAnimationFrame(reconcileDockPosition);
      }
      function selectInventoryTab(tab, userChoice) {
        activeInventoryTab = tab === 'finished' ? 'finished' : 'raw';
        if (userChoice) window.localStorage.setItem('inprofic-inventory-alert-tab', activeInventoryTab);
        inventoryTabs.forEach(button => {
          const active = button.dataset.inventoryTab === activeInventoryTab;
          button.classList.toggle('text-stone-600', !active);
          button.style.backgroundColor = active ? 'var(--tenant-background)' : '';
          button.style.color = active ? 'var(--tenant-background-contrast)' : '';
          button.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        if (rawList) rawList.classList.toggle('hidden', activeInventoryTab !== 'raw');
        if (finishedList) finishedList.classList.toggle('hidden', activeInventoryTab !== 'finished');
      }

      function ensureAudioContext() {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return null;
        if (!audioContext) audioContext = new AudioContext();
        return audioContext;
      }
      function anyAudibleAlertActive() {
        return Object.values(soundState).some(state => state.active && state.enabled && state.repeat > 0);
      }
      function startAudioKeepAlive() {
        const audio = ensureAudioContext();
        if (!audio || audio.state !== 'running' || alertKeepAlive || !anyAudibleAlertActive()) return;
        try {
          const oscillator = audio.createOscillator();
          const gain = audio.createGain();
          gain.gain.value = 0.000001;
          oscillator.frequency.value = 18;
          oscillator.connect(gain);
          gain.connect(audio.destination);
          oscillator.start();
          alertKeepAlive = { oscillator, gain };
        } catch (_error) { alertKeepAlive = null; }
      }
      function stopAudioKeepAlive() {
        if (!alertKeepAlive) return;
        try { alertKeepAlive.oscillator.stop(); } catch (_error) {}
        try { alertKeepAlive.oscillator.disconnect(); alertKeepAlive.gain.disconnect(); } catch (_error) {}
        alertKeepAlive = null;
      }
      function syncAudioKeepAlive() {
        if (anyAudibleAlertActive()) startAudioKeepAlive(); else stopAudioKeepAlive();
      }
      function customAudioPlayer(channel, tune) {
        const url = customAlertAudioFiles[tune];
        if (!url || typeof Audio === 'undefined') return null;
        const key = `${channel}:${tune}`;
        let player = customAlertPlayers.get(key);
        if (!player) {
          player = new Audio(url);
          player.preload = 'auto';
          player.playsInline = true;
          customAlertPlayers.set(key, player);
        }
        return player;
      }
      async function loadCustomAlertBuffer(tune) {
        const url = customAlertAudioFiles[tune];
        const audio = ensureAudioContext();
        if (!url || !audio) return null;
        if (customAlertBuffers.has(tune)) return customAlertBuffers.get(tune);
        if (customAlertBufferLoads.has(tune)) return customAlertBufferLoads.get(tune);
        const pending = fetch(url, { credentials:'same-origin', cache:'force-cache' })
          .then(response => { if (!response.ok) throw new Error('alert audio fetch failed'); return response.arrayBuffer(); })
          .then(bytes => audio.decodeAudioData(bytes.slice(0)))
          .then(buffer => { customAlertBuffers.set(tune, buffer); customAlertBufferLoads.delete(tune); return buffer; })
          .catch(() => { customAlertBufferLoads.delete(tune); return null; });
        customAlertBufferLoads.set(tune, pending);
        return pending;
      }
      function drainPendingSounds() {
        if (!pendingSounds.size) return;
        const channels = Array.from(pendingSounds);
        pendingSounds.clear();
        channels.forEach((channel, index) => window.setTimeout(() => playAlertSound(channel), index * 450));
      }
      async function unlockAudio(event) {
        if (event?.isTrusted) audioGestureSeen = true;
        const audio = ensureAudioContext();
        try {
          if (audio && audio.state !== 'running') await audio.resume();
          audioUnlocked = !!audio && audio.state === 'running';
        } catch (_error) { audioUnlocked = false; }
        if (audioUnlocked) {
          Object.values(soundState).forEach(state => { if (customAlertAudioFiles[state.tune]) loadCustomAlertBuffer(state.tune); });
          syncAudioKeepAlive();
        }
        if (audioGestureSeen || audioUnlocked) drainPendingSounds();
        runAlarmHeartbeat(true);
      }
      function tone(audio, frequency, start, duration, volume, type='sine') {
        const oscillator = audio.createOscillator();
        const gain = audio.createGain();
        oscillator.type = type;
        oscillator.frequency.setValueAtTime(frequency, start);
        gain.gain.setValueAtTime(.0001, start);
        gain.gain.exponentialRampToValueAtTime(volume, start + .015);
        gain.gain.exponentialRampToValueAtTime(.0001, start + duration);
        oscillator.connect(gain); gain.connect(audio.destination);
        oscillator.start(start); oscillator.stop(start + duration + .02);
      }
      function playTune(audio, tune, start) {
        if (tune === 'gentle_chime') { tone(audio, 660, start, .28, .10, 'sine'); tone(audio, 880, start + .22, .34, .08, 'sine'); return; }
        if (tune === 'urgent_pulse') { tone(audio, 760, start, .16, .14, 'triangle'); tone(audio, 760, start + .22, .16, .14, 'triangle'); tone(audio, 980, start + .44, .22, .15, 'triangle'); return; }
        if (tune === 'hard_buzzer') { tone(audio, 190, start, .42, .19, 'square'); tone(audio, 150, start + .48, .42, .19, 'square'); return; }
        if (tune === 'alarm_buzzer') { tone(audio, 210, start, .32, .22, 'sawtooth'); tone(audio, 520, start + .34, .32, .20, 'square'); tone(audio, 210, start + .68, .36, .22, 'sawtooth'); return; }
        tone(audio, 780, start, .22, .13, 'sine'); tone(audio, 1040, start + .25, .25, .13, 'sine');
      }
      function playDecodedBuffer(audio, buffer) {
        const source = audio.createBufferSource();
        const gain = audio.createGain();
        gain.gain.value = .9;
        source.buffer = buffer;
        source.connect(gain); gain.connect(audio.destination);
        source.start(audio.currentTime + .01);
      }
      async function playAlertSound(channel) {
        const state = soundState[channel];
        if (!state || !state.active || !state.enabled) return false;
        const tune = state.tune || 'double_ping';
        const audio = ensureAudioContext();
        if (audio && audio.state !== 'running') {
          try { await audio.resume(); } catch (_error) {}
        }
        if (audio && audio.state === 'running') {
          audioUnlocked = true;
          pendingSounds.delete(channel);
          startAudioKeepAlive();
          if (customAlertAudioFiles[tune]) {
            const buffer = await loadCustomAlertBuffer(tune);
            if (buffer && state.active && state.enabled) { playDecodedBuffer(audio, buffer); return true; }
          } else {
            playTune(audio, tune, audio.currentTime + .01);
            return true;
          }
        }
        // Fallback for browsers that permit HTML media but have not resumed
        // Web Audio. If blocked, keep it queued for the first trusted gesture.
        const player = customAudioPlayer(channel, tune);
        if (player) {
          try { player.pause(); player.currentTime = 0; } catch (_error) {}
          try {
            await player.play();
            pendingSounds.delete(channel);
            return true;
          } catch (_error) { pendingSounds.add(channel); return false; }
        }
        pendingSounds.add(channel);
        return false;
      }
      function clearAlarmBurst(channel) {
        const state = soundState[channel];
        if (!state) return;
        (state.burstTimers || []).forEach(timer => clearTimeout(timer));
        state.burstTimers = [];
      }
      function playAlarmBurst(channel) {
        const state = soundState[channel];
        if (!state || !state.active || !state.enabled) return;
        clearAlarmBurst(channel);
        // A due alert behaves like a short alarm burst instead of a single easy-
        // to-miss ping. The configured interval still controls when the next
        // burst is due, and read/snooze immediately cancels the remaining burst.
        playAlertSound(channel);
        [4200, 8400].forEach(delay => {
          const timer = window.setTimeout(() => {
            if (state.active && state.enabled) playAlertSound(channel);
          }, delay);
          state.burstTimers.push(timer);
        });
      }
      function armNextAlarm(channel, fromNow=true) {
        const state = soundState[channel];
        if (!state || !state.active || !state.enabled || !state.repeat) { if (state) state.nextDueAt = 0; return; }
        const interval = state.repeat * 60 * 1000;
        if (fromNow || !state.nextDueAt) state.nextDueAt = Date.now() + interval;
      }
      function runAlarmHeartbeat(force=false) {
        const now = Date.now();
        Object.entries(soundState).forEach(([channel, state]) => {
          if (!state.active || !state.enabled || !state.repeat) return;
          if (!state.nextDueAt) armNextAlarm(channel, true);
          if (state.nextDueAt && now >= state.nextDueAt) {
            playAlarmBurst(channel);
            armNextAlarm(channel, true);
          }
        });
        syncAudioKeepAlive();
      }
      function ensureAlarmHeartbeat() {
        if (!alarmHeartbeat) alarmHeartbeat = window.setInterval(() => runAlarmHeartbeat(false), 5000);
      }
      function syncPersistentSound(channel, options) {
        const state = soundState[channel];
        const oldEnabled = state.enabled;
        const oldRepeat = state.repeat;
        const oldTune = state.tune;
        state.active = !!options.active;
        state.enabled = !!options.enabled;
        state.repeat = Math.max(0, Number(options.repeat || 0));
        state.signature = options.signature || '';
        state.tune = options.tune || state.tune || 'double_ping';
        if (!state.active || !state.enabled) {
          state.nextDueAt = 0;
          clearAlarmBurst(channel);
          pendingSounds.delete(channel);
          syncAudioKeepAlive();
          return;
        }
        ensureAlarmHeartbeat();
        if (customAlertAudioFiles[state.tune]) loadCustomAlertBuffer(state.tune);
        const soundConfigChanged = oldEnabled !== state.enabled || oldRepeat !== state.repeat || oldTune !== state.tune;
        if (options.immediate) {
          playAlarmBurst(channel);
          armNextAlarm(channel, true);
        } else if (soundConfigChanged || !state.nextDueAt) {
          armNextAlarm(channel, true);
        }
        syncAudioKeepAlive();
      }
      document.addEventListener('pointerdown', unlockAudio, { once: true, capture: true });
      document.addEventListener('touchstart', unlockAudio, { once: true, capture: true, passive: true });
      document.addEventListener('keydown', unlockAudio, { once: true, capture: true });
      // Timers can be heavily throttled in background tabs. Reconcile against
      // absolute due-times when the page becomes active again, rather than
      // relying on one recursive timeout surviving indefinitely.
      document.addEventListener('visibilitychange', () => { if (!document.hidden) { unlockAudio(); runAlarmHeartbeat(true); } });
      window.addEventListener('focus', () => { unlockAudio(); runAlarmHeartbeat(true); });
      window.addEventListener('pageshow', () => { unlockAudio(); runAlarmHeartbeat(true); });
      window.setTimeout(() => { unlockAudio(); runAlarmHeartbeat(true); }, 0);

      function updateDock() {
        const cCount = commerceAvailable && commerceData.enabled ? Number(commerceData.unread_count || 0) : 0;
        const iCount = inventoryAvailable && inventoryData.enabled ? Number(inventoryData.count || 0) : 0;
        const fCount = financeAvailable && financeData.enabled ? Number(financeData.count || 0) : 0;
        const total = cCount + iCount + fCount;
        if (commerceCount) commerceCount.textContent = cCount;
        if (inventoryCount) inventoryCount.textContent = iCount;
        if (financeCount) financeCount.textContent = fCount;
        totalCount.textContent = total;
        totalPlural.textContent = total === 1 ? '' : 's';
        if (!total) {
          dock.classList.add('hidden');
          setExpanded(false);
          return;
        }
        dock.classList.remove('hidden');
        requestAnimationFrame(reconcileDockPosition);
        if (!channelIsAvailable(activeChannel)) activeChannel = commerceAvailable ? 'commerce' : (inventoryAvailable ? 'inventory' : 'finance');
        if (!expanded) selectChannel(activeChannel, false);
      }

      function desktopAlert(item) {
        if (!('Notification' in window) || Notification.permission !== 'granted') return;
        const alert = new Notification(item.title, { body: item.message, tag: item.id, renotify: true, icon: shellAsset('core/pwa/icon-mark-192.png'), badge: shellAsset('core/pwa/icon-mark-monochrome-192.png') });
        alert.onclick = function () { window.focus(); window.location.href = item.target_url || '/commerce/'; alert.close(); };
      }
      function renderCommerce(data) {
        commerceData = data;
        document.dispatchEvent(new CustomEvent('inprofic:commerce-notifications', { detail: data }));
        const notices = Array.isArray(data.notifications) ? data.notifications : [];
        if (commerceList) commerceList.replaceChildren();
        if (data.enabled && notices.length && commerceList) {
          notices.forEach(item => {
            const row = document.createElement('article');
            row.className = 'px-4 py-3 transition hover:bg-stone-50';
            const top = document.createElement('div'); top.className = 'flex items-start justify-between gap-3';
            const copy = document.createElement('div'); copy.className = 'min-w-0';
            const label = document.createElement('span');
            label.className = (item.event_type === 'payment_review' || item.event_type === 'payment_claim') ? 'inline-flex rounded-full bg-rose-100 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider text-rose-800' : 'inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider text-amber-900';
            label.textContent = item.event_type === 'payment_claim' ? 'Needs verification' : item.event_type === 'payment_review' ? 'Needs attention' : item.event_type.replaceAll('_', ' ');
            const heading = document.createElement('h2'); heading.className = 'mt-1.5 text-sm font-semibold leading-5 text-stone-900'; heading.textContent = item.title;
            const detail = document.createElement('p'); detail.className = 'mt-0.5 text-xs leading-4 text-stone-500'; detail.textContent = item.message;
            copy.append(label, heading, detail);
            const itemTime = document.createElement('time'); itemTime.className = 'shrink-0 pt-0.5 text-[10px] text-stone-400'; itemTime.textContent = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', month: 'short', day: 'numeric' }).format(new Date(item.created_at));
            top.append(copy, itemTime);
            const actions = document.createElement('div'); actions.className = 'mt-2 flex items-center gap-3';
            const openItem = document.createElement('a'); openItem.href = item.target_url || '/commerce/'; openItem.className = 'text-[11px] font-semibold text-[#d14900] hover:underline'; openItem.textContent = 'Open';
            const readItem = document.createElement('button'); readItem.type = 'button'; readItem.dataset.notificationId = item.id; readItem.className = 'commerce-notification-read text-[11px] font-semibold text-stone-400 hover:text-stone-800'; readItem.textContent = 'Mark read';
            actions.append(openItem, readItem); row.append(top, actions); commerceList.append(row);
          });
        }
        const newest = notices[0] || null;
        if (commerceInitialized && newest && latestCommerceId !== newest.id && !suppressNextCommerceDesktop && data.desktop_enabled) desktopAlert(newest);
        suppressNextCommerceDesktop = false;
        latestCommerceId = newest ? newest.id : null;
        const currentCommerceIds = new Set(notices.map(item => item.id));
        const commerceHasNew = notices.length > 0 && (!commerceInitialized || notices.some(item => !previousCommerceIds.has(item.id)));
        syncPersistentSound('commerce', {
          active: !!(data.enabled && data.unread_count && notices.length), enabled: data.sound_enabled,
          repeat: data.sound_repeat_minutes, tune: data.sound_tune, signature: newest ? newest.id : '', immediate: commerceHasNew,
        });
        previousCommerceIds = currentCommerceIds;
        commerceInitialized = true;
        updateDock();
      }
      function inventoryRow(item) {
        const palette = {
          raw_warning: ['Raw material','Warning','bg-amber-100 text-amber-900','border-amber-200'], raw_low: ['Raw material','LOW','bg-rose-100 text-rose-800','border-rose-300'],
          finished_warning: ['Finished good','Warning','bg-yellow-100 text-amber-950','border-yellow-300'], finished_low: ['Finished good','LOW','bg-red-100 text-red-900','border-red-300'],
        };
        const meta = palette[item.type] || ['Stock','Alert','bg-stone-100 text-stone-800','border-stone-200'];
        const row = document.createElement('article'); row.className = `p-3 border-l-4 ${meta[3]} bg-white`;
        const badges = document.createElement('div'); badges.className = 'flex flex-wrap gap-1.5';
        const resource = document.createElement('span'); resource.className = 'rounded-full bg-stone-100 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider text-stone-700'; resource.textContent = meta[0];
        const severity = document.createElement('span'); severity.className = `rounded-full px-2 py-0.5 text-[9px] font-black uppercase tracking-wider ${meta[2]}`; severity.textContent = meta[1];
        badges.append(resource, severity);
        const title = document.createElement('h3'); title.className = 'mt-1.5 text-sm font-semibold text-stone-900'; title.textContent = item.item_name;
        const detail = document.createElement('p'); detail.className = 'mt-0.5 text-xs leading-5 text-stone-500'; detail.textContent = `Stock ${item.stock} ${item.unit} · reorder at ${item.reorder_level} ${item.unit}`;
        const actions = document.createElement('div'); actions.className = 'mt-2 flex gap-3';
        const open = document.createElement('a'); open.href = item.target_url; open.className = 'text-[11px] font-semibold text-[#d14900] hover:underline'; open.textContent = 'Open inventory';
        const ack = document.createElement('button'); ack.type = 'button'; ack.dataset.alertId = item.id; ack.className = 'inventory-alert-ack text-[11px] font-semibold text-stone-500 hover:text-stone-900'; ack.textContent = 'Acknowledge for now';
        actions.append(open, ack); row.append(badges, title, detail, actions); return row;
      }
      function renderInventory(data) {
        inventoryData = data;
        const alerts = Array.isArray(data.alerts) ? data.alerts : [];
        if (rawList) rawList.replaceChildren();
        if (finishedList) finishedList.replaceChildren();
        alerts.forEach(item => {
          const target = item.resource === 'finished_good' ? finishedList : rawList;
          if (target) target.append(inventoryRow(item));
        });
        if (rawCount) rawCount.textContent = Number(data.raw_count || 0);
        if (finishedCount) finishedCount.textContent = Number(data.finished_count || 0);
        const resource = data.raw_count && data.finished_count ? 'both' : data.finished_count ? 'finished' : 'raw';
        const currentInventoryIds = new Set(alerts.map(item => item.id));
        const inventoryHasNew = alerts.some(item => !previousInventoryIds.has(item.id));
        syncPersistentSound('inventory', {
          active: !!(data.enabled && data.count && alerts.length), enabled: data.sound_enabled,
          repeat: data.sound_repeat_minutes, tune: data.sound_tune, signature: alerts.map(item => item.id).sort().join('|'), immediate: inventoryHasNew,
        });
        previousInventoryIds = currentInventoryIds;
        if (activeInventoryTab === 'raw' && !data.raw_count && data.finished_count) activeInventoryTab = 'finished';
        else if (activeInventoryTab === 'finished' && !data.finished_count && data.raw_count) activeInventoryTab = 'raw';
        selectInventoryTab(activeInventoryTab, false);
        updateDock();
      }

      function renderFinance(data) {
        financeData = data;
        const alerts = Array.isArray(data.alerts) ? data.alerts : [];
        if (financeList) financeList.replaceChildren();
        if (financeSummary) financeSummary.textContent = `${Number(data.invoice_count || 0)} unpaid · ${Number(data.receivable_count || 0)} receivable · ${Number(data.payable_count || 0)} payable · ${Number(data.balancing_count || 0)} balancing`;
        if (financeList) alerts.forEach(item => {
          const row = document.createElement('article'); row.className = 'px-4 py-3 transition hover:bg-stone-50';
          const badge = document.createElement('span');
          const badgeMap = { invoice:'bg-orange-100 text-orange-900', receivable:'bg-amber-100 text-amber-900', payable:'bg-rose-100 text-rose-800', balancing:'bg-sky-100 text-sky-900' };
          badge.className = `inline-flex rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider ${badgeMap[item.type] || 'bg-stone-100 text-stone-700'}`;
          badge.textContent = item.type === 'balancing' ? 'Balance check' : item.type;
          const heading = document.createElement('h3'); heading.className = 'mt-1.5 text-sm font-semibold leading-5 text-stone-900'; heading.textContent = item.title;
          const detail = document.createElement('p'); detail.className = 'mt-0.5 text-xs leading-5 text-stone-500'; detail.textContent = item.message;
          const open = document.createElement('a'); open.href = item.target_url || '/finance/'; open.className = 'mt-2 inline-block text-[11px] font-semibold text-[#d14900] hover:underline'; open.textContent = 'Review in Finance';
          row.append(badge, heading, detail, open); financeList.append(row);
        });
        updateDock();
      }
      async function pollFinance() {
        if (!financeAvailable || pageClosing) return;
        try {
          const response = await fetch(dock.dataset.financeFeedUrl, { headers: { Accept: 'application/json' }, cache: 'no-store' });
          if (response.ok) {
            const data = await response.json(); renderFinance(data);
            clearTimeout(financeTimer); financeTimer = setTimeout(pollFinance, Math.max(30, Math.min(300, data.poll_seconds || 45)) * 1000); return;
          }
          if (response.status === 401 || response.status === 403) {
            financeAvailable = false;
            const button = document.getElementById('notification-channel-finance'); if (button) button.classList.add('hidden');
            updateDock(); return;
          }
        } catch (_error) { /* Retry below. */ }
        clearTimeout(financeTimer); financeTimer = setTimeout(pollFinance, 45000);
      }

      async function pollCommerce() {
        if (!commerceAvailable || pageClosing) return;
        const sequence = ++refreshSequence;
        try {
          const response = await fetch(dock.dataset.feedUrl, { headers: { Accept: 'application/json' }, cache: 'no-store' });
          if (response.ok) {
            const data = await response.json();
            if (sequence !== refreshSequence) return;
            renderCommerce(data);
            clearTimeout(commerceTimer);
            if (!socketConnected) commerceTimer = setTimeout(pollCommerce, Math.max(5, data.poll_seconds || 8) * 1000);
            return;
          }
          if (response.status === 401 || response.status === 403) {
            commerceAvailable = false;
            const button = document.getElementById('notification-channel-commerce'); if (button) button.classList.add('hidden');
            updateDock(); return;
          }
        } catch (_error) { /* Retry below. */ }
        clearTimeout(commerceTimer); commerceTimer = setTimeout(pollCommerce, 15000);
      }
      async function pollInventory() {
        if (!inventoryAvailable || pageClosing) return;
        try {
          const response = await fetch(dock.dataset.inventoryFeedUrl, { headers: { Accept: 'application/json' }, cache: 'no-store' });
          if (response.ok) {
            const data = await response.json(); renderInventory(data);
            clearTimeout(inventoryTimer); inventoryTimer = setTimeout(pollInventory, Math.max(15, Math.min(300, data.poll_seconds || 45)) * 1000); return;
          }
          if (response.status === 401 || response.status === 403) {
            inventoryAvailable = false;
            const button = document.getElementById('notification-channel-inventory'); if (button) button.classList.add('hidden');
            updateDock(); return;
          }
        } catch (_error) { /* Retry below. */ }
        clearTimeout(inventoryTimer); inventoryTimer = setTimeout(pollInventory, 30000);
      }
      function connectSocket() {
        if (!commerceAvailable || !('WebSocket' in window) || pageClosing || socket) return;
        const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const connection = new WebSocket(`${scheme}//${window.location.host}${dock.dataset.socketPath}`); socket = connection;
        connection.addEventListener('open', () => { socketConnected = true; reconnectAttempt = 0; clearTimeout(commerceTimer); clearTimeout(reconnectTimer); pollCommerce(); });
        connection.addEventListener('message', event => {
          try {
            const message = JSON.parse(event.data);
            if (message.type === 'notifications.changed') pollCommerce();
            if (message.type === 'delivery.changed') document.dispatchEvent(new CustomEvent('inprofic:delivery-changed', { detail: message }));
          } catch (_error) { /* Ignore malformed transport messages. */ }
        });
        connection.addEventListener('close', () => {
          if (socket === connection) socket = null; socketConnected = false;
          if (pageClosing || !commerceAvailable) return;
          clearTimeout(commerceTimer); commerceTimer = setTimeout(pollCommerce, 1000);
          const delay = Math.min(30000, 1000 * (2 ** reconnectAttempt)) + Math.random() * 500; reconnectAttempt += 1;
          clearTimeout(reconnectTimer); reconnectTimer = setTimeout(connectSocket, delay);
        });
        connection.addEventListener('error', () => connection.close());
      }
      async function acknowledgeCommerce(payload) {
        if (!commerceAvailable || !commerceList) return;
        const buttons = commerceList.querySelectorAll('.commerce-notification-read'); buttons.forEach(button => { button.disabled = true; });
        if (commerceReadAll) commerceReadAll.disabled = true;
        try {
          const response = await fetch(dock.dataset.readUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken(), Accept: 'application/json' }, body: JSON.stringify(payload) });
          if (response.ok) { suppressNextCommerceDesktop = true; await pollCommerce(); }
        } finally {
          buttons.forEach(button => { button.disabled = false; }); if (commerceReadAll) commerceReadAll.disabled = false;
        }
      }
      async function acknowledgeInventory(id) {
        if (!inventoryAvailable) return;
        const response = await fetch(dock.dataset.inventoryAckUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken(), Accept: 'application/json' }, body: JSON.stringify({ alert_ids: [id] }) });
        if (response.ok) pollInventory();
      }

      channelButtons.forEach(button => button.addEventListener('click', () => { selectChannel(button.dataset.alertChannel, true); setExpanded(true); }));
      inventoryTabs.forEach(button => button.addEventListener('click', () => selectInventoryTab(button.dataset.inventoryTab, true)));
      expand.addEventListener('click', () => setExpanded(!expanded));
      minimize.addEventListener('click', () => setExpanded(!expanded));
      if (commerceList) commerceList.addEventListener('click', event => { const button = event.target.closest('.commerce-notification-read'); if (button) acknowledgeCommerce({ notification_ids: [button.dataset.notificationId] }); });
      if (commerceReadAll) commerceReadAll.addEventListener('click', () => acknowledgeCommerce({ all: true }));
      [rawList, finishedList].filter(Boolean).forEach(list => list.addEventListener('click', event => { const button = event.target.closest('.inventory-alert-ack'); if (button) acknowledgeInventory(button.dataset.alertId); }));
      document.addEventListener('click', event => { if (expanded && !dock.contains(event.target)) setExpanded(false); });
      document.addEventListener('keydown', event => { if (event.key === 'Escape') setExpanded(false); });
      document.addEventListener('visibilitychange', () => { if (!document.hidden) { if (commerceAvailable) { pollCommerce(); connectSocket(); } if (inventoryAvailable) pollInventory(); if (financeAvailable) pollFinance(); } });
      window.addEventListener('pagehide', () => {
        pageClosing = true; clearTimeout(commerceTimer); clearTimeout(inventoryTimer); clearTimeout(financeTimer); clearTimeout(reconnectTimer);
        clearTimeout(soundState.commerce.timer); clearTimeout(soundState.inventory.timer); if (socket) socket.close(); if (audioContext) audioContext.close();
      });

      selectInventoryTab(activeInventoryTab, false);
      selectChannel(activeChannel, false);
      const startAlerts = () => {
        if (commerceAvailable) { if ('WebSocket' in window) connectSocket(); else pollCommerce(); }
        if (inventoryAvailable) pollInventory();
        if (financeAvailable) pollFinance();
      };
      if ('requestIdleCallback' in window) window.requestIdleCallback(startAlerts, { timeout: 1000 });
      else window.setTimeout(startAlerts, 250);
    });
  
