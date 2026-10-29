import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import fs from 'node:fs';
import path from 'node:path';
import * as sharedCore from '../../../core/src';
import {
  activeGuardianAlert,
  authoritativeSpend,
  computeSpend,
  defaultProject,
  reconcileProjectSelection,
  EVENT_TYPES,
  eventKey,
  eventMatchesView,
  filterProjects,
  canonicalEventType,
  resolveProjectSelection,
  responseError,
  visibleBacklogItems,
} from '../../../core/src';
import { formatBytes, shortcut, uptime } from '../lib/format';
import { finishManagerMessage, managerMessageError } from '../lib/messageResult';
import { filterPaletteItems, type PaletteItem } from '../components/CommandPalette';
import type { AgentIoCompleteEvent } from '../../../core/src/eventPayloads.generated';
import {
  selectPreferredPreviewArtifact,
  selectPreviewArtifacts,
} from '../components/ResearchCanvas';
import { MarkdownContent } from '../components/MarkdownContent';
import { PendingBanner } from '../components/PendingBanner';
import { Sidebar } from '../components/Sidebar';
import { BackendHandshake } from '../components/BackendHandshake';
import { motionDistance, motionDuration, motionQueries } from '../lib/motion';
import { activeProviderRequest } from '../components/EventStream';
import { HtmlPreview } from '../components/HtmlPreview';
import { formatStructuredData, parseDelimited } from '../components/DataPreview';
import { Button } from '../components/primitives';
import { ArgusMark, Wordmark } from '../components/Wordmark';
import { ConnectionProblemBanner, pairingTokenFromInput } from '../components/ConnectionProblemBanner';
import { LocalArgusUnavailableError, PairingRequiredError, pairWithToken } from '../api';
import { isMarkdownArtifact } from '../lib/artifactPresentation';

let queryClient: QueryClient;
beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
});
afterEach(() => queryClient.clear());

const typedCallEvent: AgentIoCompleteEvent = {
  type: 'agent.io.complete',
  payload_schema_version: 1,
  call_id: 'call-1',
  run_label: 'engineer-r1',
};

describe('shared frontend core', () => {
  it('says durations and shortcuts in the reader\'s words and on their platform', () => {
    expect(uptime(4 * 3600 + 12 * 60, 'zh-CN')).toBe('4 小时 12 分钟');
    expect(uptime(9 * 60)).toBe('9m');
    vi.stubGlobal('navigator', { platform: 'Linux x86_64' });
    expect(shortcut('K')).toBe('Ctrl+K');
    vi.stubGlobal('navigator', { platform: 'MacIntel' });
    expect(shortcut('K')).toBe('⌘K');
    vi.unstubAllGlobals();
  });

  it('renders Markdown by kind, MIME type, or filename', () => {
    expect(isMarkdownArtifact({ kind: 'markdown', name: 'report.bin' })).toBe(true);
    expect(isMarkdownArtifact({ kind: 'text', mime: 'text/markdown; charset=utf-8', name: 'report.txt' })).toBe(true);
    expect(isMarkdownArtifact({ kind: 'text', mime: 'text/plain', name: 'REPORT.MD' })).toBe(true);
    expect(isMarkdownArtifact({ kind: 'text', mime: 'text/plain', path: 'notes/readme.markdown' })).toBe(true);
    expect(isMarkdownArtifact({ kind: 'text', mime: 'text/plain', name: 'report.txt' })).toBe(false);
  });

  it('renders an actionable pairing message instead of a raw 401 loop', () => {
    const html = renderToStaticMarkup(createElement(ConnectionProblemBanner, {
      error: new PairingRequiredError(),
      onRetry: () => undefined,
    }));

    expect(html).toContain('role="alert"');
    expect(html).toContain('not paired with Argus');
    expect(html).toContain('valid pairing link for this Argus site');
    // The paste box is open at once, with who to ask when there is no link.
    expect(html).toContain('id="pairing-link"');
    expect(html).toContain('Ask the person who invited you');
  });

  it('accepts a fresh pairing token or extracts one from a pairing link', () => {
    vi.stubGlobal('window', { location: { href: 'http://127.0.0.1:8765/' } });

    expect(pairingTokenFromInput('fresh-token')).toBe('fresh-token');
    expect(pairingTokenFromInput('http://127.0.0.1:8765/?token=fresh-link-token')).toBe('fresh-link-token');
    expect(pairingTokenFromInput('http://127.0.0.1:8765/')).toBe('');
    expect(pairingTokenFromInput('not a token')).toBe('');

    vi.unstubAllGlobals();
  });

  it('opens the page with a pasted token only once the server takes it', async () => {
    const replace = vi.fn();
    vi.stubGlobal('window', { location: { href: 'http://127.0.0.1:8765/?x=1', replace } });
    const meta = (authenticated: boolean) => vi.fn(async () => Response.json({ authentication: { required: true, authenticated } }));

    vi.stubGlobal('fetch', meta(false));
    expect(await pairWithToken('wrong')).toBe(false);
    expect(replace).not.toHaveBeenCalled();

    const fetch = meta(true);
    vi.stubGlobal('fetch', fetch);
    expect(await pairWithToken('right')).toBe(true);
    expect(fetch).toHaveBeenCalledWith('/api/meta', { headers: { Authorization: 'Bearer right' } });
    expect(String(replace.mock.calls[0]![0])).toBe('http://127.0.0.1:8765/?x=1&token=right');

    vi.unstubAllGlobals();
  });

  it('renders an actionable local-service message instead of Failed to fetch', () => {
    const html = renderToStaticMarkup(createElement(ConnectionProblemBanner, {
      error: new LocalArgusUnavailableError('GET', '/api/meta'),
      onRetry: () => undefined,
    }));

    expect(html).toContain('local Argus service is unavailable');
    // A browser tab is not the desktop shell: no advice to keep a desktop app open.
    expect(html).toContain('may be restarting');
    expect(html).not.toContain('Argus Desktop running');
    expect(html).toContain('Retry');
  });

  it('uses theme-aware Rounded 02 eye geometry', () => {
    const lockup = renderToStaticMarkup(createElement(Wordmark, { size: 24 }));
    const mark = renderToStaticMarkup(createElement(ArgusMark, { size: 32 }));
    expect(lockup).toContain('data-logo="rounded-horizontal"');
    expect(mark).toContain('data-logo="rounded-mark"');
    expect(lockup).toContain('fill="rgb(var(--brand-body))"');
    expect(lockup).not.toContain('linearGradient');
    expect(mark).toContain('fill="rgb(var(--brand-eye))"');
    expect(mark).toContain('fill="rgb(var(--brand-pupil))"');
    expect(mark).toContain('fill="rgb(var(--brand-highlight))"');
    expect(mark).toContain('argus-mark-eye');
  });

  it('defines the public-brand workbench surface contract', () => {
    const css = fs.readFileSync(path.resolve('src/index.css'), 'utf8');
    for (const token of [
      '--blue',
      '--ok',
      '--warn',
      '--err',
      '--glass',
      '--glass-raised',
      '--glass-edge',
    ]) {
      expect(css).toContain(token);
    }
    expect(css).toContain('.workbench-shell');
    expect(css).toContain('.glass-panel');
    expect(css).toContain('.glass-card');
    expect(css).toContain('.icon-control');
    expect(css).toContain('.compact-control');
    // The accent fill belongs to the send button only; no shared class paints other controls with it.
    expect(css).not.toContain('.send-control');
    expect(css).toContain('.session-card');
    for (const selector of [
      '.ambient-canvas',
      '.glass-panel--side',
      '.glass-panel--main',
      '.glass-panel--raised',
    ]) {
      expect(css).toContain(selector);
    }
    expect(css).not.toContain('@keyframes ambient-drift');
    expect(css).not.toContain('will-change: transform, opacity');
    expect(css).not.toContain('--spectral-');
    expect(css).not.toContain('#89dceb');
    expect(css).not.toContain('#cba6f7');
    expect(css).toContain('.role-log-group[data-open=\"true\"]');
  });

  it('keeps light-theme blue info text at WCAG AA contrast', () => {
    const css = fs.readFileSync(path.resolve('src/index.css'), 'utf8');
    const root = css.match(/:root\s*\{([\s\S]*?)\}/)?.[1] ?? '';
    const channels = root.match(/--blue:\s*(\d+)\s+(\d+)\s+(\d+)/);
    expect(channels).not.toBeNull();
    const relativeLuminance = (rgb: number[]) => {
      const linear = rgb.map((channel) => {
        const value = channel / 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
    };
    const foreground = relativeLuminance(channels!.slice(1).map(Number));
    const background = relativeLuminance([255, 255, 255]);
    expect((background + 0.05) / (foreground + 0.05)).toBeGreaterThanOrEqual(4.5);
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

  it('closes a task’s orphaned provider request at its own completion', () => {
    const orphaned = {
      type: 'provider.request.started',
      call_id: 'orphaned-manager-call',
      item_id: 'mission-1',
      run_label: 'manager-classify-grounded',
    };
    expect(activeProviderRequest([
      orphaned,
      { type: 'life.mission.completed', item_id: 'mission-1', status: 'done' },
    ])).toBeNull();
    const next = {
      type: 'provider.request.started',
      call_id: 'next-manager-call',
      run_label: 'manager-classify-grounded',
    };
    expect(activeProviderRequest([
      orphaned,
      { type: 'life.mission.completed', item_id: 'mission-1', status: 'done' },
      next,
    ])).toEqual(next);
  });

  it('uses the canonical event catalog and explicit legacy aliases', () => {
    expect(EVENT_TYPES.AGENT_IO_COMPLETE).toBe(typedCallEvent.type);
    expect(canonicalEventType('mission.started')).toBe(EVENT_TYPES.LIFE_MISSION_STARTED);
    expect(canonicalEventType('research.custom.ready')).toBe('research.custom.ready');
  });

  it('renders operator questions above the composer without a dialog', () => {
    const html = renderToStaticMarkup(createElement(PendingBanner, {
      reply: {
        id: 'engineer-blocked-1',
        item_id: 'blocked-1',
        revision: 1,
        status: 'pending',
        title: 'Blocked task',
        reason: 'Dataset access is required.',
        question: 'Which dataset should the process use?',
        evidence: [],
        options: [{ id: 'custom', label: 'Choose dataset', description: 'Name the dataset.' }],
        selected_option: '',
        note: '',
      },
    }));
    expect(html).not.toContain('role="dialog"');
    expect(html).toContain('Which dataset should the process use?');
    expect(html).not.toMatch(/<textarea|<form|<button/);
  });

  it('renders Settings and icon-only theme controls in the sidebar footer', () => {
    const props = {
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
      onCycleTheme: () => undefined,
    };
    const light = renderToStaticMarkup(createElement(QueryClientProvider, { client: queryClient }, createElement(Sidebar, { ...props, themeMode: 'light' })));
    const dark = renderToStaticMarkup(createElement(QueryClientProvider, { client: queryClient }, createElement(Sidebar, { ...props, themeMode: 'dark' })));
    expect(light).toContain('aria-label="Open settings"');
    expect(light).toContain('lucide-settings');
    expect(light).toContain('lucide-sun');
    expect(light).toContain('aria-label="Switch to dark"');
    expect(dark).toContain('lucide-moon');
    expect(dark).toContain('aria-label="Switch to light"');
    expect(`${light}${dark}`).not.toContain('system');
    expect(`${light}${dark}`).not.toContain('desktop');
    expect(light).not.toContain('>Runtime<');
    expect(light).not.toContain('>light<');
  });

  it('renders a readable backend handshake before GSAP loads', () => {
    const html = renderToStaticMarkup(createElement(BackendHandshake));
    expect(html).toContain('Getting Argus ready');
    expect(html).toContain('Service');
    expect(html).toContain('Project');
    expect(html).toContain('Ready');
    expect(html).toContain('Reopening your workspace');
    expect(html).toContain('aria-label="Getting Argus ready"');
    expect(html).not.toContain('Protocol');
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
      kind: 'validation',
      text: "Argus skipped a record it couldn't read.",
    });
  });

  it('surfaces Manager error results instead of silently dropping the reply', () => {
    expect(managerMessageError({
      kind: 'error',
      reply: 'could not enqueue: provider quota reached',
    })).toBe('could not enqueue: provider quota reached');
    expect(managerMessageError({ kind: 'error', reply: '' })).toBe(
      'Manager could not handle this message.',
    );
    expect(managerMessageError({ kind: 'chat', reply: 'hello' })).toBeNull();
  });

  it('notifies and refetches for streaming and blocking Manager errors without dispatching', () => {
    const dispatchTask = vi.fn();
    const notifyError = vi.fn();
    const refetchTranscript = vi.fn();
    const complete = (result: Record<string, unknown>) => finishManagerMessage(
      result,
      { dispatchTask, notifyError, refetchTranscript },
    );

    const streamingOnDone = complete;
    streamingOnDone({ kind: 'error', reply: 'stream dispatch failed' });
    const blockingFallbackResult = { kind: 'error', reply: 'blocking dispatch failed' };
    complete(blockingFallbackResult);

    expect(dispatchTask).not.toHaveBeenCalled();
    expect(notifyError.mock.calls).toEqual([
      ['stream dispatch failed'],
      ['blocking dispatch failed'],
    ]);
    expect(refetchTranscript).toHaveBeenCalledTimes(2);
  });

  it('selects live work first and gives replayed events one identity', () => {
    const rows = [
      { workdir: '/work', id: 'new', label: 'new', last_active: 20, daemon_alive: false, daemon_pid: null, },
      { workdir: '/work', id: 'live', label: 'Research', last_active: 10, daemon_alive: true, daemon_pid: 1, },
    ];
    expect(defaultProject(rows)?.id).toBe('live');
    const event = { type: 'life.mission.completed', ts: 10, status: 'done' };
    expect(eventKey(event)).toBe(eventKey({ ...event }));
  });

  it('finds projects consistently by multiple fields and palette keywords', () => {
    const rows = [
      { workdir: '/work', id: 's-kernel-42', label: 'AAAI Paper', last_active: 2, daemon_alive: true, daemon_pid: 1, },
      { workdir: '/work', id: 's-vision-7', label: 'Vision notes', last_active: 1, daemon_alive: false, daemon_pid: null, },
    ];
    expect(filterProjects(rows, 'aaai live').map((row) => row.id)).toEqual(['s-kernel-42']);
    expect(filterProjects(rows, 'aaai live').map((row) => row.id)).toEqual(['s-kernel-42']);
    expect(filterProjects(rows, 'vision stopped').map((row) => row.id)).toEqual(['s-vision-7']);

    const items: PaletteItem[] = rows.map((row) => ({
      id: row.id,
      label: row.label,
      group: 'Project',
      keywords: `${row.id} ${row.label}`,
      run: () => {},
    }));
    expect(filterPaletteItems(items, 'kernel aaai').map((item) => item.id)).toEqual(['s-kernel-42']);
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
    const current = { workdir: '/work',
      id: 'mine', label: 'Mine', last_active: 1,
      daemon_alive: false, daemon_pid: null,
    };
    const other = { workdir: '/work',
      id: 'other', label: 'Other live session', last_active: 2,
      daemon_alive: true, daemon_pid: 42,
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


  it('maps mission terminal outcomes truthfully across new and legacy fields', () => {
    const present = (sharedCore as Record<string, unknown>).missionOutcomePresentation;
    expect(typeof present).toBe('function');
    if (typeof present !== 'function') return;

    expect((present as (event: Record<string, unknown>) => unknown)({
      success: true,
    })).toMatchObject({
      outcomeClass: 'completed',
      kind: 'mission_completed',
      label: 'The task was completed.',
      tone: 'ok',
      missionStatus: 'complete',
      technical: '',
    });

    const cases = [
      [
        { outcome_class: 'completed', status: 'supervisor_error', success: false },
        { outcomeClass: 'completed', kind: 'mission_completed', label: 'The task was completed.', tone: 'ok', missionStatus: 'complete' },
      ],
      [
        { status: 'done', success: true, final_submission_certified: true },
        { outcomeClass: 'completed', kind: 'mission_certified', label: 'The final submission was checked and approved.', tone: 'ok', missionStatus: 'complete' },
      ],
      [
        {
          status: 'done',
          success: true,
          outcome: { final_submission_certified: true },
        },
        { outcomeClass: 'completed', kind: 'mission_completed', label: 'The task was completed.', tone: 'ok', missionStatus: 'complete' },
      ],
      [
        { status: 'research_incomplete', success: false },
        { outcomeClass: 'incomplete', kind: 'mission_incomplete', label: 'The task stopped with work still remaining.', tone: 'warn', missionStatus: 'incomplete' },
      ],
      [
        { status: 'no_progress', success: false },
        { outcomeClass: 'stalled', kind: 'mission_stalled', label: 'The task stopped because recent rounds made no useful progress.', tone: 'warn', missionStatus: 'stalled' },
      ],
      [
        { status: 'blocked', success: false },
        { outcomeClass: 'blocked', kind: 'mission_blocked', label: 'The task cannot continue until something outside it is resolved.', tone: 'err', missionStatus: 'blocked' },
      ],
      [
        { status: 'supervisor_error', success: false },
        { outcomeClass: 'failed', kind: 'mission_failed', label: 'The task could not be completed.', tone: 'err', missionStatus: 'failed' },
      ],
      [
        // An unrecognised status is kept as a technical fact, never written into the sentence.
        { status: 'legacy_weird_status', success: false },
        { outcomeClass: 'ended', kind: 'mission_ended', label: 'The task ended.', tone: 'info', missionStatus: 'ended', technical: 'legacy_weird_status' },
      ],
      [
        // A paused task says why it paused; the raw status stays technical.
        { status: 'paused_daemon_shutdown', success: false, stop_kind: 'backend_unavailable', resumable: true },
        { outcomeClass: 'ended', kind: 'mission_paused', label: 'The task was paused because the Argus background service stopped or restarted; its progress is saved and it can be resumed.', tone: 'info', missionStatus: 'ended', technical: 'paused_daemon_shutdown' },
      ],
      [
        // A `parked` status carries its reason; it reads like the `paused_*` status it replaces.
        { status: 'parked', park_reason: 'daemon_shutdown', success: false },
        { outcomeClass: 'ended', kind: 'mission_paused', label: 'The task was paused because the Argus background service stopped or restarted; its progress is saved and it can be resumed.', tone: 'info', missionStatus: 'ended', technical: 'parked' },
      ],
      [
        { status: 'parked', park_reason: 'external_work', success: false },
        { kind: 'mission_paused', label: 'The task was paused because work outside Argus had to finish first; its progress is saved and it can be resumed.' },
      ],
      [
        { status: 'parked', park_reason: 'research_open', success: false },
        { outcomeClass: 'incomplete', kind: 'mission_incomplete' },
      ],
      [
        { status: 'parked', park_reason: 'infra', success: false },
        { outcomeClass: 'blocked', kind: 'mission_blocked' },
      ],
      [
        { success: false },
        { outcomeClass: 'ended', kind: 'mission_ended', label: 'The task ended.', tone: 'info', missionStatus: 'ended', technical: '' },
      ],
    ] as const;

    for (const [event, expected] of cases) {
      expect((present as (event: Record<string, unknown>) => unknown)(event)).toMatchObject(expected);
    }
  });



  it('formats artifact sizes for compact result metadata', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(12 * 1024 * 1024)).toBe('12 MB');
  });

  it('reserves stable shell, scrollbar, and font geometry', () => {
    const css = fs.readFileSync(path.resolve('src/index.css'), 'utf8');
    const html = fs.readFileSync(path.resolve('index.html'), 'utf8');
    const canvas = fs.readFileSync(path.resolve('src/components/ResearchCanvas.tsx'), 'utf8');
    expect(css).toContain('height: 100dvh');
    expect(css).toContain('scrollbar-gutter: stable');
    expect(html.match(/rel="preload"/g)).toHaveLength(2);
    expect(canvas).not.toContain('key={showLiveProgress');
    expect(canvas).not.toContain('gsap.fromTo');
  });

  it('favicon uses monochrome Rounded 02 geometry', () => {
    const svg = fs.readFileSync(path.resolve('public/favicon.svg'), 'utf8');
    expect(svg).toContain('fill="#000"');
    expect(svg).toContain('fill="#fff"');
    expect(svg).not.toContain('linearGradient');
    expect(svg).toMatch(/A\s*42\s+42/);
    expect(svg).toContain('<rect');
  });

  it('ships a non-inverted dark-mode favicon and full semantic palette', () => {
    const svg = fs.readFileSync(path.resolve('public/favicon-dark.svg'), 'utf8');
    const css = fs.readFileSync(path.resolve('src/index.css'), 'utf8');
    const html = fs.readFileSync(path.resolve('index.html'), 'utf8');
    const manifest = fs.readFileSync(path.resolve('public/manifest.webmanifest'), 'utf8');
    expect(svg).toContain('fill="#d7d9dc"');
    expect(svg).toContain('fill="#ffffff"');
    expect(svg).toContain('fill="#202326"');
    expect(svg).toContain('fill="#080a0b"');
    expect(css).toContain('--brand-body: 215 217 220');
    expect(css).toContain('--brand-eye: 255 255 255');
    expect(css).toContain('--brand-pupil: 32 35 38');
    expect(css).toContain('--brand-highlight: 255 255 255');
    expect(html).toContain('href="./favicon-dark.svg" media="(prefers-color-scheme: dark)"');
    expect(html).toContain('href="./apple-touch-icon-dark.png" media="(prefers-color-scheme: dark)"');
    expect(manifest).toContain('/icon-dark-192.png');
    expect(manifest).toContain('/icon-dark-512.png');
    expect(manifest).toContain('/icon-maskable-dark-512.png');
  });


  it('selects the returned live artifacts without an existence flag', () => {
    const artifacts = [
      { path: 'FINAL_REPORT.md', name: 'FINAL_REPORT.md', kind: 'markdown' as const, mime: 'text/markdown', size: 0, mtime: null, source: 'manager_live' as const },
      { path: 'research/results.jsonl', name: 'results.jsonl', kind: 'json' as const, mime: 'application/json', size: 10, mtime: 1, source: 'reviewer_evidence' as const },
      { path: 'paper/main.pdf', name: 'main.pdf', kind: 'pdf' as const, mime: 'application/pdf', size: 20, mtime: 2, source: 'reviewer_evidence' as const },
    ];
    expect(selectPreviewArtifacts(artifacts).map((item) => item.path)).toEqual([
      'paper/main.pdf', 'research/results.jsonl', 'FINAL_REPORT.md',
    ]);
    expect(selectPreferredPreviewArtifact(artifacts)?.path).toBe('paper/main.pdf');
  });








  it('renders conversation Markdown without executing raw HTML', () => {
    const html = renderToStaticMarkup(
      createElement(MarkdownContent, null, '## Result\n\n- **passed**\n\n\\[x^2\\]\n\nInline \\(y\\). Costs $20 and $30 today. Literal \\\\(not math\\\\).\n\n`score = \\(literal\\)`\n\n    \\(indented code\\)\n\n[artifact](notes/\\(draft\\).md)\n\n[reference][ref]\n\n[ref]: notes/\\(draft\\).md\n\n```\nraw block\n```\n\n<script>alert(1)</script>'),
    );
    expect(html).toContain('<h2');
    expect(html).toContain('<strong');
    expect(html).toContain('<code');
    expect(html).toContain('aria-label="Copy code"');
    expect(html).toContain('whitespace-pre-wrap');
    expect(html).not.toContain('min-w-max');
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('katex-display');
    expect(html).toContain('class="katex"');
    expect(html).toContain('Costs $20 and $30 today.');
    expect(html).toContain('score = \\(literal\\)');
    expect(html).toContain('\\(indented code\\)');
    expect(html).toContain('Literal \\(not math\\).');
    expect(html).toContain('href="notes/(draft).md"');
    expect(html).not.toContain('notes/$draft$.md');
  });

  it('keeps Markdown syntax inside TeX atomic', () => {
    const html = renderToStaticMarkup(
      createElement(MarkdownContent, null, String.raw`Inline \(a * b + [x](y)\).`),
    );

    expect(html).toContain('class="katex"');
    expect(html).not.toContain('<a ');
    expect(html).not.toContain('<em>');
  });

  it('preserves reference targets and malformed fenced code literally', () => {
    const markdown = [
      '[paper][ref]',
      '',
      '[ref]: notes/\\(draft\\).md "Title \\(literal\\)"',
      '',
      '```text',
      '\\[not_math\\]',
      '``',
      '\\(still_code\\)',
    ].join('\n');
    const html = renderToStaticMarkup(createElement(MarkdownContent, null, markdown));

    expect(html).toContain('href="notes/(draft).md"');
    expect(html).toContain('title="Title (literal)"');
    expect(html).toContain('\\[not_math\\]');
    expect(html).toContain('\\(still_code\\)');
    expect(html).not.toContain('class="katex"');
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

  it.each([
    { detail: { code: 'reader_source_unavailable', message: 'The selected source is missing.' } },
    { code: 'reader_source_unavailable', detail: { code: 'ignored_nested_code', message: 'The selected source is missing.' } },
  ])('preserves structured FastAPI source rejection codes and messages (%j)', async body => {
    const error = await responseError({ ok: false, status: 422, text: async () => JSON.stringify(body) },
      'POST', '/api/map-question-source/project/s');
    expect(error).toMatchObject({ status: 422, code: 'reader_source_unavailable', detail: 'The selected source is missing.' });
    expect(error.message).toContain('The selected source is missing.');
    expect(error.message).not.toContain('[object Object]');
  });

  it('shares feed filters and backlog lifecycle semantics with Ink', () => {
    const alert = { type: 'life.lifecycle.block', reason: 'needs credentials', operator_alert: true };
    expect(eventMatchesView(alert, { tone: 'err', text: 'blocked — needs you' }, 'attention')).toBe(true);
    expect(eventMatchesView(alert, { tone: 'err', text: 'blocked — needs you' }, 'messages')).toBe(false);
    expect(eventMatchesView(alert, { tone: 'err', text: 'blocked — needs you' }, 'all', 'credentials')).toBe(true);
    const items = [
      { id: 'run', title: 'running', objective: '', status: 'running', priority: 1 },
      { id: 'done', title: 'done', objective: '', status: 'done', priority: 2 },
      { id: 'replaced', title: 'replaced', objective: '', status: 'superseded', priority: 3 },
    ];
    expect(visibleBacklogItems(items, false).map((item) => item.id)).toEqual(['run']);
    expect(visibleBacklogItems(items, true).map((item) => item.id)).toEqual(['done', 'replaced']);
  });


});
