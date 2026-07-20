import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, render, Text } from 'ink';
import {
  ApiClient,
  type CreatedDaemon,
  type DaemonStartResult,
  type ProjectRow,
} from './api.js';
import { App } from './App.js';
import { HELP, parseArgs, type Args } from './args.js';
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
import { createImeCursorOutput, ImeCursorProvider } from './imeCursor.js';

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
  const [initialAdmission, setInitialAdmission] = useState<DaemonStartResult | undefined>();
  const [initialResumeContinuous, setInitialResumeContinuous] = useState(false);
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
        ownerFile: args.ownerFile,
        onStatus: (s) => !cancelled && setNote(s),
        onWarning: (warning) => {
          if (!cancelled) setInitialNotice(`warning: ${warning}`);
        },
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
          ? await base.createDaemon(args.objective)
          : null;
        const resumable = startup.kind === 'pick'
          ? projectsForLaunchCwd(await base.listProjects(), launchCwd, args.resumeAll)
          : [];
        const sid = created?.sid ?? selection?.id ?? null;
        if (cancelled) return;
        setProjects(resumable);
        if (sid) setProject(sid);
        if (created) {
          setInitialAdmission(created.start);
          setInitialResumeContinuous(Boolean(created.objective));
          const createdNotice = created.start?.admission_required
              ? `created ${created.sid} · choose running work to park`
              : `created ${created.sid} · message Argus when ready`;
          setInitialNotice((current) => [current, createdNotice].filter(Boolean).join(' · '));
        } else if (selection?.recovered && sid) {
          const recoveredNotice = `requested ${selection.requested} not found · attached to ${sid}`;
          setInitialNotice((current) => [current, recoveredNotice].filter(Boolean).join(' · '));
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
  }, [args.host, args.objective, args.port, args.project, args.resume, args.resumeAll, args.token, base, launchCwd]);

  const onSplashDone = () => {
    splashDone.current = true;
    setPhase(destination.current);
  };

  const onFirstDaemon = (created: CreatedDaemon) => {
    destination.current = 'live';
    setProject(created.sid);
    setInitialAdmission(created.start);
    setInitialResumeContinuous(Boolean(created.objective));
    const createdNotice = created.spawned
        ? `created ${created.sid} · campaign started`
        : `created ${created.sid} · message Argus when ready`;
    setInitialNotice((current) => [current, createdNotice].filter(Boolean).join(' · '));
    setPhase('live');
  };

  const onResume = (selected: ProjectRow) => {
    destination.current = 'live';
    setProject(selected.id);
    const resumedNotice = `resumed ${selected.label || selected.id}`;
    setInitialNotice((current) => [current, resumedNotice].filter(Boolean).join(' · '));
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
    return (
      <App
        host={args.host}
        port={args.port}
        token={args.token}
        project={project}
        initialNotice={initialNotice}
        initialAdmission={initialAdmission}
        initialResumeContinuous={initialResumeContinuous}
      />
    );
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
      ownerFile: args.ownerFile,
      onStatus: (status) => process.stderr.write(`${status}\n`),
      onWarning: (warning) => process.stderr.write(`argus: warning: ${warning}\n`),
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
    const probe = new ApiClient({
      host: args.host,
      port: args.port,
      project: '_',
      token: args.token,
      onCompatibilityWarning: (warning) => {
        process.stderr.write(`argus: warning: ${warning}\n`);
      },
    });
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
  const imeCursor = createImeCursorOutput(process.stdout);
  const instance = render(
    <ImeCursorProvider controller={imeCursor.controller}>
      <Boot args={args} animate={canAnimate} />
    </ImeCursorProvider>,
    { exitOnCtrlC: false, stdout: imeCursor.stdout },
  );
  void instance.waitUntilExit().finally(imeCursor.dispose);
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
