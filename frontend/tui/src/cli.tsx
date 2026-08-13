import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, render, Text } from 'ink';
import {
  ApiClient,
  type CreatedDaemon,
  type DaemonStartResult,
  type ProjectRow,
} from './api.js';
import { App } from './App.js';
import { HELP, parseArgs, withSelectedPort, type Args } from './args.js';
import { FirstRun } from './components/FirstRun.js';
import { ResumePicker } from './components/ResumePicker.js';
import { Splash } from './components/Splash.js';
import { StartupExitKeys } from './components/StartupExitKeys.js';
import { Wordmark } from './components/Wordmark.js';
import {
  ensureApi,
  resolveBin,
  scheduleOutdatedDaemonUpgrades,
  uniqueWarningReporter,
} from './ensureApi.js';
import { SPINNER, theme } from './theme.js';
import {
  initialProjectSelection,
  interactiveStartup,
  liveProjectsForLaunchCwd,
} from './initialProject.js';
import { projectsForLaunchCwd } from '../../core/src/projects.js';
import { openWebBrowser, resolvePairing, webUiUrl, withProject } from './webLaunch.js';
import { createImeCursorOutput, ImeCursorProvider } from './imeCursor.js';
import { InteractiveExitLifecycle } from './exitLifecycle.js';
import { selectApiPort } from './portSelection.js';

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
      <Text dimColor>Ctrl-C or Ctrl-D exits this UI; API/executors are separate.</Text>
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
function Boot({
  args,
  animate,
  lifecycle,
}: {
  args: Args;
  animate: boolean;
  lifecycle: InteractiveExitLifecycle;
}) {
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
  const exitRequested = useRef(false);
  const webOpened = useRef(false);
  const destination = useRef<'connecting' | 'picker' | 'empty' | 'live'>('connecting');
  const base = useMemo(
    () => new ApiClient({ host: args.host, port: args.port, project: '_', token: args.token }),
    [args.host, args.port, args.token],
  );

  useEffect(() => {
    let cancelled = false;
    const uiCancelled = () => cancelled || exitRequested.current;
    (async () => {
      const pendingEnsure = ensureApi({
        host: args.host,
        port: args.port,
        token: args.token,
        ownerFile: args.ownerFile,
        onStatus: (s) => !uiCancelled() && setNote(s),
        onWarning: (warning) => {
          if (!uiCancelled()) setInitialNotice(`warning: ${warning}`);
        },
      });
      lifecycle.trackEnsure(pendingEnsure);
      const res = await pendingEnsure;
      if (uiCancelled()) return;
      lifecycle.acceptEnsureResult(res);
      if (!res.reachable) {
        setErr(res.message);
        setPhase('error');
        return;
      }
      if (args.openWebWithCli && !args.noOpen && !webOpened.current) {
        webOpened.current = true;
        openWebBrowser(webUiUrl(args.host, args.port, args.project, args.token));
      }
      setNote('connecting…');
      try {
        const availableProjects = await base.listProjects();
        if (uiCancelled()) return;
        const upgrades = await scheduleOutdatedDaemonUpgrades(
          availableProjects,
          (sid) => base.scheduleDaemonUpgrade(sid),
        );
        if (uiCancelled()) return;
        if (upgrades.scheduled.length > 0) {
          setInitialNotice((current) => [
            current,
            `${upgrades.scheduled.length} outdated daemon(s) will upgrade at the next mission boundary`,
          ].filter(Boolean).join(' · '));
        }
        if (upgrades.failed.length > 0) {
          setInitialNotice((current) => [
            current,
            `warning: could not schedule ${upgrades.failed.length} daemon upgrade(s)`,
          ].filter(Boolean).join(' · '));
        }
        const startup = interactiveStartup(args.project, args.resume);
        const selection = startup.kind === 'resume'
          ? initialProjectSelection(availableProjects, startup.project)
          : null;
        const liveMatches = startup.kind === 'fresh'
          && !args.forceNew
          && !args.objective.trim()
          ? liveProjectsForLaunchCwd(availableProjects, launchCwd)
          : [];
        const attached = liveMatches[0] ?? null;
        const created = startup.kind === 'fresh' && !attached
          ? await lifecycle.trackDaemonCreation(base.createDaemon(args.objective))
          : null;
        const resumable = startup.kind === 'pick'
          ? projectsForLaunchCwd(availableProjects, launchCwd, args.resumeAll)
          : [];
        const sid = created?.sid ?? attached?.id ?? selection?.id ?? null;
        if (uiCancelled()) return;
        setProjects(resumable);
        if (sid) {
          setProject(sid);
          lifecycle.setCurrentProject(sid);
        }
        if (created) {
          setInitialAdmission(created.start);
          setInitialResumeContinuous(Boolean(created.objective));
          const createdNotice = created.start?.admission_required
              ? `created ${created.sid} · choose running work to park`
              : `created ${created.sid} · message Argus when ready`;
          setInitialNotice((current) => [current, createdNotice].filter(Boolean).join(' · '));
        } else if (attached) {
          const attachedLabel = attached.label || attached.display_name || attached.id;
          const attachedNotice = liveMatches.length > 1
            ? `found ${liveMatches.length} live sessions for this folder · resumed ${attachedLabel}`
            : `resumed running session ${attachedLabel} · reused its existing executor`;
          setInitialNotice((current) => [current, attachedNotice].filter(Boolean).join(' · '));
        } else if (selection?.recovered && sid) {
          const recoveredNotice = `requested ${selection.requested} not found · attached to ${sid}`;
          setInitialNotice((current) => [current, recoveredNotice].filter(Boolean).join(' · '));
        }
        destination.current = sid ? 'live' : startup.kind === 'pick' ? 'picker' : 'empty';
        if (splashDone.current) setPhase(destination.current);
      } catch (e) {
        if (!uiCancelled()) {
          setErr((e as Error).message);
          setPhase('error');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [args.forceNew, args.host, args.noOpen, args.objective, args.openWebWithCli, args.port, args.project, args.resume, args.resumeAll, args.token, base, launchCwd, lifecycle]);

  const onSplashDone = () => {
    if (exitRequested.current) return;
    splashDone.current = true;
    setPhase(destination.current);
  };

  const onFirstDaemon = (created: CreatedDaemon) => {
    destination.current = 'live';
    setProject(created.sid);
    lifecycle.setCurrentProject(created.sid);
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
    lifecycle.setCurrentProject(selected.id);
    const resumedNotice = `resumed ${selected.label || selected.id}`;
    setInitialNotice((current) => [current, resumedNotice].filter(Boolean).join(' · '));
    setPhase('live');
  };

  if (phase === 'error') {
    return (
      <>
        <StartupExitKeys active onExit={() => { exitRequested.current = true; }} />
        <Box flexDirection="column" paddingX={1}>
          <Wordmark />
          <Text color={theme.error}>{`argus: ${err}`}</Text>
          <Text dimColor>Ctrl-C or Ctrl-D exits this terminal UI.</Text>
        </Box>
      </>
    );
  }
  if (phase === 'splash') {
    return (
      <>
        <StartupExitKeys active onExit={() => { exitRequested.current = true; }} />
        <Splash onDone={onSplashDone} />
      </>
    );
  }
  if (phase === 'picker') {
    return (
      <ResumePicker
        projects={projects}
        scopeLabel={args.resumeAll ? 'all account sessions' : launchCwd}
        onSelect={(selected) => { void onResume(selected); }}
      />
    );
  }
  if (phase === 'empty') return (
    <FirstRun
      createDaemon={(objective, name) => lifecycle.trackDaemonCreation(base.createDaemon(objective, name))}
      onCreated={onFirstDaemon}
    />
  );
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
        exitPolicy={args.exitPolicy}
        onProjectChange={(sid) => lifecycle.setCurrentProject(sid)}
        trackDaemonCreation={(promise) => lifecycle.trackDaemonCreation(promise)}
      />
    );
  }
  return (
    <>
      <StartupExitKeys active onExit={() => { exitRequested.current = true; }} />
      <Connecting note={note} />
    </>
  );
}

async function main() {
  let args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(HELP);
    return;
  }

  const selectedPort = await selectApiPort({
    host: args.host,
    preferredPort: args.port,
    token: args.token,
    explicit: args.portExplicit,
  });
  if (selectedPort !== args.port) args = withSelectedPort(args, selectedPort);

  if (args.web) {
    // Resolve pairing before starting the backend: on a non-loopback bind the
    // token may be minted here, and the backend has to be started with it.
    const plan = resolvePairing(resolveBin(), args.host, args.port);
    const token = args.token || plan?.token || undefined;
    const result = await ensureApi({
      host: args.host,
      port: args.port,
      token,
      ownerFile: args.ownerFile,
      onStatus: (status) => process.stderr.write(`${status}\n`),
      onWarning: (warning) => process.stderr.write(`argus: warning: ${warning}\n`),
    });
    if (!result.reachable) {
      process.stderr.write(`argus: ${result.message}\n`);
      process.exitCode = 2;
      return;
    }
    const url = plan
      ? withProject(plan.url, args.project)
      : webUiUrl(args.host, args.port, args.project, token);
    const opened = !args.noOpen && openWebBrowser(url);
    // A bind other devices can reach gets the full banner — reachable address,
    // token, and a QR code to scan from a phone.
    if (plan?.pairing && plan.banner) process.stdout.write(`${plan.banner}\n`);
    else process.stdout.write(`${opened ? 'Opened' : 'Argus Web UI'}: ${url}\n`);
    if (!opened && !args.noOpen) {
      process.stdout.write('No desktop browser detected; open the URL locally or forward this port over SSH.\n');
    }
    return;
  }

  if (args.once) {
    const reportWarning = uniqueWarningReporter(
      (warning) => process.stderr.write(`argus: warning: ${warning}\n`),
    );
    const ready = await ensureApi({
      host: args.host,
      port: args.port,
      token: args.token,
      ownerFile: args.ownerFile,
      onWarning: reportWarning,
    });
    if (!ready.reachable) {
      process.stderr.write(`argus: ${ready.message}\n`);
      process.exitCode = 2;
      return;
    }
    const probe = new ApiClient({
      host: args.host,
      port: args.port,
      project: '_',
      token: args.token,
      onCompatibilityWarning: reportWarning,
    });
    let project: string;
    try {
      const availableProjects = await probe.listProjects();
      await scheduleOutdatedDaemonUpgrades(
        availableProjects,
        (sid) => probe.scheduleDaemonUpgrade(sid),
      );
      const selection = initialProjectSelection(availableProjects, args.project);
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
  const lifecycle = new InteractiveExitLifecycle({
    host: args.host,
    port: args.port,
    token: args.token,
    policy: args.exitPolicy,
  });
  const instance = render(
    <ImeCursorProvider controller={imeCursor.controller}>
      <Boot args={args} animate={canAnimate} lifecycle={lifecycle} />
    </ImeCursorProvider>,
    { exitOnCtrlC: false, stdout: imeCursor.stdout },
  );
  try {
    await instance.waitUntilExit();
  } finally {
    imeCursor.dispose();
    const cleanup = await lifecycle.cleanup();
    for (const warning of cleanup.warnings) {
      process.stderr.write(`argus: warning: ${warning}\n`);
    }
  }
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
    mission_summary: snap.mission_view?.mission.summary ?? '',
    events,
  };
  process.stdout.write(JSON.stringify(out, null, 2) + '\n');
  process.exit(0);
}

main().catch((err) => {
  const message = err instanceof Error ? err.message : String(err);
  process.stderr.write(`argus: ${message}\n`);
  process.exit(1);
});
