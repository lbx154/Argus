import type { Config } from 'tailwindcss';

/** Neutral graphite workbench tokens. Colour communicates state; it is not
 * decoration. No gradients, neon glows, or candy role palette. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // warm graphite surfaces
        bg: '#111210',
        surface: '#171815',
        panel: '#1d1e1a',
        line: '#32332d',
        // restrained action + attention colours
        blue: { DEFAULT: '#8fa7b8', deep: '#607d91', sky: '#b0c3cf' },
        gold: { DEFAULT: '#c7a66a', soft: '#ddc99e', deep: '#a88955' },
        // semantic
        ok: '#7fa386',
        warn: '#c1a363',
        err: '#c77b72',
        // text
        ink: { DEFAULT: '#efeee8', dim: '#b8b7af', faint: '#7e7d75' },
        manager: '#90a8b5',
        planner: '#a69daf',
        engineer: '#8fa78f',
        reviewer: '#b5a57f',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: { xl: '0.5rem' },
      boxShadow: {
        glow: '0 14px 36px rgba(0,0,0,0.28)',
      },
    },
  },
  plugins: [],
} satisfies Config;
