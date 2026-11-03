// The machine the work runs on, read when the page asks: its CPUs and memory, and the NVIDIA GPUs
// nvidia-smi lists, none where it is not installed. Argus limits none of them.
// Also what runs in a project: on Linux the commands running in it, elsewhere the logs it is writing. Read
// from the machine, so watching an experiment costs no model call.

import { execFile } from 'node:child_process';
import { open, opendir, readdir, readFile, readlink, stat, statfs } from 'node:fs/promises';
import { availableParallelism, freemem, loadavg, totalmem } from 'node:os';
import { basename, join, relative, sep } from 'node:path';
import { promisify } from 'node:util';
import type { Handler } from './index.js';
import type { Project } from '../store.js';

const run = promisify(execFile);
const GIB = 1 << 30;

export function resources(project: (id: string) => Project): [string, RegExp, Handler][] {
  return [['GET', /^\/api\/system\/resources$/, async ({ url }) => {
    const id = url.searchParams.get('sid');
    const workdir = id ? project(id).workdir : null;
    const [devices, disk] = await Promise.all([gpus(), workdir ? storage(workdir) : undefined]);
    return { schema_version: 1, enforcement: 'advisory', accelerators: [cpu(), ...devices], ...disk ? { disk } : {} };
  }], ['GET', /^\/api\/system\/experiments$/, async ({ url }) => {
    const id = url.searchParams.get('sid');
    return id ? experiments(project(id).workdir) : { processes: false, experiments: [] };
  }]];
}

/** The longest lines and how much of a log's end is read for them. */
const TAIL_LINES = 12;
const TAIL_BYTES = 64 << 10;
/** What a run writes its output to: `train.log`, `job.err`, `nohup.out`. */
const LOG = /\.(log|err)$|^nohup\.out$/i;
/** Where commands cannot be seen, a log written this recently is shown: a run logging every few minutes stays in view. */
const RECENT_MS = 10 * 60_000;
/** How much of the project is read for logs each time the page asks, shallowest first, and how many are shown. */
const WALK_DEPTH = 4;
const WALK_ENTRIES = 5000;
const LOGS_SHOWN = 3;

interface Running {
  pid: number; ppid: number; command: string; seconds: number;
  /** The newest written of its stdout, stderr and open logs in the project, and the descriptor it writes it by. */
  log: { path: string; fd: string } | null;
}

/**
 * What runs in the project. Where the system shows commands (Linux's /proc): those running in its folder
 * that write to a file there or use a GPU. Elsewhere, the logs in it written lately, and `processes` false.
 */
export async function experiments(workdir: string) {
  const running = await commands(workdir);
  if (running) return { processes: true, experiments: running };
  return {
    processes: false,
    experiments: await Promise.all((await written(workdir)).map(async path =>
      ({ pid: null, command: null, seconds: null, log: relative(workdir, path), ...await tail(path) }))),
  };
}

/**
 * The project's logs written in the last RECENT_MS, newest first. Read level by level, so a large data folder
 * cannot hide a log above it, and WALK_ENTRIES entries in all. Hidden folders, node_modules, wandb's own
 * records, links and a LaTeX build's log beside its .tex are passed over.
 */
export async function written(workdir: string): Promise<string[]> {
  const found: { path: string; at: number }[] = [];
  let left = WALK_ENTRIES;
  let level = [workdir];
  for (let depth = 1; depth <= WALK_DEPTH && left > 0; depth++) {
    const next: string[] = [];
    for (const dir of level) {
      const names = new Set<string>();
      try {
        for await (const entry of await opendir(dir)) {
          if (--left < 0) break;
          if (entry.name.startsWith('.') || entry.name === 'node_modules' || entry.name === 'wandb') continue;
          if (entry.isDirectory()) next.push(join(dir, entry.name));
          else if (entry.isFile()) names.add(entry.name);
        }
      } catch { /* a folder it may not read is passed over */ }
      for (const name of names) {
        if (!LOG.test(name) || names.has(name.replace(/\.\w+$/, '.tex'))) continue;
        const at = await stat(join(dir, name)).then(info => info.mtimeMs, () => 0);
        if (Date.now() - at < RECENT_MS) found.push({ path: join(dir, name), at });
      }
    }
    level = next;
  }
  return found.sort((a, b) => b.at - a.at).slice(0, LOGS_SHOWN).map(entry => entry.path);
}

/**
 * The commands running in the project's folder that write to a file there or hold a GPU open: the
 * experiments, not the agents (their output is a pipe). Each is shown once, as the first of its process
 * tree, with its command and log. Null where there is no /proc.
 */
async function commands(workdir: string) {
  let names: string[];
  try { names = await readdir('/proc'); } catch { return null; }
  const uptime = Number((await readFile('/proc/uptime', 'utf8')).split(' ')[0]);
  const inside = (path: string) => path === workdir || path.startsWith(workdir.endsWith(sep) ? workdir : workdir + sep);
  const found = new Map((await Promise.all(names.filter(name => /^\d+$/.test(name)).map(async name => {
    try {
      if (!inside(await readlink(`/proc/${name}/cwd`))) return null;
      const [cmdline, statText, fds] = await Promise.all([readFile(`/proc/${name}/cmdline`, 'utf8'), readFile(`/proc/${name}/stat`, 'utf8'), readdir(`/proc/${name}/fd`)]);
      const targets = (await Promise.all(fds.map(fd => readlink(`/proc/${name}/fd/${fd}`).then(target => [fd, target] as const, () => null))))
        .filter(entry => entry !== null);
      // Which GPU it computes on cannot be told from here: CUDA opens every card it may use.
      const gpu = targets.some(([, target]) => /^\/dev\/nvidia\d+$/.test(target));
      // tqdm draws on stderr and logging opens its own file, so the log is the newest written of these.
      const logs = await Promise.all(targets
        .filter(([fd, target]) => inside(target) && !target.endsWith(' (deleted)') && (fd === '1' || fd === '2' || LOG.test(basename(target))))
        .map(async ([fd, path]) => ({ fd, path, at: await stat(`/proc/${name}/fd/${fd}`).then(info => info.isFile() ? info.mtimeMs : -1, () => -1) })));
      const log = logs.filter(entry => entry.at >= 0).sort((a, b) => b.at - a.at)[0] ?? null;
      if (!gpu && !log) return null;
      // Fields after the command's name, which may hold spaces: state is the first, ppid the second, start time the twentieth.
      const fields = statText.slice(statText.lastIndexOf(')') + 2).split(' ');
      const ticks = Number(fields[19]) / 100;
      return { pid: Number(name), ppid: Number(fields[1]), command: cmdline.split('\0').filter(Boolean).join(' '),
        seconds: Math.max(0, Math.round(uptime - ticks)), log: log && { path: log.path, fd: log.fd } } satisfies Running;
    } catch { return null; }
  }))).filter(entry => entry !== null).map(entry => [entry.pid, entry]));
  const trees = new Map<Running, Running[]>();
  for (const entry of found.values()) {
    let root = entry;
    while (found.has(root.ppid)) root = found.get(root.ppid)!;
    trees.set(root, [...trees.get(root) ?? [], entry]);
  }
  return Promise.all([...trees].sort(([a], [b]) => a.seconds - b.seconds).map(async ([root, members]) => {
    const holder = root.log ? root : members.find(entry => entry.log) ?? root;
    return { pid: root.pid, command: root.command, seconds: root.seconds, log: holder.log && relative(workdir, holder.log.path),
      ...await tail(holder.log && `/proc/${holder.pid}/fd/${holder.log.fd}`) };
  }));
}

/** A log's last lines as a terminal would show them, and how long since it was written. A process's log is
 * read through its descriptor: the file it writes, wherever its name now leads. Windows PowerShell writes UTF-16. */
export async function tail(path: string | null) {
  const none = { log_age_seconds: null, tail: [] as string[] };
  if (!path) return none;
  const handle = await open(path, 'r').catch(() => null);
  if (!handle) return none;
  try {
    const { size, mtimeMs } = await handle.stat();
    const length = Math.min(size, TAIL_BYTES);
    const buffer = Buffer.alloc(length);
    await handle.read(buffer, 0, length, size - length);
    const head = Buffer.alloc(2);
    await handle.read(head, 0, 2, 0);
    let text = buffer.toString(head[0] === 0xff && head[1] === 0xfe ? 'utf16le' : 'utf8').replace(/^\uFEFF/, '');
    // What precedes the first line end or redraw read is cut off.
    if (length < size) text = text.slice(text.search(/[\r\n]/) + 1);
    // A progress bar redraws its line after each carriage return: the line is what it drew last.
    const lines = text.split(/\r?\n/).map(row => row.split('\r').filter(part => part.trim()).at(-1)?.trimEnd() ?? '').filter(Boolean);
    return { log_age_seconds: Math.max(0, Math.round((Date.now() - mtimeMs) / 1000)), tail: lines.slice(-TAIL_LINES) };
  } catch { return none; } finally { await handle.close(); }
}

async function storage(workdir: string) {
  try {
    const { bavail, blocks, bsize } = await statfs(workdir);
    const available_bytes = bavail * bsize;
    return { workdir, total_bytes: blocks * bsize, available_bytes, low_space: available_bytes < 5 * GIB, detail: '' };
  } catch (error) {
    return { workdir, total_bytes: null, available_bytes: null, low_space: false, detail: (error as Error).message };
  }
}

function cpu() {
  const [total, free] = [totalmem() / GIB, freemem() / GIB];
  return {
    kind: 'cpu', status: 'available', device_count: availableParallelism(), devices: [],
    load_average: loadavg()[0]!, used_memory_gib: total - free, total_memory_gib: total,
  };
}

async function gpus() {
  let stdout: string;
  try {
    ({ stdout } = await run('nvidia-smi', ['--query-gpu=index,name,memory.total,memory.used,utilization.gpu', '--format=csv,noheader,nounits'],
      { timeout: 10_000 }));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    return [{ kind: 'cuda', status: 'inaccessible', device_count: 0, devices: [], detail: `nvidia-smi failed: ${(error as Error).message}` }];
  }
  const devices = stdout.split('\n').filter(line => line.trim()).map(line => {
    const [index = '', name = '', total, used, load] = line.split(',').map(part => part.trim());
    // A field the driver cannot report reads `[N/A]`: 0.
    return { index, name, total_memory_mib: Number(total) || 0, used_memory_mib: Number(used) || 0, utilization_percent: Number(load) || 0 };
  });
  return devices.length ? [{ kind: 'cuda', status: 'available', device_count: devices.length, devices, detail: '' }] : [];
}
