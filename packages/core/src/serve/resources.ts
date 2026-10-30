// The machine the work runs on, read when the page asks: its CPUs and memory, and the NVIDIA GPUs
// nvidia-smi lists, none where it is not installed. Argus limits none of them.

import { execFile } from 'node:child_process';
import { statfs } from 'node:fs/promises';
import { availableParallelism, freemem, loadavg, totalmem } from 'node:os';
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
  }]];
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
