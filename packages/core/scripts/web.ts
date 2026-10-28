// The web cockpit the package carries (web/), built from frontend/web's source for this release.
// frontend/web/dist is not used: it keeps every earlier release's files for open tabs.

import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = fileURLToPath(new URL('../../../frontend/web', import.meta.url));
execFileSync(process.execPath, [join(frontend, 'node_modules/vite/bin/vite.js'), 'build',
  '--outDir', fileURLToPath(new URL('../web', import.meta.url)), '--emptyOutDir'], { cwd: frontend, stdio: 'inherit' });
