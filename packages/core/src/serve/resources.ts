// The machine the work runs on, read when the page asks: its CPUs and memory, and the NVIDIA GPUs
// nvidia-smi lists, none where it is not installed. Argus limits none of them.

import { execFile } from 'node:child_process';
import { availableParallelism, freemem, loadavg, totalmem } from 'node:os';
import { promisify } from 'node:util';
import type { Handler } from './index.js';

const run = promisify(execFile);
const GIB = 1 << 30;

export function resources(): [string, RegExp, Handler][] {
  return [['GET', /^\/api\/system\/resources$/, async () => ({
    schema_version: 1, enforcement: 'advisory', accelerators: [cpu(), ...await gpus()],
  })]];
}

function cpu() {
  const [total, free] = [totalmem() / GIB, freemem() / GIB];
  return {
    kind: 'cpu', status: 'available', device_count: availableParallelism(), devices: [],
    detail: `load ${loadavg()[0]!.toFixed(2)} · memory ${(total - free).toFixed(1)} of ${total.toFixed(1)} GiB in use`,
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
