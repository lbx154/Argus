// The operator's environment registrations: names and literal values in one private file.
import { closeSync, fchmodSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { argusHome } from './store.js';

export function credentials(home = argusHome()): Record<string, string> {
  try { return JSON.parse(readFileSync(join(home, 'credentials.json'), 'utf8')) as Record<string, string>; }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {};
    // JSON parser errors can quote the input, so never expose them through a settings response.
    throw new Error('Cannot read credential registrations.');
  }
}

/** No temporary copy or history: this file alone holds the registered values. */
export function saveCredentials(home: string, values: Record<string, string>): void {
  const fd = openSync(join(home, 'credentials.json'), 'w', 0o600);
  try {
    fchmodSync(fd, 0o600);
    writeFileSync(fd, JSON.stringify(values));
  } finally { closeSync(fd); }
}
