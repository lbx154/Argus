#!/usr/bin/env node
// What this machine offers a run: its GPUs and their free memory, the Python ML stack, memory and
// disk where the work is. Run it with node from the workspace.
import { execFileSync } from 'node:child_process';

const run = (command, args) => {
  try { return execFileSync(command, args, { encoding: 'utf8', timeout: 30_000, stdio: ['ignore', 'pipe', 'ignore'] }).trim(); } catch { return null; }
};

// CUDA numbers only the GPUs it can use, skipping a failed one: a GPU is known by its UUID.
console.log('GPUs (index, uuid, name, memory used, memory total, utilization):');
console.log(run('nvidia-smi', ['--query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu', '--format=csv,noheader']) ?? 'none found (no nvidia-smi)');
console.log(`CUDA_VISIBLE_DEVICES=${process.env.CUDA_VISIBLE_DEVICES ?? '(unset: every GPU)'}\n`);

const python = ['python3', 'python'].find(name => run(name, ['--version']));
const packages = ['torch', 'transformers', 'vllm', 'sglang', 'trl', 'verl', 'openrlhf', 'peft', 'deepspeed', 'flash_attn', 'datasets', 'accelerate'];
console.log(python ? run(python, ['-c', `
import importlib.metadata as m, sys
print('Python', sys.version.split()[0])
for name in ${JSON.stringify(packages)}:
    try: print(f'  {name} {m.version(name.replace("_", "-"))}')
    except m.PackageNotFoundError: print(f'  {name}: not installed')
`]) : 'No Python found.');

console.log(`\n${run('free', ['-g']) ?? ''}\n\n${run('df', ['-h', process.cwd()]) ?? ''}`);
