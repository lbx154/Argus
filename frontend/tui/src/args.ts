import { defaultApiOwnershipPath } from './apiOwnership.js';

export interface Args {
  host: string;
  port: number;
  portExplicit: boolean;
  project?: string;
  resume: boolean;
  resumeAll: boolean;
  token?: string;
  ownerFile?: string;
  once: boolean;
  json: boolean;
  count: number;
  help: boolean;
  web: boolean;
  openWebWithCli: boolean;
  noOpen: boolean;
  objective: string;
  forceNew: boolean;
  exitPolicy: ExitPolicy;
  ownerFileExplicit: boolean;
}

export type ExitPolicy = 'detach' | 'stop-api' | 'stop-all';

function exitPolicy(value: string | undefined, source: string): ExitPolicy {
  const normalized = value?.trim() || 'detach';
  if (normalized === 'detach' || normalized === 'stop-api' || normalized === 'stop-all') {
    return normalized;
  }
  throw new Error(`${source} must be detach, stop-api, or stop-all; got ${normalized}`);
}

function valueAfter(argv: string[], index: number, option: string): string {
  const value = argv[index + 1];
  if (!value || value.startsWith('-')) {
    throw new Error(`${option} requires a value`);
  }
  return value;
}

export interface ParseArgsRuntime {
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
}

/** Parse both the native Ink flags and the retained argus-skill compatibility flags. */
export function parseArgs(argv: string[], runtime: ParseArgsRuntime = {}): Args {
  const env = runtime.env ?? process.env;
  const platform = runtime.platform ?? process.platform;
  const envPort = env.ARGUS_TUI_PORT;
  const ownerFile = env.ARGUS_TUI_API_OWNER_FILE?.trim();
  const args: Args = {
    host: env.ARGUS_TUI_HOST ?? '127.0.0.1',
    port: Number(envPort ?? 8799),
    portExplicit: envPort !== undefined,
    project: env.ARGUS_TUI_PROJECT,
    resume: false,
    resumeAll: false,
    token: env.ARGUS_SKILL_WEB_TOKEN,
    ownerFile: undefined,
    once: false,
    json: false,
    count: 5,
    help: false,
    web: false,
    openWebWithCli: platform === 'win32',
    noOpen: false,
    objective: '',
    forceNew: false,
    exitPolicy: exitPolicy(env.ARGUS_TUI_EXIT_POLICY, 'ARGUS_TUI_EXIT_POLICY'),
    ownerFileExplicit: Boolean(ownerFile),
  };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--host') {
      args.host = valueAfter(argv, i, arg);
      i += 1;
    } else if (arg === '--port') {
      args.port = Number(valueAfter(argv, i, arg));
      args.portExplicit = true;
      i += 1;
    } else if (arg === '--project') {
      args.project = valueAfter(argv, i, arg);
      i += 1;
    } else if (arg === 'resume' || arg === '--resume' || arg === '-r') {
      const requested = argv[i + 1];
      if (requested && !requested.startsWith('-')) {
        args.project = requested;
        i += 1;
      } else {
        args.resume = true;
      }
    } else if (arg === '--all') {
      args.resumeAll = true;
    } else if (arg === '--token') {
      args.token = valueAfter(argv, i, arg);
      i += 1;
    } else if (arg === '--count') {
      args.count = Number(valueAfter(argv, i, arg));
      i += 1;
    } else if (arg === '--once') args.once = true;
    else if (arg === '--json') args.json = true;
    else if (arg === '--web') args.web = true;
    else if (arg === '--no-open') args.noOpen = true;
    else if (arg === '--objective') {
      args.objective = valueAfter(argv, i, arg);
      i += 1;
    } else if (arg === '--continue') {
      args.resume = true;
    } else if (arg === '--new') {
      args.project = undefined;
      args.resume = false;
      args.resumeAll = false;
      args.forceNew = true;
    } else if (arg === '--exit-policy') {
      args.exitPolicy = exitPolicy(valueAfter(argv, i, arg), '--exit-policy');
      i += 1;
    } else if (arg === '-h' || arg === '--help') {
      args.help = true;
    }
  }

  if (!Number.isFinite(args.port) || args.port <= 0 || args.port > 65_535) {
    throw new Error(`--port must be between 1 and 65535; got ${args.port}`);
  }
  if (!Number.isInteger(args.count) || args.count < 1) {
    throw new Error(`--count must be a positive integer; got ${args.count}`);
  }
  args.ownerFile = ownerFile
    || defaultApiOwnershipPath(args.host, args.port);
  return args;
}

export function withSelectedPort(args: Args, port: number): Args {
  return {
    ...args,
    port,
    ownerFile: args.ownerFileExplicit
      ? args.ownerFile
      : defaultApiOwnershipPath(args.host, port),
  };
}

export const HELP = `argus — the terminal cockpit for the argus-skill autonomous-research daemon

Usage: argus resume [SID] [--all]
       argus [--resume [SID]] [--host H] [--port P] [--project SID] [--token T]
             [--exit-policy detach|stop-api|stop-all]
       argus --web [--no-open]  # start Web UI and open/print its URL
       argus --once --json   # headless smoke: fetch snapshot + N events, print JSON, exit
       argus render-events [--locale en|zh-CN]
             [--unknown-event-policy hide|greppable] [--density compact|full]
             [--show-reasoning]  # NDJSON stdin to one plain-text line per event

Every launch compares the local source identity with the running backend. It
auto-starts a missing backend and safely replaces an outdated backend only when
process ownership is proven; unrelated port occupants are never signalled.
Without an explicit port, Argus reuses a compatible backend or selects the
first available port starting at 8799. A
plain interactive launch reattaches to a live executor from this directory, or
creates a fresh idle session when none is running. argus resume shows
conversations from this directory; add --all for every account session.
On Windows, a plain interactive launch also opens the Web UI.

The terminal UI, local API server, and per-session executor are separate
processes. Ctrl-D (or Ctrl-C twice in the live view) exits only this UI by
default; the API and executor remain available for Web/reconnect. Use an exit
policy when this invocation should also perform graceful cleanup.

Options:
  --host H       API host (default 127.0.0.1, env ARGUS_TUI_HOST)
  --port P       pin the API port (otherwise first available from 8799;
                 env ARGUS_TUI_PORT)
  --project SID  project/session id (interactive recovers; --once is strict)
  -r, --resume   open the local resume picker; an optional SID resumes directly
  --continue     compatibility alias for the local resume picker
  --all          with resume, include sessions launched outside this directory
  --new          force a fresh session instead of reattaching to local live work
  --token T      bearer token if the API requires one (env ARGUS_SKILL_WEB_TOKEN)
  --web          ensure the Web UI backend is running, then open it in a browser
  --no-open      do not launch a browser (including the Windows interactive default)
  --objective X  create and immediately start a fresh campaign with objective X
  --exit-policy P  detach (default), stop-api, or stop-all
                   stop-api stops only an API safely owned by this invocation;
                   stop-all first stops the current executor, then that API
                   (env ARGUS_TUI_EXIT_POLICY)
  --once --json  connect, print a JSON snapshot+events sample, exit 0 (CI/headless)
  --count N      events to collect in --once mode (default 5)
`;
