import { describe, expect, it, vi } from 'vitest';
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
  deriveMissionView,
  displayObjective,
  projectMissionView,
  EVENT_TYPES,
  eventKey,
  eventMatchesView,
  filterProjects,
  canonicalEventType,
  resolveProjectSelection,
  responseError,
  visibleBacklogItems,
} from '../../../core/src';
import { COMMANDS } from '../../../core/src/commands';
import { formatBytes } from '../lib/format';
import { finishManagerMessage, managerMessageError } from '../lib/messageResult';
import { filterPaletteItems, commandPaletteRows, type PaletteItem } from '../components/CommandPalette';
import type { UsageRecordedEvent } from '../../../core/src/eventPayloads.generated';
import {
  selectPreferredLiveArtifact,
  selectPreferredPreviewArtifact,
  selectPreviewArtifacts,
  selectLiveMissionStatus,
  defaultPreviewPath,
  liveProgressSummary,
  LIVE_PROGRESS_PATH,
} from '../components/ResearchCanvas';
import { emptyMissionView } from '../../../core/src/missionView';
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
import { ArgusMark, Wordmark } from '../components/Wordmark';

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
  it('uses Rounded 02 geometry with one continuous brand gradient', () => {
    const lockup = renderToStaticMarkup(createElement(Wordmark, { size: 24 }));
    const mark = renderToStaticMarkup(createElement(ArgusMark, { size: 32 }));
    expect(lockup).toContain('data-logo="rounded-horizontal"');
    expect(mark).toContain('data-logo="rounded-mark"');
    expect(lockup).toContain('gradientUnits="userSpaceOnUse"');
    expect(lockup).toContain('x1="180"');
    expect(lockup).toContain('x2="1280"');
    expect(lockup).not.toContain('var(--spectral-violet)');
    expect(lockup).not.toContain('NightPupil');
  });

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
    expect(css).toContain('.icon-control');
    expect(css).toContain('.compact-control');
    expect(css).toContain('.send-control');
    expect(css).toContain('.session-card');
    for (const selector of [
      '.ambient-canvas',
      '.glass-panel--side',
      '.glass-panel--main',
      '.glass-panel--raised',
    ]) {
      expect(css).toContain(selector);
    }
    expect(css).toContain('@keyframes ambient-drift');
    expect(css).toContain('[data-page-visible=\"false\"]');
    expect(css).not.toContain('--spectral-violet: 105 73 205');
    expect(css).not.toContain('--spectral-rose: 190 67 119');
    expect(css).not.toContain('#89dceb');
    expect(css).not.toContain('#cba6f7');
    expect(css).toContain('.workspace-tab-indicator');
    expect(css).toContain('.role-log-group[data-open=\"true\"]');
  });

  it('keeps light-theme spectral info text at WCAG AA contrast', () => {
    const css = fs.readFileSync(path.resolve('src/index.css'), 'utf8');
    const root = css.match(/:root\s*\{([\s\S]*?)\}/)?.[1] ?? '';
    const channels = root.match(/--spectral-blue:\s*(\d+)\s+(\d+)\s+(\d+)/);
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

  it('uses the canonical event catalog and explicit legacy aliases', () => {
    expect(EVENT_TYPES.USAGE_RECORDED).toBe('usage.recorded');
    expect(typedUsageEvent.payload_schema_version).toBe(2);
    expect(canonicalEventType('mission.started')).toBe(EVENT_TYPES.LIFE_MISSION_STARTED);
    expect(canonicalEventType('research.custom.ready')).toBe('research.custom.ready');
  });

  it('renders operator questions in a dedicated direct-reply dialog', () => {
    const html = renderToStaticMarkup(createElement(PendingReplyDialog, {
      reply: {
        id: 'decision-blocked-1',
        item_id: 'blocked-1',
        revision: 1,
        status: 'pending',
        title: 'Blocked task',
        reason: 'Dataset access is required.',
        question: 'Which dataset should the process use?',
        evidence: [],
        options: [{ id: 'custom', label: 'Choose dataset', description: 'Name the dataset.', requires_note: true }],
        selected_option: '',
        note: '',
      },
      open: true,
      busy: false,
      onClose: () => undefined,
      onSubmit: () => undefined,
    }));
    expect(html).toContain('Decision required');
    expect(html).toContain('Which dataset should the process use?');
    expect(html).toContain('Choose dataset');
    expect(html).toContain('The Manager applies your choice');
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
    const light = renderToStaticMarkup(createElement(Sidebar, { ...props, themeMode: 'light' }));
    const dark = renderToStaticMarkup(createElement(Sidebar, { ...props, themeMode: 'dark' }));
    expect(light).toContain('Settings');
    expect(light).toContain('data-icon="gear"');
    expect(light).toContain('data-icon="sun"');
    expect(light).toContain('switch to dark');
    expect(dark).toContain('data-icon="moon"');
    expect(dark).toContain('switch to light');
    expect(`${light}${dark}`).not.toContain('system');
    expect(`${light}${dark}`).not.toContain('desktop');
    expect(light).not.toContain('>Runtime<');
    expect(light).not.toContain('>light<');
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
      daemon: { alive: true, pid: 1, uptime_seconds: 1, backend: 'x', global_daily_cap_usd: 0 },
      roles: [],
      backlog: [],
      recent_events: [],
      continuous: { enabled: false, objective: 'CO2 paper', done_reason: 'done' },
    });
    expect(view).toMatchObject({ state: 'complete', objective: 'CO2 paper' });
  });

  it('maps mission terminal outcomes truthfully across new and legacy fields', () => {
    const present = (sharedCore as Record<string, unknown>).missionOutcomePresentation;
    expect(typeof present).toBe('function');
    if (typeof present !== 'function') return;

    expect((present as (event: Record<string, unknown>) => unknown)({
      success: true,
    })).toMatchObject({
      outcomeClass: 'completed',
      label: 'Task completed',
      tone: 'ok',
      missionStatus: 'complete',
    });

    const cases = [
      [
        { outcome_class: 'completed', status: 'supervisor_error', success: false },
        { outcomeClass: 'completed', label: 'Task completed', tone: 'ok', missionStatus: 'complete' },
      ],
      [
        { status: 'done', success: true, final_submission_certified: true },
        { outcomeClass: 'completed', label: 'Submission certified', tone: 'ok', missionStatus: 'complete' },
      ],
      [
        {
          status: 'done',
          success: true,
          outcome: { final_submission_certified: true },
        },
        { outcomeClass: 'completed', label: 'Task completed', tone: 'ok', missionStatus: 'complete' },
      ],
      [
        { status: 'research_incomplete', success: false },
        { outcomeClass: 'incomplete', label: 'Mission incomplete', tone: 'warn', missionStatus: 'incomplete' },
      ],
      [
        { status: 'no_progress', success: false },
        { outcomeClass: 'stalled', label: 'Mission stalled', tone: 'warn', missionStatus: 'stalled' },
      ],
      [
        { status: 'blocked', success: false },
        { outcomeClass: 'blocked', label: 'Mission blocked', tone: 'err', missionStatus: 'blocked' },
      ],
      [
        { status: 'supervisor_error', success: false },
        { outcomeClass: 'failed', label: 'Mission failed', tone: 'err', missionStatus: 'failed' },
      ],
      [
        { status: 'legacy_weird_status', success: false },
        { outcomeClass: 'ended', label: 'Mission ended · legacy_weird_status', tone: 'info', missionStatus: 'ended' },
      ],
      [
        { success: false },
        { outcomeClass: 'ended', label: 'Mission ended', tone: 'info', missionStatus: 'ended' },
      ],
    ] as const;

    for (const [event, expected] of cases) {
      expect((present as (event: Record<string, unknown>) => unknown)(event)).toMatchObject(expected);
    }
  });

  it('treats a fresh session with a lazy daemon as ready, not offline', () => {
    const view = deriveMissionView({
      session: { id: 's-fresh', display_name: '', objective: '', last_active: 0, cwd: '' },
      daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, global_daily_cap_usd: null },
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
      daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, global_daily_cap_usd: null },
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
    expect(WEB_SPLASH_DURATION_MS).toBeLessThanOrEqual(200);
  });

  it('uses Rounded 02 SVGs for both boot splash widths', () => {
    const html = renderToStaticMarkup(
      createElement(BootSplash, { onDone: () => undefined }),
    );
    expect(html).toContain('data-logo="rounded-horizontal"');
    expect(html).toContain('data-logo="rounded-mark"');
    expect(html).not.toContain('<pre');
    expect(html).not.toContain('ARGUS-SKILL');
  });

  it('favicon uses Rounded 02 geometry with fixed blue-gold gradient', () => {
    const svg = fs.readFileSync(path.resolve('public/favicon.svg'), 'utf8');
    expect(svg).toContain('gradientUnits="userSpaceOnUse"');
    expect(svg).toContain('#075fe4');
    expect(svg).toContain('#d99a16');
    expect(svg).toMatch(/A\s*42\s+42/);
    expect(svg).not.toContain('<rect');
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

  it('falls back to an existing reviewed artifact when the live draft is pending', () => {
    const artifacts = [
      { path: 'FINAL_REPORT.md', name: 'FINAL_REPORT.md', why: 'live', exists: false, kind: 'markdown' as const, mime: 'text/markdown', size: 0, mtime: null, source: 'manager_live' as const },
      { path: 'research/results.jsonl', name: 'results.jsonl', why: 'rows', exists: true, kind: 'json' as const, mime: 'application/json', size: 10, mtime: 1, source: 'reviewer_evidence' as const },
      { path: 'paper/main.pdf', name: 'main.pdf', why: 'paper', exists: true, kind: 'pdf' as const, mime: 'application/pdf', size: 20, mtime: 2, source: 'reviewer_evidence' as const },
    ];
    expect(selectPreviewArtifacts(artifacts).map((item) => item.path)).toEqual([
      'paper/main.pdf', 'research/results.jsonl',
    ]);
    expect(selectPreferredPreviewArtifact(artifacts)?.path).toBe('paper/main.pdf');
  });

  it('decodes transport escapes only for objective presentation', () => {
    expect(displayObjective('\\*\\*问题\\*\\*: $n=2,3,\\\\dots$')).toBe(
      '**问题**: $n=2,3,\\dots$',
    );
  });

  it('presents an idle project without a fake zero-over-missing DAG', () => {
    const summary = liveProgressSummary(emptyMissionView());
    expect(summary).toEqual({
      title: 'Ready for a new mission',
      dagProgress: 'Not planned',
    });
  });

  it('lets Manager selection own the default and falls back to live progress', () => {
    const view = emptyMissionView();
    const artifacts = [{ path: '.argus/live/status.md', name: 'status.md', why: 'checkpoint', exists: true, kind: 'markdown' as const, mime: 'text/markdown', size: 20, mtime: 1, source: 'manager_live' as const }];

    expect(defaultPreviewPath(view, artifacts)).toBe('.argus/live/status.md');
    expect(defaultPreviewPath(view, [])).toBe(LIVE_PROGRESS_PATH);
    expect(defaultPreviewPath(null, artifacts)).toBe('.argus/live/status.md');
  });

  it('shows the current runtime role above a stale Manager checkpoint', () => {
    const view = emptyMissionView();
    view.active_role = 'reviewer';
    Object.assign(view.roles.find((role) => role.role === 'reviewer')!, {
      status: 'active',
      label: 'Reporting progress',
    });

    const status = selectLiveMissionStatus(view, [{
      type: 'engineer.progress',
      agent_layer: 'reviewer',
      kind: 'agent_message',
      text: '正在独立核对证明与证书。',
      ts: 10,
    }]);

    expect(status).toEqual({
      role: 'reviewer',
      roleLabel: 'Reviewer',
      label: 'Reporting progress',
      detail: '正在独立核对证明与证书。',
    });
  });

  it('keeps campaign elapsed stable when the active DAG node changes', () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(130_000);
    const view = emptyMissionView();
    view.mission.started_at = 100;
    const snapshot = {
      session: { id: 's', display_name: '', objective: '', created: 10, last_active: 0, cwd: '' },
      daemon: { alive: true, pid: 1, uptime_seconds: 1, backend: 'x', global_daily_cap_usd: 3 },
      roles: [],
      backlog: [{ id: 'solve', title: 'Solve', objective: '', status: 'running', priority: 100, iterate: true, pending_question: '', started_ts: 100, finished_ts: null, deps: [], iteration_max_cycles: 1, iteration_cycles_done: 0 }],
      recent_events: [],
      continuous: { enabled: true, objective: 'Solve the problem' },
      mission_view: view,
    };

    const result = projectMissionView(snapshot, [], []);

    expect(result.mission.elapsed_seconds).toBe(30);
    expect(result.mission.campaign_elapsed_seconds).toBe(120);
    now.mockRestore();
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
      { id: 'run', title: 'running', objective: '', status: 'running', priority: 1 },
      { id: 'done', title: 'done', objective: '', status: 'done', priority: 2 },
    ];
    expect(visibleBacklogItems(items, false).map((item) => item.id)).toEqual(['run']);
    expect(visibleBacklogItems(items, true).map((item) => item.id)).toEqual(['done']);
  });

  it('builds one palette row for every shared slash command', () => {
    const rows = commandPaletteRows(COMMANDS, vi.fn(), vi.fn());
    expect(rows).toHaveLength(35);
    expect(rows.map((row) => row.hint)).toContain('/status');
    expect(rows.map((row) => row.hint)).toContain('/quit');
  });
});
