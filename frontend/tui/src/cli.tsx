import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, render, Text } from 'ink';
import { ApiClient, type CreatedDaemon, type ProjectRow } from './api.js';
import { App } from './App.js';
import { FirstRun } from './components/FirstRun.js';
import { ResumePicker } from './components/ResumePicker.js';
import { Splash } from './components/Splash.js';
import { Wordmark } from './components/Wordmark.js';
import { ensureApi } from './ensureApi.js';
import { SPINNER, theme } from './theme.js';
import { initialProjectSelection, interactiveStartup } from './initialProject.js';
import type { ProjectSelection } from '../../core/src/projects.js';
import { projectsForLaunchCwd } from '../../core/src/projects.js';
import { openWebBrowser, webUiUrl } from './webLaunch.js';

interface Args {
  host: string;
  port: number;
  project?: string;
  resume: boolean;
  resumeAll: boolean;
  token?: string;
  once: boolean;
  json: boolean;
  count: number;
  help: boolean;
  web: boolean;
  noOpen: boolean;
}

function parseArgs(argv: string[]): Args {
  const a: Args = {
    host: process.env.ARGUS_TUI_HOST ?? '127.0.0.1',
    port: Number(process.env.ARGUS_TUI_PORT ?? 8799),
    project: process.env.ARGUS_TUI_PROJECT,
    resume: false,
    resumeAll: false,
    token: process.env.ARGUS_SKILL_WEB_TOKEN,
    once: false,
    json: false,
    count: 5,
    help: false,
    web: false,
    noOpen: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const eat = () => argv[++i];
    if (arg === '--host') a.host = eat();
    else if (arg === '--port') a.port = Number(eat());
    else if (arg === '--project') a.project = eat();
    else if (arg === 'resume' || arg === '--resume' || arg === '-r') a.resume = true;
    else if (arg === '--all') a.resumeAll = true;
    else if (arg === '--token') a.token = eat();
    else if (arg === '--count') a.count = Number(eat());
    else if (arg === '--once') a.once = true;
    else if (arg === '--json') a.json = true;
    else if (arg === '--web') a.web = true;
    else if (arg === '--no-open') a.noOpen = true;
    else if (arg === '-h' || arg === '--help') a.help = true;
  }
  return a;
}

const HELP = `argus — the terminal cockpit for the argus-skill autonomous-research daemon

Usage: argus resume [--all]
       argus [--resume] [--host H] [--port P] [--project SID] [--token T]
       argus --web [--no-open]  # start Web UI and open/print its URL
       argus --once --json   # headless smoke: fetch snapshot + N events, print JSON, exit

On launch it auto-starts the backend API (argus-skill --web) if it isn't already
running. A plain interactive launch creates a fresh idle session. argus resume
shows conversations from this directory; add --all for every account session.

Options:
  --host H       API host (default 127.0.0.1, env ARGUS_TUI_HOST)
  --port P       API port (default 8799, env ARGUS_TUI_PORT)
  --project SID  project/session id (interactive recovers; --once is strict)
  -r, --resume   compatibility alias for argus resume
  --all          with resume, include sessions launched outside this directory
  --token T      bearer token if the API requires one (env ARGUS_SKILL_WEB_TOKEN)
  --web          ensure the Web UI backend is running, then open it in a browser
  --no-open      with --web, print the URL without launching a local browser
  --once --json  connect, print a JSON snapshot+events sample, exit 0 (CI/headless)
  --count N      events to collect in --once mode (default 5)
`;

async function resolveProject(base: ApiClient, given?: string): Promise<ProjectSelection> {
  return initialProjectSelection(await base.listProjects(), given);
}

/** A small spinner shown if the animation finishes before the API is reachable. */
function Connecting({ note }: { note: string }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((x) => (x + 1) % SPINNER.length), 90);
    return () => clearInterval(id);
  }, []);
  return (
    <Box flexDirection="column" paddingX={1}>
      <Wordmark />
      <Box marginTop={1}>
        <Text color={theme.accent}>{SPINNER[i]} </Text>
        <Text dimColor>{note}</Text>
      </Box>
    </Box>
  );
}

/**
 * Boot orchestrator — renders IMMEDIATELY so the splash starts with zero
 * latency; ensureApi + project resolution run in the BACKGROUND behind the
 * animation (this is what makes startup feel smooth). Goes live only once the
 * splash is done AND the API is reachable; if the API comes up slower than the
 * animation, a "connecting…" spinner bridges the gap.
 */
function Boot({ args, animate }: { args: Args; animate: boolean }) {
  const [phase, setPhase] = useState<'splash' | 'connecting' | 'picker' | 'empty' | 'live' | 'error'>(
    animate ? 'splash' : 'connecting',
  );
  const [project, setProject] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const launchCwd = process.cwd();
  const [initialNotice, setInitialNotice] = useState('');
  const [note, setNote] = useState('starting backend…');
  const [err, setErr] = useState('');
  const splashDone = useRef(!animate);
  const destination = useRef<'connecting' | 'picker' | 'empty' | 'live'>('connecting');
  const base = useMemo(
    () => new ApiClient({ host: args.host, port: args.port, project: '_', token: args.token }),
    [args.host, args.port, args.token],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const res = await ensureApi({
        host: args.host,
        port: args.port,
        token: args.token,
        onStatus: (s) => !cancelled && setNote(s),
      });
      if (cancelled) return;
      if (!res.reachable) {
        setErr(res.message);
        setPhase('error');
        return;
      }
      setNote('connecting…');
      try {
        const startup = interactiveStartup(args.project, args.resume);
        const selection = startup.kind === 'resume'
          ? await resolveProject(base, startup.project)
          : null;
        const created = startup.kind === 'fresh'
          ? await base.createDaemon()
          : null;
        const resumable = startup.kind === 'pick'
          ? projectsForLaunchCwd(await base.listProjects(), launchCwd, args.resumeAll)
          : [];
        const sid = created?.sid ?? selection?.id ?? null;
        if (cancelled) return;
        setProjects(resumable);
        if (sid) setProject(sid);
        if (created) {
          setInitialNotice(`created ${created.sid} · message Argus when ready`);
        } else if (selection?.recovered && sid) {
          setInitialNotice(`requested ${selection.requested} not found · attached to ${sid}`);
        }
        destination.current = sid ? 'live' : startup.kind === 'pick' ? 'picker' : 'empty';
        if (splashDone.current) setPhase(destination.current);
      } catch (e) {
        if (!cancelled) {
          setErr((e as Error).message);
          setPhase('error');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [args.host, args.port, args.project, args.resume, args.resumeAll, args.token, base, launchCwd]);

  const onSplashDone = () => {
    splashDone.current = true;
    setPhase(destination.current);
  };

  const onFirstDaemon = (created: CreatedDaemon) => {
    destination.current = 'live';
    setProject(created.sid);
    setInitialNotice(
      created.spawned
        ? `created ${created.sid} · campaign started`
        : `created ${created.sid} · message Argus when ready`,
    );
    setPhase('live');
  };

  const onResume = async (selected: ProjectRow) => {
    try {
      await base.setProjectLaunchCwd(selected.id, launchCwd);
    } catch (error) {
      setErr(`could not bind ${selected.id} to ${launchCwd}: ${(error as Error).message}`);
      setPhase('error');
      return;
    }
    destination.current = 'live';
    setProject(selected.id);
    setInitialNotice(`resumed ${selected.label || selected.id}`);
    setPhase('live');
  };

  if (phase === 'error') {
    return (
      <Box flexDirection="column" paddingX={1}>
        <Wordmark />
        <Text color={theme.error}>{`argus: ${err}`}</Text>
      </Box>
    );
  }
  if (phase === 'splash') return <Splash onDone={onSplashDone} />;
  if (phase === 'picker') {
    return (
      <ResumePicker
        projects={projects}
        scopeLabel={args.resumeAll ? 'all account sessions' : launchCwd}
        onSelect={(selected) => { void onResume(selected); }}
      />
    );
  }
  if (phase === 'empty') return <FirstRun createDaemon={(objective, name) => base.createDaemon(objective, name)} onCreated={onFirstDaemon} />;
  if (phase === 'live' && project) {
    return <App host={args.host} port={args.port} token={args.token} project={project} initialNotice={initialNotice} />;
  }
  return <Connecting note={note} />;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(HELP);
    return;
  }

  if (args.web) {
    const result = await ensureApi({
      host: args.host,
      port: args.port,
      token: args.token,
      onStatus: (status) => process.stderr.write(`${status}\n`),
    });
    if (!result.reachable) {
      process.stderr.write(`argus: ${result.message}\n`);
      process.exitCode = 2;
      return;
    }
    const url = webUiUrl(args.host, args.port, args.project);
    const opened = !args.noOpen && openWebBrowser(url);
    process.stdout.write(`${opened ? 'Opened' : 'Argus Web UI'}: ${url}\n`);
    if (!opened && !args.noOpen) {
      process.stdout.write('No desktop browser detected; open the URL locally or forward this port over SSH.\n');
    }
    return;
  }

  if (args.once) {
    const probe = new ApiClient({ host: args.host, port: args.port, project: '_', token: args.token });
    let project: string;
    try {
      const selection = await resolveProject(probe, args.project);
      if (selection.recovered) throw new Error(`project "${selection.requested}" not found`);
      if (!selection.id) throw new Error('no projects found');
      project = selection.id;
    } catch (err) {
      process.stderr.write(`argus: ${(err as Error).message}\n`);
      process.exit(2);
      return;
    }
    await runOnce(new ApiClient({ host: args.host, port: args.port, project, token: args.token }), args.count);
    return;
  }

  // Interactive: render immediately; connect in the background (smooth startup).
  const canAnimate = !!process.stdout.isTTY && !process.env.NO_COLOR && !process.env.CI;
  render(<Boot args={args} animate={canAnimate} />, { exitOnCtrlC: false });
}

/** Headless data-chain smoke: prove REST + WS work without the render layer. */
async function runOnce(api: ApiClient, count: number): Promise<void> {
  const snap = await api.snapshot();
  const events: string[] = [];
  await new Promise<void>((resolve) => {
    const done = () => {
      ws.close();
      resolve();
    };
    const ws = api.connectStream({
      replay: count,
      onEvent: (ev) => {
        events.push(String(ev.type ?? 'event'));
        if (events.length >= count) done();
      },
      onError: () => done(),
    });
    setTimeout(done, 4000);
  });
  const out = {
    project: api.project,
    daemon_alive: snap.daemon.alive,
    roles: snap.roles.map((r) => `${r.role}:${r.active ? 'active' : 'idle'}`),
    backlog: snap.backlog.length,
    events,
  };
  process.stdout.write(JSON.stringify(out, null, 2) + '\n');
  process.exit(0);
}

main().catch((err) => {
  process.stderr.write(`argus: ${(err as Error).stack ?? err}\n`);
  process.exit(1);
});
