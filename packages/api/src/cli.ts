import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import { PythonQueryBackend } from './backend.js';
import { startReadApi } from './server.js';

const { values } = parseArgs({ options: {
  python: { type: 'string' }, 'source-root': { type: 'string' }, 'global-root': { type: 'string' },
  port: { type: 'string', default: '0' }, host: { type: 'string', default: '127.0.0.1' },
  help: { type: 'boolean', short: 'h' },
} });

async function main(): Promise<void> {
  if (values.help) {
    process.stdout.write('Usage: npm start -w @argus/api -- --python PATH --source-root DIR --global-root DIR [--port 0] [--host 127.0.0.1|::1]\nAuthentication: set ARGUS_READ_API_TOKEN to a bearer token of at least 16 characters.\n');
    return;
  }
  if (!values.python || !values['source-root'] || !values['global-root']) throw new Error('--python, --source-root and --global-root are required');
  if (values.host !== '127.0.0.1' && values.host !== '::1') throw new Error('--host must be a loopback address');
  if (!/^\d+$/.test(values.port ?? '')) throw new Error('--port must be an integer');
  const controller = new AbortController();
  const stop = (): void => controller.abort();
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  const backend = new PythonQueryBackend({
    executable: values.python, sourceRoot: resolve(values['source-root']), globalRoot: resolve(values['global-root']),
  });
  const service = await startReadApi({ backend, token: process.env.ARGUS_READ_API_TOKEN ?? '', host: values.host, port: Number(values.port), signal: controller.signal });
  process.stdout.write(JSON.stringify({ url: service.url, read_only: true }) + '\n');
}
main().catch(error => { process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`); process.exitCode = 1; });
