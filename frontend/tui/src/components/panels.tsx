import React from 'react';
import { Box, Text } from 'ink';
import { theme, effortColor } from '../theme.js';
import { helpGroups } from '../input/slash.js';
import type {
  ArtifactInfo,
  BacklogItem,
  ConfigSnapshot,
  DoctorReport,
  EventMsg,
  JournalEntry,
  ProjectRow,
  Snapshot,
  StatusView,
} from '../api.js';
import { filterProjects, rankProjects } from '../../../core/src/projects.js';
import { visibleBacklogItems } from '../../../core/src/backlog.js';
import { outcomeDimensionSummary } from '../../../core/src/missionOutcome.js';
import { eventMatchesView, type EventViewFilter } from '../../../core/src/events.js';
import { buildEventLines } from '../eventLines.js';
import { roleColor, toneColor } from '../eventRender.js';
import { computeSpend } from '../cost.js';
import { activityHistory } from '../../../core/src/activity.js';
import { CostGauge } from './CostGauge.js';

/** The overlay panel opened by a read/inspect slash command. Loosely-typed
 *  container; each panel body is a typed component below. */
export interface PanelState {
  kind: 'operations' | 'help' | 'status' | 'doctor' | 'backlog' | 'journal' | 'config' | 'identity' | 'daemons' | 'artifacts' | 'artifact' | 'events' | 'task';
  all?: boolean; // /backlog all
  page?: number;
  selection?: number;
  path?: string;
  itemId?: string;
  filter?: EventViewFilter;
  query?: string;
  loading?: boolean;
  data?: unknown;
  error?: string;
}

function Frame({
  title,
  children,
  page = 0,
  pages = 1,
  hint,
}: {
  title: string;
  children: React.ReactNode;
  page?: number;
  pages?: number;
  hint?: string;
}) {
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={theme.border} paddingX={2} marginTop={1}>
      <Text bold color={theme.accent}>
        {title}
      </Text>
      <Box flexDirection="column" marginTop={1}>
        {children}
      </Box>
      <Text> </Text>
      <Text dimColor>
        {hint ?? (pages > 1 ? `↑/k ↓/j page ${page + 1}/${pages} · Enter/Esc close` : 'Enter/Esc close')}
      </Text>
    </Box>
  );
}

const dot = (ok: boolean) => (ok ? '●' : '○');

export function PanelView({
  panel,
  snap,
  viewportRows,
  activeProject,
  events = [],
  viewportColumns = 80,
}: {
  panel: PanelState;
  snap: Snapshot | null;
  viewportRows: number;
  activeProject?: string;
  events?: EventMsg[];
  viewportColumns?: number;
}) {
  const pageSize = Math.max(4, viewportRows - 10);
  if (panel.loading) {
    return (
      <Frame title={panel.kind}>
        <Text dimColor>loading…</Text>
      </Frame>
    );
  }
  if (panel.error) {
    return (
      <Frame title={panel.kind}>
        <Text color={theme.error}>{panel.error}</Text>
      </Frame>
    );
  }
  switch (panel.kind) {
    case 'operations':
      return <OperationsPanel snap={snap} events={events} width={viewportColumns} />;
    case 'help':
      return <HelpPanel page={panel.page ?? 0} pageSize={pageSize} />;
    case 'status':
      return <StatusPanel s={panel.data as StatusView} />;
    case 'doctor':
      return <DoctorPanel r={panel.data as DoctorReport} />;
    case 'backlog':
      return <BacklogPanel items={snap?.backlog ?? []} all={!!panel.all} selected={panel.selection ?? 0} pageSize={pageSize} />;
    case 'journal':
      return <JournalPanel entries={(panel.data as JournalEntry[]) ?? []} page={panel.page ?? 0} pageSize={pageSize} />;
    case 'config':
      return <ConfigPanel c={panel.data as ConfigSnapshot} />;
    case 'identity':
      return <IdentityPanel text={(panel.data as string) ?? ''} page={panel.page ?? 0} pageSize={pageSize} />;
    case 'daemons':
      return (
        <DaemonsPanel
          rows={(panel.data as ProjectRow[]) ?? []}
          selected={panel.selection ?? 0}
          pageSize={pageSize}
          activeProject={activeProject}
          query={panel.query ?? ''}
        />
      );
    case 'artifacts':
      return (
        <ArtifactsPanel
          rows={(panel.data as ArtifactInfo[]) ?? []}
          selected={panel.selection ?? 0}
          pageSize={Math.max(2, Math.floor(pageSize / 2))}
        />
      );
    case 'artifact':
      return (
        <ArtifactPanel
          artifact={panel.data as ArtifactInfo}
          page={panel.page ?? 0}
          pageSize={pageSize}
        />
      );
    case 'events':
      return (
        <EventsPanel
          events={events}
          filter={panel.filter ?? 'all'}
          query={panel.query ?? ''}
          page={panel.page ?? 0}
          pageSize={Math.max(3, Math.floor(pageSize / 2))}
          width={viewportColumns}
        />
      );
    case 'task':
      return (
        <TaskPanel
          item={panel.data as BacklogItem}
          page={panel.page ?? 0}
          pageSize={pageSize}
          width={viewportColumns}
        />
      );
  }
}

function OperationsPanel({
  snap,
  events,
  width,
}: {
  snap: Snapshot | null;
  events: EventMsg[];
  width: number;
}) {
  if (!snap) {
    return <Frame title="Operations" hint="Ctrl+O close"><Text dimColor>loading…</Text></Frame>;
  }
  const activities = activityHistory(events, 8);
  const slo = snap.observability?.slo;
  const storage = snap.mission_view?.storage;
  return (
    <Frame title="Operations" hint="Ctrl+O close · /status and /doctor for details">
      <Row k="daemon" v={snap.daemon.alive ? `● pid ${snap.daemon.pid ?? '—'} · ${Math.floor((snap.daemon.uptime_seconds ?? 0) / 60)}m` : '○ stopped'} c={snap.daemon.alive ? theme.success : 'gray'} />
      <Row k="backend" v={snap.daemon.backend_label || snap.daemon.backend || '—'} />
      <Row k="protocol" v={`${snap.daemon.protocol?.name || '—'}/${snap.daemon.protocol?.major ?? '—'}.${snap.daemon.protocol?.minor ?? '—'}`} />
      <Text> </Text>
      <CostGauge
        spend={computeSpend(events)}
        settledUsd={snap.spend_usd}
        spendStatus={snap.spend_status}
        usageSummary={snap.usage_summary}
        daemon={snap.daemon}
        requestUsage={snap.request_usage}
        costControl={snap.cost_control}
        width={width}
      />
      <Text> </Text>
      <Text dimColor>roles</Text>
      {snap.roles.map((role) => (
        <Text key={role.role}>
          <Text color={theme.role[role.role] ?? 'white'}>{role.role.padEnd(10)}</Text>
          <Text dimColor>{`${role.backend_label || role.backend} · ${role.model || '—'} · ${role.effort || 'default'}`}</Text>
        </Text>
      ))}
      {storage && (storage.project_skill_dir || storage.global_skill_dir || storage.wiki_paths.length || storage.skill_history_compressed || storage.wiki_retired_compressed) ? (
        <>
          <Text> </Text>
          <Text dimColor>self-evolution storage</Text>
          {storage.project_skill_dir ? <Row k="project skills" v={`${storage.project_skill_count} · ${storage.project_skill_dir}`} /> : null}
          {storage.global_skill_dir ? <Row k="global skills" v={`${storage.global_skill_count} · ${storage.global_skill_dir}`} /> : null}
          {storage.wiki_paths.map((path, index) => <Row key={path} k={index ? "" : "project wiki"} v={path} />)}
          {(storage.skill_history_compressed || storage.wiki_retired_compressed) ? <Row k="cold history" v={`skill ${storage.skill_history_compressed} · wiki ${storage.wiki_retired_compressed} · ${formatBytes(storage.skill_history_bytes_saved + storage.wiki_retired_bytes_saved)} saved`} /> : null}
        </>
      ) : null}
      {slo?.status === 'degraded' ? (
        <>
          <Text> </Text>
          <Text color={theme.error}>SLO degraded</Text>
          {slo.violations.slice(0, 5).map((violation) => <Text key={violation} dimColor>{`  ! ${violation}`}</Text>)}
        </>
      ) : null}
      {activities.length ? (
        <>
          <Text> </Text>
          <Text dimColor>recent observable activity</Text>
          {activities.map((activity) => (
            <Text key={activity.id} dimColor>{`  · ${activity.role} · ${activity.label}`}</Text>
          ))}
        </>
      ) : null}
    </Frame>
  );
}

function pageSlice<T>(rows: T[], page: number, pageSize: number): { shown: T[]; page: number; pages: number } {
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const safePage = Math.min(Math.max(0, page), pages - 1);
  return { shown: rows.slice(safePage * pageSize, (safePage + 1) * pageSize), page: safePage, pages };
}

function HelpPanel({ page, pageSize }: { page: number; pageSize: number }) {
  const rows = helpGroups().flatMap((group) => group.rows.map((row) => ({ ...row, group: group.group })));
  const view = pageSlice(rows, page, pageSize);
  return (
    <Frame title="argus cockpit — commands" page={view.page} pages={view.pages}>
      <Text dimColor>type freely to chat/queue · commands start with /</Text>
      <Text> </Text>
      {view.shown.map((row, index) => (
        <Box key={row.label} flexDirection="column">
          {(index === 0 || view.shown[index - 1]?.group !== row.group) && <Text bold color="cyan">{row.group}</Text>}
          <Box>
            <Text color={theme.accent}>{`  ${row.label}`.padEnd(34)}</Text>
            <Text dimColor>{row.desc}</Text>
          </Box>
        </Box>
      ))}
    </Frame>
  );
}

function StatusPanel({ s }: { s: StatusView }) {
  return (
    <Frame title="/status">
      <Row k="daemon" v={s.daemon.alive ? `● alive (pid ${s.daemon.pid})` : '○ no daemon'} c={s.daemon.alive ? theme.success : 'gray'} />
      <Row k="active role" v={s.active_role ?? 'idle'} />
      <Row k="continuous" v={s.continuous.enabled ? `on · ${s.continuous.objective}` : 'off'} />
      <Row k="inbox" v={`${s.inbox_pending} pending`} />
      <Row k="backlog" v={`${s.backlog_pending.length} pending`} />
      {s.request_usage ? (
        <Row
          k="requests"
          v={`Codex ${s.request_usage.codex.daily_calls}/${s.request_usage.codex.daily_cap || '∞'} · Copilot ${s.request_usage.copilot.daily_calls}/${s.request_usage.copilot.daily_cap || '∞'}`}
        />
      ) : null}
      {s.pending_questions.length > 0 ? (
        <Row k="questions" v={`${s.pending_questions.length} awaiting you`} c={theme.warning} />
      ) : null}
      <Text> </Text>
      <Text dimColor>recent journal:</Text>
      {s.journal.length === 0 ? (
        <Text dimColor>  (none)</Text>
      ) : (
        s.journal.slice(-3).map((j) => (
          <Text key={j.id} dimColor>{`  · ${j.title || j.kind}`}</Text>
        ))
      )}
      {s.identity ? (
        <>
          <Text> </Text>
          <Text dimColor>{`identity: ${s.identity.split('\n')[0].slice(0, 70)}`}</Text>
        </>
      ) : null}
    </Frame>
  );
}

function DoctorPanel({ r }: { r: DoctorReport }) {
  return (
    <Frame title="/doctor — why isn't anything running?">
      {r.checks.map((c) => (
        <Box key={c.name}>
          <Text color={c.ok ? theme.success : theme.error}>{dot(c.ok)} </Text>
          <Text>{c.name.padEnd(20)}</Text>
          <Text dimColor>{c.detail}</Text>
        </Box>
      ))}
      {r.recommended ? (
        <>
          <Text> </Text>
          <Text color={theme.accent}>{`→ recommended: ${r.recommended.fix || r.recommended.detail}`}</Text>
        </>
      ) : (
        <Text color={theme.success}>{'\n✓ all checks pass'}</Text>
      )}
      {r.log_tail ? (
        <>
          <Text> </Text>
          <Text dimColor>recent daemon.log:</Text>
          {r.log_tail.split('\n').slice(-6).map((ln, i) => (
            <Text key={i} dimColor>{`  ${ln}`}</Text>
          ))}
        </>
      ) : null}
    </Frame>
  );
}

function BacklogPanel({ items, all, selected, pageSize }: { items: BacklogItem[]; all: boolean; selected: number; pageSize: number }) {
  const shown = all ? items : visibleBacklogItems(items, false);
  const safeSelection = Math.min(Math.max(0, selected), Math.max(0, shown.length - 1));
  const view = pageSlice(shown, Math.floor(safeSelection / pageSize), pageSize);
  const color = (s: string) =>
    s === 'pending' ? 'cyan' : s === 'running' ? theme.info : s === 'done' || s === 'completed' ? theme.success : s === 'failed' || s === 'blocked' ? theme.error : 'gray';
  return (
    <Frame
      title={`/backlog${all ? ' all' : ''}`}
      page={view.page}
      pages={view.pages}
      hint={`↑/k ↓/j select${view.pages > 1 ? ` · page ${view.page + 1}/${view.pages}` : ''} · Enter details · Esc close`}
    >
      {shown.length === 0 ? (
        <Text dimColor>(backlog is empty — type a task or /task &lt;text&gt;)</Text>
      ) : (
        view.shown.map((it, index) => {
          const focused = view.page * pageSize + index === safeSelection;
          return (
          <Box key={it.id}>
            <Text color={focused ? theme.accent : 'gray'}>{focused ? '› ' : '  '}</Text>
            <Text color={color(it.status)}>{it.status.padEnd(10)}</Text>
            <Text dimColor>{`${it.id.slice(0, 8)}  `}</Text>
            <Text dimColor>{`$${it.max_cost_usd.toFixed(0)}  `}</Text>
            <Text bold={focused}>{it.title || it.objective}</Text>
          </Box>
          );
        })
      )}
    </Frame>
  );
}

function EventsPanel({
  events,
  filter,
  query,
  page,
  pageSize,
  width,
}: {
  events: EventMsg[];
  filter: EventViewFilter;
  query: string;
  page: number;
  pageSize: number;
  width: number;
}) {
  const all = buildEventLines(events);
  const filtered = all.filter((line) => eventMatchesView(line.ev, line.r, filter, query)).reverse();
  const view = pageSlice(filtered, page, pageSize);
  const queryLabel = query ? ` · “${query.slice(0, Math.max(8, width - 34))}${query.length > width - 34 ? '…' : ''}”` : '';
  return (
    <Frame title={`/events ${filter}${queryLabel} · ${filtered.length}/${all.length}`} page={view.page} pages={view.pages}>
      {filtered.length === 0 ? <Text dimColor>(no events match this view)</Text> : view.shown.map((line) => (
        <Box key={line.key} flexDirection="column">
          <Box>
            <Text color={roleColor(line.r.role)} bold>{`${line.r.label}`.padEnd(10)}</Text>
            <Text color={toneColor(line.r.tone)}>{`${['err', 'warn'].includes(line.r.tone) ? '!' : '·'} ${line.r.text.slice(0, Math.max(18, width - 19))}`}</Text>
          </Box>
          <Text dimColor>{`  ${String(line.ev.type ?? 'event')}`}</Text>
        </Box>
      ))}
    </Frame>
  );
}

function wrapDetail(text: string, width: number): string[] {
  const size = Math.max(20, width - 8);
  return String(text || '').split('\n').flatMap((line) => {
    if (!line) return [' '];
    const chunks: string[] = [];
    for (let offset = 0; offset < line.length; offset += size) chunks.push(line.slice(offset, offset + size));
    return chunks;
  });
}

function TaskPanel({ item, page, pageSize, width }: { item: BacklogItem; page: number; pageSize: number; width: number }) {
  const outcome = outcomeDimensionSummary(item.outcome);
  const lines = [
    `status      ${item.status}`,
    ...(outcome.length ? outcome.map((row, index) => `${index ? '            ' : 'outcome     '}${row}`) : []),
    `priority    p${item.priority}`,
    `budget      $${item.max_cost_usd.toFixed(2)}`,
    ...wrapDetail(`iteration   ${item.iterate ? 'auto' : 'single'} · ${item.iteration_cycles_done ?? 0}/${item.iteration_max_cycles ?? '—'} cycles · $${(item.iteration_cost_usd ?? 0).toFixed(2)}`, width),
    ...(item.pending_question ? ['', 'WAITING ON YOU', ...wrapDetail(item.pending_question, width)] : []),
    '',
    'OBJECTIVE',
    ...wrapDetail(item.objective || item.original_objective || '(none)', width),
    ...(item.last_error ? ['', 'LAST ERROR', ...wrapDetail(item.last_error, width)] : []),
    ...(item.notes ? ['', 'NOTES', ...wrapDetail(item.notes, width)] : []),
    ...(item.tags?.length ? ['', ...wrapDetail(`tags        ${item.tags.join(', ')}`, width)] : []),
    ...(item.deps?.length ? wrapDetail(`depends on   ${item.deps.join(', ')}`, width) : []),
  ];
  const view = pageSlice(lines, page, pageSize);
  return (
    <Frame title={`/item ${item.id} — ${(item.title || 'task').slice(0, Math.max(10, width - item.id.length - 18))}`} page={view.page} pages={view.pages}>
      {view.shown.map((line, index) => {
        const heading = ['WAITING ON YOU', 'OBJECTIVE', 'LAST ERROR', 'NOTES'].includes(line);
        return <Text key={`${view.page}-${index}`} bold={heading} color={line === 'WAITING ON YOU' ? theme.warning : heading ? theme.accent : undefined}>{line}</Text>;
      })}
    </Frame>
  );
}

function JournalPanel({ entries, page, pageSize }: { entries: JournalEntry[]; page: number; pageSize: number }) {
  const view = pageSlice(entries.slice(-20).reverse(), page, Math.max(2, Math.floor(pageSize / 2)));
  return (
    <Frame title="/journal" page={view.page} pages={view.pages}>
      {entries.length === 0 ? (
        <Text dimColor>(no journal entries yet)</Text>
      ) : (
        view.shown.map((j) => {
          const report = (j.extra?.planner_report ?? {}) as Record<string, unknown>;
          const detail = j.summary || String(report.headline ?? '');
          const certified = j.extra?.final_submission_certified === true;
          return (
            <Box key={j.id} flexDirection="column">
              <Box>
                <Text color={j.kind === 'mission_complete' ? theme.success : 'cyan'}>{`${j.kind}`.padEnd(18)}</Text>
                <Text>{certified ? '✓ certified · ' : ''}{j.title}</Text>
              </Box>
              {detail ? <Text dimColor>{`  ${detail.slice(0, 100)}`}</Text> : null}
            </Box>
          );
        })
      )}
    </Frame>
  );
}

function ConfigPanel({ c }: { c: ConfigSnapshot }) {
  return (
    <Frame title="/config — runtime settings">
      {c.roles.map((r) => (
        <Box key={r.role}>
          <Text color={theme.role[r.role] ?? 'white'}>{`${r.role}`.padEnd(10)}</Text>
          <Text dimColor>{`${r.backend_label} · ${r.model} · `}</Text>
          <Text color={effortColor(r.effort)}>{`effort ${r.effort ?? '—'}`}</Text>
        </Box>
      ))}
      <Text> </Text>
      <Text dimColor>NL-editable: model · effort · backend · caps · safe_mode</Text>
      <Text dimColor>full list: argus-skill --config-help</Text>
    </Frame>
  );
}

function IdentityPanel({ text, page, pageSize }: { text: string; page: number; pageSize: number }) {
  const lines = (text || '(no identity set)').split('\n');
  const view = pageSlice(lines, page, pageSize);
  return (
    <Frame title="/identity — operator card" page={view.page} pages={view.pages}>
      {view.shown.map((ln, i) => (
        <Text key={i}>{ln || ' '}</Text>
      ))}
    </Frame>
  );
}

function DaemonsPanel({
  rows,
  selected,
  pageSize,
  activeProject,
  query,
}: {
  rows: ProjectRow[];
  selected: number;
  pageSize: number;
  activeProject?: string;
  query: string;
}) {
  const ranked = filterProjects(rankProjects(rows), query);
  const safeSelection = Math.min(Math.max(0, selected), Math.max(0, ranked.length - 1));
  const view = pageSlice(ranked, Math.floor(safeSelection / pageSize), pageSize);
  return (
    <Frame
      page={view.page}
      pages={view.pages}
      title={query ? `/daemons · “${query}” · ${ranked.length}/${rows.length}` : '/daemons — select project'}
      hint={`↑/k ↓/j select${view.pages > 1 ? ` · page ${view.page + 1}/${view.pages}` : ''} · Enter switch · / search · n new · Esc close`}
    >
      {ranked.length === 0 ? (
        <Text dimColor>{query ? `(no daemons match “${query}”)` : '(no projects — press n to create one)'}</Text>
      ) : (
        view.shown.map((p, index) => {
          const absoluteIndex = view.page * pageSize + index;
          const focused = absoluteIndex === safeSelection;
          const current = p.id === activeProject;
          return (
          <Box key={p.id}>
            <Text color={focused ? theme.accent : 'gray'}>{focused ? '› ' : '  '}</Text>
            <Text color={p.daemon_alive ? theme.success : 'gray'}>{dot(p.daemon_alive)} </Text>
            <Text color={focused ? theme.accent : undefined} dimColor={!focused}>{`${p.id.slice(0, 12)}  `}</Text>
            <Text bold={focused}>{p.label || p.id}</Text>
            {p.daemon_alive ? <Text dimColor>{`  pid ${p.daemon_pid}`}</Text> : null}
            {current ? <Text color={theme.success}>  current</Text> : null}
          </Box>
          );
        })
      )}
    </Frame>
  );
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(bytes >= 10 * 1024 ? 0 : 1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(bytes >= 10 * 1024 ** 2 ? 0 : 1)} MB`;
}

function ArtifactsPanel({ rows, selected, pageSize }: { rows: ArtifactInfo[]; selected: number; pageSize: number }) {
  const safeSelection = Math.min(Math.max(0, selected), Math.max(0, rows.length - 1));
  const view = pageSlice(rows, Math.floor(safeSelection / pageSize), pageSize);
  return (
    <Frame
      title="/artifacts — latest reviewed result"
      page={view.page}
      pages={view.pages}
      hint={`↑/k ↓/j select${view.pages > 1 ? ` · page ${view.page + 1}/${view.pages}` : ''} · Enter preview · Esc close`}
    >
      {rows.length === 0 ? (
        <Text dimColor>(the latest result has no reviewer-approved files)</Text>
      ) : (
        view.shown.map((artifact, index) => {
          const focused = view.page * pageSize + index === safeSelection;
          return (
            <Box key={artifact.path} flexDirection="column">
              <Box>
                <Text color={focused ? theme.accent : 'gray'}>{focused ? '› ' : '  '}</Text>
                <Text color={artifact.exists ? theme.success : theme.error}>{artifact.exists ? '◆ ' : '× '}</Text>
                <Text bold={focused} color={focused ? theme.accent : undefined}>{artifact.path}</Text>
                <Text dimColor>{`  ${artifact.kind} · ${formatBytes(artifact.size)}`}</Text>
              </Box>
              {artifact.why ? <Text dimColor>{`    ${artifact.why.slice(0, 110)}`}</Text> : null}
            </Box>
          );
        })
      )}
    </Frame>
  );
}

function ArtifactPanel({ artifact, page, pageSize }: { artifact: ArtifactInfo; page: number; pageSize: number }) {
  const textual = ['text', 'markdown', 'json', 'table'].includes(artifact?.kind);
  const lines = textual
    ? (artifact.preview || '(empty file)').split('\n')
    : [];
  const view = pageSlice(lines, page, pageSize);
  return (
    <Frame
      title={`/artifact ${artifact?.path ?? ''}`}
      page={view.page}
      pages={view.pages}
      hint={view.pages > 1 ? `↑/k ↓/j page ${view.page + 1}/${view.pages} · Enter/Esc close` : 'Enter/Esc close'}
    >
      <Row k="type" v={`${artifact.kind} · ${artifact.mime}`} />
      <Row k="size" v={formatBytes(artifact.size)} />
      {artifact.why ? <Row k="reviewer" v={artifact.why} c={theme.accent} /> : null}
      <Text> </Text>
      {textual ? (
        <>
          {view.shown.map((line, index) => <Text key={`${view.page}-${index}`}>{line || ' '}</Text>)}
          {artifact.truncated && view.page === view.pages - 1 ? (
            <Text color={theme.warning}>… preview truncated; use the Web UI to download the complete file</Text>
          ) : null}
        </>
      ) : (
        <>
          <Text dimColor>{artifact.kind === 'binary' ? 'No safe inline preview for this file type.' : `${artifact.kind.toUpperCase()} preview is available in the Web UI.`}</Text>
          <Text dimColor>Use the authenticated Web cockpit to preview or download it.</Text>
        </>
      )}
    </Frame>
  );
}

function Row({ k, v, c }: { k: string; v: string; c?: string }) {
  return (
    <Box>
      <Text dimColor>{`${k}`.padEnd(14)}</Text>
      <Text color={c}>{v}</Text>
    </Box>
  );
}
