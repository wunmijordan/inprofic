/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './assets/ui/**/*.{ts,tsx}',
    './apps/**/*.html',
    './apps/**/*.py',
    './storetrack/**/*.py',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Sora', 'sans-serif'],
        display: ['Fraunces', 'Georgia', 'serif'],
        mono: ['IBM Plex Mono', 'monospace'],
      },
      colors: {
        border: 'var(--ui-border)', input: 'var(--ui-border)', ring: 'var(--ui-accent)',
        background: 'var(--ui-surface)', foreground: 'var(--ui-surface-text)',
        primary: { DEFAULT: 'var(--ui-accent)', foreground: 'var(--ui-accent-contrast)' },
        secondary: { DEFAULT: 'var(--ui-surface-muted)', foreground: 'var(--ui-surface-text)' },
        muted: { DEFAULT: 'var(--ui-surface-muted)', foreground: 'var(--ui-surface-muted-text)' },
        accent: { DEFAULT: 'var(--ui-surface-muted)', foreground: 'var(--ui-surface-text)' },
        popover: { DEFAULT: 'var(--ui-surface)', foreground: 'var(--ui-surface-text)' },
        destructive: { DEFAULT: '#be123c', foreground: '#ffffff' },
      },
    },
  },
  plugins: [],
};
