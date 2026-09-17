import type { Config } from 'tailwindcss';

/** Cool, quiet research-workbench tokens. Colour communicates state while the
 * chrome stays close to an editor/notebook rather than a card dashboard. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: 'rgb(var(--bg) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        panel: 'rgb(var(--panel) / <alpha-value>)',
        'panel-raised': 'rgb(var(--panel-raised) / <alpha-value>)',
        line: 'rgb(var(--line) / <alpha-value>)',
        blue: {
          DEFAULT: 'rgb(var(--blue) / <alpha-value>)',
          deep: 'rgb(var(--blue-deep) / <alpha-value>)',
          sky: 'rgb(var(--blue-sky) / <alpha-value>)',
        },
        gold: {
          DEFAULT: 'rgb(var(--gold) / <alpha-value>)',
          soft: 'rgb(var(--gold-soft) / <alpha-value>)',
          deep: 'rgb(var(--gold-deep) / <alpha-value>)',
        },
        // semantic
        ok: 'rgb(var(--ok) / <alpha-value>)',
        warn: 'rgb(var(--warn) / <alpha-value>)',
        err: 'rgb(var(--err) / <alpha-value>)',
        ink: {
          DEFAULT: 'rgb(var(--ink) / <alpha-value>)',
          dim: 'rgb(var(--ink-dim) / <alpha-value>)',
          faint: 'rgb(var(--ink-faint) / <alpha-value>)',
        },
        manager: 'rgb(var(--role-manager) / <alpha-value>)',
        planner: 'rgb(var(--role-planner) / <alpha-value>)',
        engineer: 'rgb(var(--role-engineer) / <alpha-value>)',
        reviewer: 'rgb(var(--role-reviewer) / <alpha-value>)',
        conversation: {
          user: 'rgb(var(--conversation-user) / <alpha-value>)',
          argus: 'rgb(var(--conversation-argus) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['Geist Variable', 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['Geist Mono Variable', 'SFMono-Regular', 'Menlo', 'ui-monospace', 'monospace'],
      },
      borderRadius: { xl: '0.75rem' },
      boxShadow: {
        glow: '0 16px 44px rgba(0,0,0,0.34)',
      },
      keyframes: {
        appear: {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'appear-right': {
          '0%': { opacity: '0', transform: 'translateX(8px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
      },
      animation: {
        appear: 'appear 200ms cubic-bezier(0.4, 0, 0.2, 1)',
        'appear-right': 'appear-right 200ms cubic-bezier(0.4, 0, 0.2, 1)',
      },
      transitionTimingFunction: {
        panel: 'cubic-bezier(0.4, 0, 0.2, 1)',
      },
      transitionDuration: {
        panel: '220ms',
      },
    },
  },
  plugins: [],
} satisfies Config;
