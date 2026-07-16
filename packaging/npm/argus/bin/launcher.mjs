import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import process from 'node:process';

const require = createRequire(import.meta.url);

const packages = new Map([
  ['linux-x64', '@argusevolve/argus-linux-x64'],
  ['win32-x64', '@argusevolve/argus-win32-x64'],
]);

export function run(mode) {
  const target = `${process.platform}-${process.arch}`;
  const packageName = packages.get(target);
  if (!packageName) {
    console.error(`Argus beta does not support ${target}.`);
    process.exitCode = 1;
    return;
  }

  let binary = process.env.ARGUS_BINARY_PATH;
  if (!binary) {
    try {
      const packageRoot = dirname(require.resolve(`${packageName}/package.json`));
      binary = join(
        packageRoot,
        'bin',
        process.platform === 'win32' ? 'argus-core.exe' : 'argus-core',
      );
    } catch {
      console.error(
        `Argus platform package ${packageName} is missing. ` +
        'Reinstall @argusevolve/argus with optional dependencies enabled.',
      );
      process.exitCode = 1;
      return;
    }
  }

  const child = spawn(binary, process.argv.slice(2), {
    stdio: 'inherit',
    env: {
      ...process.env,
      ARGUS_BINARY_DISTRIBUTION: '1',
      ARGUS_BINARY_MODE: mode,
      ARGUS_SKILL_AUTOCOMMIT_SKILLS: '0',
    },
  });
  child.on('error', (error) => {
    console.error(`Unable to start Argus: ${error.message}`);
    process.exitCode = 1;
  });
  child.on('exit', (code) => {
    process.exitCode = code ?? 1;
  });
}
