import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import process from 'node:process';

const require = createRequire(import.meta.url);

const packages = new Map([
  ['linux-x64', '@argusbot/cli-linux-x64'],
]);

export function run(mode) {
  const target = `${process.platform}-${process.arch}`;
  const packageName = packages.get(target);
  if (!packageName) {
    console.error(`Argus binary preview does not support ${target}.`);
    process.exitCode = 1;
    return;
  }

  let binary = process.env.ARGUS_BINARY_PATH;
  if (!binary) {
    try {
      binary = require.resolve(packageName);
    } catch {
      console.error(
        `Argus platform package ${packageName} is missing. ` +
        'Reinstall @argusbot/cli with optional dependencies enabled.',
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
