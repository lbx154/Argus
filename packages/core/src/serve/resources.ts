// The machine the work runs on, read when the page asks: its CPUs and memory, and the NVIDIA GPUs
// nvidia-smi lists, none where it is not installed. Argus limits none of them.
// Also what runs in a project's folder: read from the machine, so watching an experiment costs no model call.

import { execFile } from 'node:child_process';
import { open, readdir, readFile, readlink, statfs } from 'node:fs/promises';
import { availableParallelism, freemem, loadavg, totalmem } from 'node:os';
import { relative, sep } from 'node:path';
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
    if (!id) return { experiments: [] };
    return { experiments: await experiments(project(id).workdir) };
  }]];
}

/** The longest lines and how much of a log's end is read for them. */
const TAIL_LINES = 12;
const TAIL_BYTES = 64 << 10;

interface Running {
  pid: number; ppid: number; command: string; seconds: number;
  /** The GPUs it holds open, by device minor. */
  gpus: number[];
  /** Where its output goes when that is a file in the project. */
  log: string | null;
}

/**
 * The commands running in the project's folder whose output goes to a file there or that hold a GPU:
 * the experiments, not the agents (their output is a pipe). Each is shown once, as the first of its
 * process tree: its command and log, and the GPUs any of its processes hold. Linux only; elsewhere none.
 */
export async function experiments(workdir: string) {
  let names: string[];
  try { names = await readdir('/proc'); } catch { return []; }
  const [uptime, minors] = await Promise.all([readFile('/proc/uptime', 'utf8').then(text => Number(text.split(' ')[0])), gpuMinors()]);
  const inside = (path: string) => path === workdir || path.startsWith(workdir.endsWith(sep) ? workdir : workdir + sep);
  const found = new Map((await Promise.all(names.filter(name => /^\d+$/.test(name)).map(async name => {
    try {
      if (!inside(await readlink(`/proc/${name}/cwd`))) return null;
      const [cmdline, statText, fds] = await Promise.all([readFile(`/proc/${name}/cmdline`, 'utf8'), readFile(`/proc/${name}/stat`, 'utf8'), readdir(`/proc/${name}/fd`)]);
      const targets = await Promise.all(fds.map(fd => readlink(`/proc/${name}/fd/${fd}`).then(target => [fd, target] as const, () => null)));
      const gpus = targets.flatMap(entry => /^\/dev\/nvidia(\d+)$/.exec(entry?.[1] ?? '')?.[1] ?? []).map(Number);
      const out = targets.find(entry => entry?.[0] === '1')?.[1];
      const log = out && inside(out) && !out.endsWith(' (deleted)') ? out : null;
      if (!gpus.length && !log) return null;
      // Fields after the command's name, which may hold spaces: state is the first, ppid the second, start time the twentieth.
      const fields = statText.slice(statText.lastIndexOf(')') + 2).split(' ');
      const ticks = Number(fields[19]) / 100;
      return { pid: Number(name), ppid: Number(fields[1]), command: cmdline.split('\0').filter(Boolean).join(' '), seconds: Math.max(0, Math.round(uptime - ticks)), gpus, log } satisfies Running;
    } catch { return null; }
  }))).filter(entry => entry !== null).map(entry => [entry.pid, entry]));
  const trees = new Map<Running, Running[]>();
  for (const entry of found.values()) {
    let root = entry;
    while (found.has(root.ppid)) root = found.get(root.ppid)!;
    trees.set(root, [...trees.get(root) ?? [], entry]);
  }
  return Promise.all([...trees].sort(([a], [b]) => a.seconds - b.seconds).map(async ([root, members]) => {
    const log = root.log ?? members.find(entry => entry.log)?.log ?? null;
    const holder = members.find(entry => entry.log === log)!;
    const gpus = [...new Set(members.flatMap(entry => entry.gpus))].map(minor => minors.get(minor) ?? String(minor)).sort((a, b) => Number(a) - Number(b));
    return { pid: root.pid, command: root.command, seconds: root.seconds, gpus, log: log && relative(workdir, log), ...await tail(log && `/proc/${holder.pid}/fd/1`) };
  }));
}

/** A log's last lines as a terminal would show them, and how long since it was written.
 * Read through the process's own descriptor: the file it writes, wherever its name now leads. */
async function tail(path: string | null) {
  const none = { log_age_seconds: null, tail: [] as string[] };
  if (!path) return none;
  const handle = await open(path, 'r').catch(() => null);
  if (!handle) return none;
  try {
    const { size, mtimeMs } = await handle.stat();
    const length = Math.min(size, TAIL_BYTES);
    const buffer = Buffer.alloc(length);
    await handle.read(buffer, 0, length, size - length);
    let text = buffer.toString('utf8');
    // What precedes the first line end or redraw read is cut off.
    if (length < size) text = text.slice(text.search(/[\r\n]/) + 1);
    // A progress bar redraws its line after each carriage return: the line is what it drew last.
    const lines = text.split(/\r?\n/).map(row => row.split('\r').filter(part => part.trim()).at(-1)?.trimEnd() ?? '').filter(Boolean);
    return { log_age_seconds: Math.max(0, Math.round((Date.now() - mtimeMs) / 1000)), tail: lines.slice(-TAIL_LINES) };
  } catch { return none; } finally { await handle.close(); }
}

/** nvidia-smi's index of each GPU by its device minor; none known, the minor is shown. */
async function gpuMinors(): Promise<Map<number, string>> {
  const minors = new Map<number, string>();
  try {
    const { stdout } = await run('nvidia-smi', ['--query-gpu=index,pci.bus_id', '--format=csv,noheader'], { timeout: 10_000 });
    await Promise.all(stdout.split('\n').filter(line => line.trim()).map(async line => {
      const [index = '', bus = ''] = line.split(',').map(part => part.trim());
      const text = await readFile(`/proc/driver/nvidia/gpus/${bus.replace(/^0000(?=[0-9a-f]{4}:)/i, '').toLowerCase()}/information`, 'utf8').catch(() => '');
      const minor = /Device Minor:\s*(\d+)/.exec(text)?.[1];
      if (minor !== undefined) minors.set(Number(minor), index);
    }));
  } catch { /* no nvidia-smi: minors stand for themselves */ }
  return minors;
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
