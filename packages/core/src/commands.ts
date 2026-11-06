// Native command lookup and shell-free npm launch. No cmd.exe re-parsing of agent arguments.
import { accessSync, constants, readFileSync, statSync } from 'node:fs';
import { delimiter, dirname, extname, isAbsolute, join, resolve, sep } from 'node:path';

/** Windows environment names are case-insensitive, including on plain objects passed to spawn.
 * Later layers win; Node otherwise silently takes the first alphabetically sorted spelling. */
export function mergeEnv(...layers: NodeJS.ProcessEnv[]): Record<string, string> {
  const entries = new Map<string, [string, string]>();
  for (const layer of layers) for (const [name, value] of Object.entries(layer)) {
    const key = process.platform === 'win32' ? name.toUpperCase() : name;
    if (value === undefined) entries.delete(key);
    else entries.set(key, [name, value]);
  }
  // fromEntries creates data properties, including a literal __proto__ registration.
  return Object.fromEntries(entries.values());
}

/** Read the effective spelling without mutating an environment supplied by the operator. */
export function envValue(env: NodeJS.ProcessEnv, key: string): string | undefined {
  const normal = (name: string): string => process.platform === 'win32' ? name.toUpperCase() : name;
  return Object.entries(env).reverse().find(([name]) => normal(name) === normal(key))?.[1];
}

/** Explicit paths resolve from the child's cwd; bare commands come from its PATH, not the cwd. */
export function findExecutable(name: string, env: NodeJS.ProcessEnv = process.env, cwd = process.cwd()): string | null {
  const windows = process.platform === 'win32';
  const explicit = isAbsolute(name) || name.includes(sep) || (windows && name.includes('/'));
  const extensions = windows && !extname(name) ? [...explicit ? [''] : [], ...(envValue(env, 'PATHEXT') ?? '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean)] : [''];
  const bases = explicit ? [windows ? resolve(cwd, name) : name] : (envValue(env, 'PATH') ?? '').split(delimiter).filter(Boolean)
    .map(dir => join(windows ? resolve(cwd, dir.replace(/^"(.*)"$/, '$1')) : dir, name));
  for (const base of bases) for (const extension of extensions) {
    const candidate = base + extension;
    try { accessSync(candidate, constants.X_OK); if (statSync(candidate).isFile()) return candidate; } catch { /* not this one */ }
  }
  return null;
}

/** npm's cmd/bat shim names a script or executable relative to itself. Do not execute batch
 * language or infer its other effects; unrecognised shims fail with a readable explanation. */
export function launcher(name: string, env: NodeJS.ProcessEnv = process.env, cwd = process.cwd()): string[] {
  const found = findExecutable(name, process.platform === 'win32' ? env : process.env, cwd);
  if (process.platform !== 'win32') return [found ?? name];
  // Do not hand a missing bare name back to Windows spawn: it would search cwd as well.
  if (!found) throw Object.assign(new Error(`Cannot launch ${name}: not found on PATH or at the given path.`), { code: 'ENOENT' });
  let file = found;
  let prefix: string[] = [];
  if (/\.(cmd|bat)$/i.test(file)) {
    const source = readFileSync(file, 'utf8');
    // dp0 already ends with a separator. Some Node wrappers add only a literal CLI name before
    // %*: accept those bare words, never batch expansion, quoting or shell operators.
    // Only a forwarding invocation, optionally npm's standard endLocal preamble.
    // Merely echoing/assigning a quoted dp0 string is not an executable shim.
    const target = /^[ \t]*@?(?:endLocal[ \t]+&[ \t]+goto[ \t]+#_undefined_#[ \t]+2>NUL[ \t]+\|\|[ \t]+title[ \t]+%COMSPEC%[ \t]+&[ \t]+(?:set[ \t]+PATHEXT=%PATHEXT:;\.JS;=;%[ \t]+&[ \t]+)?)?(?:(?:"(?:%_prog%|(?:[^"\r\n]*[\\/])?node(?:\.exe)?)"|node(?:\.exe)?)[ \t]+)?"%(?:~dp0|dp0%)[\\/]*([^"\r\n]+)"((?:[ \t]+[A-Za-z0-9_-]+)*)[ \t]+%\*[ \t]*\r?$/im.exec(source);
    if (!target) throw new Error(`Cannot launch ${found} without a shell: expected an npm/Node CLI shim. Use a native executable or Node script.`);
    file = resolve(dirname(file), target[1]!);
    prefix = target[2]!.trim().split(/\s+/).filter(Boolean);
  }
  if (/\.(exe|com)$/i.test(file)) return [file, ...prefix];
  if (/\.(m?js|cjs)$/i.test(file) || /^#![^\r\n]*\bnode(?:\s|$)/.test(readFileSync(file, 'utf8').slice(0, 256))) return [process.execPath, file, ...prefix];
  return [file, ...prefix];
}
