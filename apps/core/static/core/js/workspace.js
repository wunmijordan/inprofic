(() => {
  const root = document.documentElement;
  const tenantId = root.dataset.tenant || 'global';
  const themeKey = 'inprofic-theme';
  const accentKey = `inprofic-accent:${tenantId}`;
  const backgroundKey = `inprofic-background:${tenantId}`;
  const darkCanvasKey = `inprofic-dark-canvas:${tenantId}`;
  const configuredAccent = root.dataset.tenantAccent || '#D14900';
  const configuredBackground = root.dataset.tenantBackground || '#050733';

  const mix = (hex, target, amount) => {
    const valid = /^#[0-9a-f]{6}$/i.test(hex) ? hex : configuredAccent;
    const source = [1,3,5].map(index => parseInt(valid.slice(index,index + 2),16));
    const end = target === 'white' ? 255 : 0;
    return `#${source.map(value => Math.round(value + (end - value) * amount).toString(16).padStart(2,'0')).join('')}`;
  };
  const contrast = hex => {
    const channels = [1,3,5].map(index => parseInt(hex.slice(index,index + 2),16));
    return ((channels[0] * 299 + channels[1] * 587 + channels[2] * 114) / 1000) > 150 ? '#182433' : '#ffffff';
  };
  const accentOptions = () => ({
    tenant: configuredAccent,
    tenantSoft: mix(configuredAccent,'white',.18),
    tenantDeep: mix(configuredAccent,'black',.18),
    inprofic: '#D14900',
    inproficSoft: '#E8783A',
    inproficDeep: '#9F3500',
    inproficWine: '#8F172D',
    inproficNavy: '#206BC4'
  });
  const backgroundOptions = () => ({
    tenant: configuredBackground,
    tenantSoft: mix(configuredBackground,'white',.14),
    tenantDeep: mix(configuredBackground,'black',.2),
    inprofic: '#050733',
    inproficSoft: '#171A52',
    inproficDeep: '#02031D'
  });
  const resolvedTheme = preference => preference === 'system'
    ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : preference;
  const applyTheme = preference => {
    const chosen = ['light','dark','system'].includes(preference) ? preference : 'system';
    root.dataset.themePreference = chosen;
    root.dataset.theme = resolvedTheme(chosen);
    document.querySelectorAll('[data-theme-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeChoice === chosen)));
  };
  const applyAccent = preference => {
    const options = accentOptions();
    const chosen = options[preference] ? preference : 'tenant';
    const color = options[chosen];
    root.dataset.accentPreference = chosen;
    root.style.setProperty('--ui-accent', color);
    root.style.setProperty('--tenant-button', color);
    root.style.setProperty('--tenant-button-contrast', contrast(color));
    document.querySelectorAll('[data-accent-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.accentChoice === chosen)));
  };
  const applyBackground = (preference, useDefaultDarkCanvas = false) => {
    const options = backgroundOptions();
    const chosen = options[preference] ? preference : 'tenant';
    const color = options[chosen];
    const channels = [1,3,5].map(index => parseInt(color.slice(index,index + 2),16));
    const luminance = (channels[0] * 299 + channels[1] * 587 + channels[2] * 114) / 1000;
    const darkBase = mix(color, 'black', luminance > 150 ? .72 : .28);
    root.dataset.backgroundPreference = chosen;
    root.style.setProperty('--tenant-background', color);
    root.style.setProperty('--tenant-background-contrast', contrast(color));
    // Canvas and surface choices are intentionally independent. The fixed
    // canvas stays neutral while primary cards keep the exact palette colour.
    root.style.setProperty('--ui-dark-bg', useDefaultDarkCanvas ? 'var(--ui-default-dark-canvas, #111827)' : mix(darkBase,'white',.18));
    root.style.setProperty('--ui-dark-surface', color);
    root.style.setProperty('--ui-dark-surface-muted', mix(color,'black',.08));
    root.style.setProperty('--ui-dark-border', mix(color,'white',.25));
    document.querySelectorAll('[data-background-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.backgroundChoice === chosen)));
    document.querySelectorAll('[data-dark-canvas-toggle]').forEach(toggle => { toggle.checked = useDefaultDarkCanvas; });
  };

  const stored = key => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  const remember = (key, value) => { try { localStorage.setItem(key, value); } catch (_) {} };
  // Migrate the former palette-based dark option without losing its behavior.
  if (stored(backgroundKey) === 'defaultDark') {
    remember(backgroundKey, 'tenant');
    remember(darkCanvasKey, 'true');
  }
  const backgroundPreference = () => stored(backgroundKey) || 'tenant';
  const usesDefaultDarkCanvas = () => stored(darkCanvasKey) === 'true';
  applyTheme(stored(themeKey) || 'system');
  applyAccent(stored(accentKey) || 'tenant');
  applyBackground(backgroundPreference(), usesDefaultDarkCanvas());
  matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => {
    if ((stored(themeKey) || 'system') === 'system') applyTheme('system');
  });

  document.addEventListener('DOMContentLoaded', () => {
    // The first application happens in <head> to avoid a theme flash; repeat
    // after parsing so controls expose the current selection accessibly.
    applyTheme(stored(themeKey) || 'system');
    applyAccent(stored(accentKey) || 'tenant');
    applyBackground(backgroundPreference(), usesDefaultDarkCanvas());
    document.querySelectorAll('[data-theme-choice]').forEach(button => button.addEventListener('click', () => {
      remember(themeKey, button.dataset.themeChoice); applyTheme(button.dataset.themeChoice);
    }));
    document.querySelectorAll('[data-accent-choice]').forEach(button => {
      const color = accentOptions()[button.dataset.accentChoice];
      if (color) button.style.backgroundColor = color;
      button.addEventListener('click', () => {
        remember(accentKey, button.dataset.accentChoice); applyAccent(button.dataset.accentChoice);
      });
    });
    document.querySelectorAll('[data-background-choice]').forEach(button => {
      const color = backgroundOptions()[button.dataset.backgroundChoice];
      if (color) button.style.backgroundColor = color;
      button.addEventListener('click', () => {
        remember(backgroundKey, button.dataset.backgroundChoice);
        applyBackground(button.dataset.backgroundChoice, usesDefaultDarkCanvas());
      });
    });
    document.querySelectorAll('[data-dark-canvas-toggle]').forEach(toggle => toggle.addEventListener('change', () => {
      const enabled = toggle.checked;
      remember(darkCanvasKey, String(enabled));
      applyBackground(backgroundPreference(), enabled);
    }));
    document.querySelectorAll('[data-appearance-toggle]').forEach(button => button.addEventListener('click', () => {
      const panel = document.getElementById(button.getAttribute('aria-controls'));
      if (!panel) return;
      const opening = panel.hasAttribute('hidden');
      panel.toggleAttribute('hidden', !opening); button.setAttribute('aria-expanded', String(opening));
    }));
    document.addEventListener('click', event => {
      if (event.target.closest('[data-appearance-root]')) return;
      document.querySelectorAll('[data-appearance-panel]').forEach(panel => panel.hidden = true);
      document.querySelectorAll('[data-appearance-toggle]').forEach(button => button.setAttribute('aria-expanded','false'));
    });

    const progress = document.querySelector('.page-progress');
    const eligible = link => {
      if (!link?.href || link.target || link.download || link.dataset.noPrefetch !== undefined) return false;
      const url = new URL(link.href, location.href);
      const unsafePath = /\/(logout|switch)(\/|$)/.test(url.pathname);
      return url.origin === location.origin && url.pathname !== location.pathname && !url.hash && !unsafePath;
    };
    document.addEventListener('click', event => {
      const link = event.target.closest('a');
      if (eligible(link) && progress) progress.dataset.active = 'true';
    });
    addEventListener('pageshow', () => { if (progress) progress.dataset.active = 'false'; });
  });
})();
