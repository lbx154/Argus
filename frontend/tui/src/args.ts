import { defaultApiOwnershipPath } from './apiOwnership.js';

export interface Args {
  host: string;
  port: number;
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
  noOpen: boolean;
  objective: string;
}

function valueAfter(argv: string[], index: number, option: string): string {
  const value = argv[index + 1];
  if (!value || value.startsWith('-')) {
    throw new Error(`${option} requires a value`);
  }
  return value;
}

/** Parse both the native Ink flags and the retained argus-skill compatibility flags. */
export function parseArgs(argv: string[]): Args {
  const args: Args = {
    host: process.env.ARGUS_TUI_HOST ?? '127.0.0.1',
    port: Number(process.env.ARGUS_TUI_PORT ?? 8799),
    project: process.env.ARGUS_TUI_PROJECT,
    resume: false,
    resumeAll: false,
    token: process.env.ARGUS_SKILL_WEB_TOKEN,
    ownerFile: undefined,
    once: false,
    json: false,
    count: 5,
    help: false,
    web: false,
    noOpen: false,
    objective: '',
  };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--host') {
      args.host = valueAfter(argv, i, arg);
      i += 1;
    } else if (arg === '--port') {
      args.port = Number(valueAfter(argv, i, arg));
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
  args.ownerFile = process.env.ARGUS_TUI_API_OWNER_FILE?.trim()
    || defaultApiOwnershipPath(args.host, args.port);
  return args;
}

export const HELP = `argus — the terminal cockpit for the argus-skill autonomous-research daemon

Usage: argus resume [SID] [--all]
       argus [--resume [SID]] [--host H] [--port P] [--project SID] [--token T]
       argus --web [--no-open]  # start Web UI and open/print its URL
       argus --once --json   # headless smoke: fetch snapshot + N events, print JSON, exit

On launch it auto-starts the backend API (argus-skill --web) if it isn't already
running. It records ownership of local APIs and safely replaces an outdated API
from the same installation. A plain interactive launch creates a fresh idle session. argus resume
shows conversations from this directory; add --all for every account session.

Options:
  --host H       API host (default 127.0.0.1, env ARGUS_TUI_HOST)
  --port P       API port (default 8799, env ARGUS_TUI_PORT)
  --project SID  project/session id (interactive recovers; --once is strict)
  -r, --resume   open the local resume picker; an optional SID resumes directly
  --continue     compatibility alias for the local resume picker
  --all          with resume, include sessions launched outside this directory
  --new          force a fresh session (the default)
  --token T      bearer token if the API requires one (env ARGUS_SKILL_WEB_TOKEN)
  --web          ensure the Web UI backend is running, then open it in a browser
  --no-open      with --web, print the URL without launching a local browser
  --objective X  create and immediately start a fresh campaign with objective X
  --once --json  connect, print a JSON snapshot+events sample, exit 0 (CI/headless)
  --count N      events to collect in --once mode (default 5)
`;
