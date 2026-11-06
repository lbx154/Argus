import { symlink } from 'node:fs/promises';
import { resolve } from 'node:path';
import type { TestContext } from 'node:test';

/** A real directory link without requiring Windows developer mode or administrator rights. */
export const directoryLink = (target: string, path: string): Promise<void> =>
  symlink(resolve(target), path, process.platform === 'win32' ? 'junction' : 'dir');

/** Call only inside a file-link-only subtest: never skip its parent's ordinary I/O assertions. */
export async function fileLink(t: TestContext, target: string, path: string): Promise<boolean> {
  try { await symlink(target, path, 'file'); return true; }
  catch (error) {
    if (process.platform !== 'win32' || (error as NodeJS.ErrnoException).code !== 'EPERM') throw error;
    t.skip('Windows denied file symlink creation (EPERM); directory junction boundary coverage still runs');
    return false;
  }
}
