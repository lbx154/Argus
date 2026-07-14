import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import fs from 'node:fs';
import path from 'node:path';
import {
  activeGuardianAlert,
  authoritativeSpend,
  computeSpend,
  defaultProject,
  reconcileProjectSelection,
  deriveMissionView,
  EVENT_TYPES,
  eventKey,
  eventMatchesView,
  filterProjects,
  canonicalEventType,
  resolveProjectSelection,
  responseError,
  visibleBacklogItems,
} from '../../../core/src';
import { formatBytes } from '../lib/format';
import { filterPaletteItems, type PaletteItem } from '../components/CommandPalette';
import type { UsageRecordedEvent } from '../../../core/src/eventPayloads.generated';
import { selectPreferredLiveArtifact } from '../components/ResearchCanvas';
import { MarkdownContent } from '../components/MarkdownContent';
import { BootSplash, WEB_SPLASH_DURATION_MS } from '../components/BootSplash';
import { PendingReplyDialog } from '../components/PendingReplyDialog';
import { Sidebar } from '../components/Sidebar';
import { BackendHandshake } from '../components/BackendHandshake';
import { motionDistance, motionDuration, motionQueries } from '../lib/motion';
import { activeProviderRequest } from '../components/EventStream';
import { HtmlPreview } from '../components/HtmlPreview';
import { formatStructuredData, parseDelimited } from '../components/DataPreview';
import { Button } from '../components/primitives';

const typedUsageEvent: UsageRecordedEvent = {
  type: 'usage.recorded',
  payload_schema_version: 2,
  call_id: 'call-1',
  schema_version: 2,
  provider: 'codex',
  status: 'completed',
  usage: {},
  pricing: {},
};

describe('shared frontend core', () => {
  it('defines the public-brand workbench surface contract', () => {
    const css = fs.readFileSync(path.resolve('src/index.css'), 'utf8');
    for (const token of [
      '--spectral-blue',
      '--spectral-violet',
      '--spectral-rose',
      '--spectral-gold',
      '--glass',
      '--glass-raised',
      '--glass-edge',
    ]) {
      expect(css).toContain(token);
    }
    expect(css).toContain('.workbench-shell');
    expect(css).toContain('.glass-panel');
    expect(css).toContain('.glass-card');
  });

  it('renders branded shared button variants without changing semantics', () => {
    const primary = renderToStaticMarkup(createElement(Button, { variant: 'primary', children: 'Run' }));
    const ghost = renderToStaticMarkup(createElement(Button, { variant: 'ghost', children: 'Cancel' }));
    const danger = renderToStaticMarkup(createElement(Button, { variant: 'danger', children: 'Delete' }));
    expect(primary).toContain('brand-button-primary');
    expect(ghost).toContain('brand-button-ghost');
    expect(danger).toContain('brand-button-danger');
    expect(primary).toContain('type="button"');
  });

  it('renders generated HTML only inside an opaque script sandbox', () => {
    const markup = renderToStaticMarkup(createElement(HtmlPreview, {
      html: '<button onclick="document.body.dataset.ok=1">Start</button>',
      title: 'Timer preview',
    }));
    expect(markup).toContain('sandbox="allow-scripts"');
    expect(markup).not.toContain('allow-same-origin');
    expect(markup).toContain('referrerPolicy="no-referrer"');
    expect(markup).toContain('&lt;button');
  });

  it('formats JSON and parses quoted CSV tables', () => {
    expect(formatStructuredData('{"answer":42}')).toContain('"answer": 42');
    expect(parseDelimited('name,note\nA,"x,y"', ',')).toEqual([
      ['name', 'note'],
      ['A', 'x,y'],
    ]);
  });

  it('tracks the still-running provider request across concurrent calls', () => {
    const first = { type: 'provider.request.started', call_id: 'a', run_label: 'engineer-r1' };
    const second = { type: 'provider.request.started', call_id: 'b', run_label: 'manager' };
    expect(activeProviderRequest([
      first,
      second,
      { type: 'provider.request.completed', call_id: 'b' },
    ])).toEqual(first);
  });

  it('uses the canonical event catalog and explicit legacy aliases', () => {
    expect(EVENT_TYPES.USAGE_RECORDED).toBe('usage.recorded');
    expect(typedUsageEvent.payload_schema_version).toBe(2);
    expect(canonicalEventType('mission.started')).toBe(EVENT_TYPES.LIFE_MISSION_STARTED);
    expect(canonicalEventType('research.custom.ready')).toBe('research.custom.ready');
  });

  it('renders operator questions in a dedicated direct-reply dialog', () => {
    const html = renderToStaticMarkup(createElement(PendingReplyDialog, {
      reply: {
        id: 'blocked-1',
        title: 'Blocked task',
        question: 'Which dataset should the process use?',
      },
      open: true,
      busy: false,
      onClose: () => undefined,
      onSubmit: () => undefined,
    }));
    expect(html).toContain('Answer required');
    expect(html).toContain('Which dataset should the process use?');
    expect(html).toContain('Send answer');
    expect(html).toContain('directly to the process');
  });

  it('renders Settings and icon-only theme controls in the sidebar footer', () => {
    const html = renderToStaticMarkup(createElement(Sidebar, {
      projects: [],
      activeId: null,
      localCwd: '/workspace',
      onSelect: () => undefined,
      onManage: () => undefined,
      onOpenPanel: () => undefined,
      onNew: () => undefined,
      loading: false,
      collapsed: false,
      onToggleCollapse: () => undefined,
      themeMode: 'light',
      onCycleTheme: () => undefined,
    }));
    expect(html).toContain('Settings');
    expect(html).toContain('data-icon="gear"');
    expect(html).toContain('data-icon="sun"');
    expect(html).not.toContain('>Runtime<');
    expect(html).not.toContain('>light<');
  });

  it('renders a readable backend handshake before GSAP loads', () => {
    const html = renderToStaticMarkup(createElement(BackendHandshake));
    expect(html).toContain('Connecting to Argus');
    expect(html).toContain('API');
    expect(html).toContain('Protocol');
    expect(html).toContain('Workspace');
    expect(html).toContain('aria-label="Connecting to Argus backend"');
    expect(motionQueries).toEqual({
      all: '(min-width: 0px)',
      reduceMotion: '(prefers-reduced-motion: reduce)',
    });
  });

  it('keeps workbench motion bounded and accessible', () => {
    expect(motionDuration.fast).toBeGreaterThanOrEqual(0.18);
    expect(motionDuration.normal).toBeLessThanOrEqual(0.32);
    expect(motionDistance.magnetic).toBeLessThanOrEqual(6);
    expect(motionQueries.reduceMotion).toBe('(prefers-reduced-motion: reduce)');
  });

  it('surfaces persisted event validation failures instead of hiding them', () => {
    expect(activeGuardianAlert([{
      type: EVENT_TYPES.AGENT_IO_ERROR,
      event_validation: {
        status: 'invalid',
        errors: ['missing required fields: error'],
      },
    }])).toEqual({
      tone: 'warn',
      text: 'invalid event agent.io.error: missing required fields: error',
    });
  });

  it('selects live work first and gives replayed events one identity', () => {
    const rows = [
      { id: 'new', label: 'new', objective: '', last_active: 20, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
      { id: 'live', label: 'Research', objective: '', last_active: 10, daemon_alive: true, daemon_pid: 1, uptime_seconds: 5 },
    ];
    expect(defaultProject(rows)?.id).toBe('live');
    const event = { type: 'life.mission.completed', ts: 10, status: 'done' };
    expect(eventKey(event)).toBe(eventKey({ ...event }));
  });

  it('finds projects consistently by multiple fields and palette keywords', () => {
    const rows = [
      { id: 's-kernel-42', label: 'AAAI Paper', objective: 'Reproduce flash attention benchmark', last_active: 2, daemon_alive: true, daemon_pid: 1, uptime_seconds: 3 },
      { id: 's-vision-7', label: 'Vision notes', objective: 'Review VLM datasets', last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
    ];
    expect(filterProjects(rows, 'aaai live').map((row) => row.id)).toEqual(['s-kernel-42']);
    expect(filterProjects(rows, 'flash benchmark').map((row) => row.id)).toEqual(['s-kernel-42']);
    expect(filterProjects(rows, 'vision stopped').map((row) => row.id)).toEqual(['s-vision-7']);

    const items: PaletteItem[] = rows.map((row) => ({
      id: row.id,
      label: row.label,
      group: 'Project',
      keywords: `${row.id} ${row.objective}`,
      run: () => {},
    }));
    expect(filterPaletteItems(items, 'kernel benchmark').map((item) => item.id)).toEqual(['s-kernel-42']);
    expect(resolveProjectSelection(rows, 's-kernel-42')).toEqual({
      id: 's-kernel-42', requested: 's-kernel-42', recovered: false,
    });

    expect(resolveProjectSelection(rows, 'missing')).toEqual({
      id: 's-kernel-42', requested: 'missing', recovered: true,
    });
    expect(resolveProjectSelection([], 'missing')).toEqual({
      id: null, requested: 'missing', recovered: true,
    });
  });

  it('never auto-follows another operator session after initial selection', () => {
    const current = {
      id: 'mine', label: 'Mine', objective: '', last_active: 1,
      daemon_alive: false, daemon_pid: null, uptime_seconds: null,
    };
    const other = {
      id: 'other', label: 'Other live session', objective: '', last_active: 2,
      daemon_alive: true, daemon_pid: 42, uptime_seconds: 3,
    };
    expect(reconcileProjectSelection([current], null, false).id).toBe('mine');
    expect(reconcileProjectSelection([other, current], 'mine', true).id).toBe('mine');
    expect(reconcileProjectSelection([other], 'mine', true).id).toBe('mine');
    expect(reconcileProjectSelection([other], null, true).id).toBeNull();
  });

  it('uses only the authoritative project ledger total', () => {
    const spend = computeSpend([
      { type: 'life.planner.verdict', cost_usd: 0.2 },
      { type: 'life.mission.completed', cost_usd: 0.3 },
    ]);
    expect(spend.total).toBe(0);
    expect(authoritativeSpend(spend, 0.8)).toBe(0.8);
  });

  it('distinguishes mission completion from daemon liveness', () => {
    const view = deriveMissionView({
      session: { id: 's', display_name: '', objective: '', last_active: 0, cwd: '' },
      daemon: { alive: true, pid: 1, uptime_seconds: 1, backend: 'x', per_mission_cap_usd: 1, daily_cap_usd: 2, global_daily_cap_usd: 0 },
      roles: [],
      backlog: [],
      recent_events: [],
      continuous: { enabled: false, objective: 'CO2 paper', done_reason: 'done' },
    });
    expect(view).toMatchObject({ state: 'complete', objective: 'CO2 paper' });
  });

  it('treats a fresh session with a lazy daemon as ready, not offline', () => {
    const view = deriveMissionView({
      session: { id: 's-fresh', display_name: '', objective: '', last_active: 0, cwd: '' },
      daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, per_mission_cap_usd: null, daily_cap_usd: null, global_daily_cap_usd: null },
      roles: [],
      backlog: [],
      recent_events: [],
      continuous: { enabled: false, objective: '', done_reason: '' },
    });
    expect(view).toMatchObject({ state: 'idle', stateLabel: 'ready' });
  });

  it('does not report armed work as active when the executor is absent', () => {
    const snapshot = {
      session: { id: 's', display_name: '', objective: '', last_active: 0, cwd: '' },
      daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, per_mission_cap_usd: null, daily_cap_usd: null, global_daily_cap_usd: null },
      roles: [],
      recent_events: [],
      backlog: [],
      continuous: { enabled: true, objective: 'Run the benchmark', done_reason: '' },
    };
    expect(deriveMissionView(snapshot)).toMatchObject({
      state: 'waiting',
      stateLabel: 'queued',
    });
  });

  it('formats artifact sizes for compact result metadata', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(12 * 1024 * 1024)).toBe('12 MB');
  });

  it('keeps the opening animation lightweight and bounded', () => {
    const html = renderToStaticMarkup(
      createElement(BootSplash, { onDone: () => undefined }),
    );
    expect(WEB_SPLASH_DURATION_MS).toBeLessThanOrEqual(650);
    expect((html.match(/<pre/g) ?? []).length).toBe(2);
    expect(html).not.toContain('<span');
  });

  it('lets the Manager choose the live canvas and prefers its rendered output', () => {
    const artifacts = [
      { path: 'paper/main.tex', name: 'main.tex', why: 'draft', exists: true, kind: 'text' as const, mime: 'text/plain', size: 10, mtime: 1, source: 'manager_live' as const },
      { path: 'paper/main.pdf', name: 'main.pdf', why: 'rendered draft', exists: true, kind: 'pdf' as const, mime: 'application/pdf', size: 20, mtime: 2, source: 'manager_live' as const },
      { path: 'review/private.pdf', name: 'private.pdf', why: 'review', exists: true, kind: 'pdf' as const, mime: 'application/pdf', size: 30, mtime: 3, source: 'reviewer_evidence' as const },
    ];

    expect(selectPreferredLiveArtifact(artifacts)?.path).toBe('paper/main.tex');
    expect(selectPreferredLiveArtifact([{ ...artifacts[0], exists: false }])).toBeNull();
    expect(selectPreferredLiveArtifact([{
      ...artifacts[1],
      source: 'research_registered' as const,
    }])).toBeNull();
    expect(selectPreferredLiveArtifact([artifacts[2]])).toBeNull();
    expect(selectPreferredLiveArtifact([
      { ...artifacts[0], exists: false },
      artifacts[2],
    ])).toBeNull();
  });

  it('renders conversation Markdown without executing raw HTML', () => {
    const html = renderToStaticMarkup(
      createElement(MarkdownContent, null, '## Result\n\n- **passed**\n\n`score = 1`\n\n```\nraw block\n```\n\n<script>alert(1)</script>'),
    );
    expect(html).toContain('<h2');
    expect(html).toContain('<strong');
    expect(html).toContain('<code');
    expect(html).toContain('whitespace-pre-wrap');
    expect(html).not.toContain('min-w-max');
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
  });

  it('turns API JSON detail into a useful operator-facing error', async () => {
    const error = await responseError(
      { ok: false, status: 401, statusText: 'Unauthorized', text: async () => '{"detail":"invalid Web token"}' },
      'GET',
      '/api/projects/s/artifacts',
    );
    expect(error.message).toBe('GET /api/projects/s/artifacts → 401: invalid Web token');
    expect(error.status).toBe(401);
  });

  it('shares feed filters and backlog lifecycle semantics with Ink', () => {
    const alert = { type: 'life.lifecycle.block', reason: 'needs credentials', operator_alert: true };
    expect(eventMatchesView(alert, { tone: 'err', text: 'blocked — needs you' }, 'attention')).toBe(true);
    expect(eventMatchesView(alert, { tone: 'err', text: 'blocked — needs you' }, 'messages')).toBe(false);
    expect(eventMatchesView(alert, { tone: 'err', text: 'blocked — needs you' }, 'all', 'credentials')).toBe(true);
    const items = [
      { id: 'run', title: 'running', objective: '', status: 'running', priority: 1, max_cost_usd: 1 },
      { id: 'done', title: 'done', objective: '', status: 'done', priority: 2, max_cost_usd: 1 },
    ];
    expect(visibleBacklogItems(items, false).map((item) => item.id)).toEqual(['run']);
    expect(visibleBacklogItems(items, true).map((item) => item.id)).toEqual(['done']);
  });
});
