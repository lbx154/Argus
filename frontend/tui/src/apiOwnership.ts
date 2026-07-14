import {
  readFile,
  writeFile,
  rename,
  unlink,
} from 'node:fs/promises';

// ── Public types ────────────────────────────────────────────────────────────

export interface ApiOwnershipRecord {
  schema: 1;
  pid: number;
  host: string;
  port: number;
  backendBin: string;
  startedAt: string;
}

export interface ProcessInspection {
  alive: boolean;
  argv: string[];
}

export interface ReadOwnedApiOptions {
  path: string;
  host: string;
  port: number;
  backendBin: string;
  /** Override the OS inspector for testing. Defaults to Linux /proc reader. */
  inspect?: (pid: number) => Promise<ProcessInspection>;
}

// ── Default Linux inspector ─────────────────────────────────────────────────

async function linuxInspect(pid: number): Promise<ProcessInspection> {
  // Liveness check: signal 0 throws when process does not exist.
  let alive = false;
  try {
    process.kill(pid, 0);
    alive = true;
  } catch {
    return { alive: false, argv: [] };
  }

  try {
    const raw = await readFile(`/proc/${pid}/cmdline`);
    // /proc/<pid>/cmdline is NUL-delimited; the last byte may also be NUL.
    const argv = raw.toString('utf8').split('\0').filter(Boolean);
    return { alive, argv };
  } catch {
    // Process may have exited between the kill(0) and the read.
    return { alive: false, argv: [] };
  }
}

// ── Public API ──────────────────────────────────────────────────────────────

/**
 * Write an ownership record atomically with mode 0600.
 * Uses a sibling temp file to guarantee the rename is atomic on Linux.
 */
export async function writeOwnershipRecord(
  path: string,
  record: ApiOwnershipRecord,
): Promise<void> {
  const tmp = `${path}.tmp.${process.pid}`;
  await writeFile(tmp, JSON.stringify(record), { encoding: 'utf-8', mode: 0o600 });
  await rename(tmp, path);
}

/**
 * Remove an ownership file. Returns silently if the file does not exist.
 */
export async function removeOwnershipRecord(path: string): Promise<void> {
  try {
    await unlink(path);
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err;
  }
}

/**
 * Read and verify an ownership record.  Returns the record when all of the
 * following hold:
 *   - schema === 1
 *   - pid is a positive integer and the process is alive
 *   - host and port match the requested endpoint
 *   - backendBin matches the requested binary
 *   - argv contains the binary, `--web`, and `--web-port <port>`
 *
 * Returns null for any read, parse, inspection, or validation failure
 * (fail-closed).
 */
export async function readOwnedApi(
  opts: ReadOwnedApiOptions,
): Promise<ApiOwnershipRecord | null> {
  const { path, host, port, backendBin } = opts;
  const inspect = opts.inspect ?? linuxInspect;

  try {
    const raw = await readFile(path, 'utf-8');
    const record = JSON.parse(raw) as Partial<ApiOwnershipRecord>;

    // Schema
    if (record.schema !== 1) return null;

    // PID
    const pid = record.pid;
    if (typeof pid !== 'number' || !Number.isInteger(pid) || pid <= 0) return null;

    // Endpoint
    if (record.host !== host) return null;
    if (record.port !== port) return null;

    // Backend binary
    if (record.backendBin !== backendBin) return null;

    // Process inspection
    const { alive, argv } = await inspect(pid);
    if (!alive) return null;

    // argv must include the binary, --web, and --web-port matching requested port
    if (!argv.includes(backendBin)) return null;
    if (!argv.includes('--web')) return null;
    const webPortIdx = argv.indexOf('--web-port');
    if (webPortIdx === -1 || argv[webPortIdx + 1] !== String(port)) return null;

    return record as ApiOwnershipRecord;
  } catch {
    return null;
  }
}
