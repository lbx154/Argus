import { statSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isJsonObject } from '@argus/contracts';
import type { PiRunRequest } from './pi.js';
import { SCHEMA_ENV } from './piOutputSchemaExtension.js';

export const PI_OUTPUT_SCHEMA_EXTENSION = fileURLToPath(new URL('../dist/piOutputSchemaExtension.js', import.meta.url));

/** Reject values JSON.stringify would silently drop or change in a schema. */
export function encodeOutputSchema(schema: unknown): string {
  if (!isJsonObject(schema)) throw new Error('outputSchema must be a JSON Schema object');
  const ancestors = new Set<object>();
  let nodes = 0;
  const visit = (value: unknown, depth: number): void => {
    if (depth > 64) throw new Error('outputSchema exceeds its nesting limit');
    if (++nodes > 65_536) throw new Error('outputSchema exceeds its value limit');
    if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
    if (typeof value === 'number' && Number.isFinite(value)) return;
    if (typeof value !== 'object' || ancestors.has(value)) throw new Error('outputSchema must contain finite, acyclic JSON values');
    if (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) {
      throw new Error('outputSchema must contain plain JSON objects');
    }
    if (Object.getOwnPropertySymbols(value).some(key => Object.prototype.propertyIsEnumerable.call(value, key))) {
      throw new Error('outputSchema cannot contain symbol keys');
    }
    ancestors.add(value);
    for (const child of Array.isArray(value) ? value : Object.values(value)) visit(child, depth + 1);
    ancestors.delete(value);
  };
  visit(schema, 0);
  const encoded = JSON.stringify(schema);
  if (Buffer.byteLength(encoded) > 65_536) throw new Error('outputSchema exceeds its 64 KiB limit');
  return encoded;
}

/** A caller cannot change a queued call's schema or plugin bindings in place. */
export function snapshotPiRequest(request: PiRunRequest): PiRunRequest {
  return { ...request,
    ...(request.outputSchema === undefined ? {} : { outputSchema: JSON.parse(encodeOutputSchema(request.outputSchema)) }),
    ...(request.skillPaths === undefined ? {} : { skillPaths: [...request.skillPaths] }),
    ...(request.trustedExtensions === undefined ? {} : { trustedExtensions: [...request.trustedExtensions] }),
    ...(request.trustedToolNames === undefined ? {} : { trustedToolNames: [...request.trustedToolNames] }),
    ...(request.extensionEnv === undefined ? {} : { extensionEnv: { ...request.extensionEnv } }),
  };
}

export function validatePiCapabilities(request: PiRunRequest): void {
  const cap = request.providerTurnCap === undefined ? 0 : request.providerTurnCap;
  if (!Number.isSafeInteger(cap) || cap < 0) throw new Error('providerTurnCap must be a nonnegative safe integer');
  if (request.outputSchema !== undefined) {
    if (request.toolPolicy !== 'disabled') throw new Error('outputSchema requires toolPolicy disabled');
    encodeOutputSchema(request.outputSchema);
    if (!statSync(PI_OUTPUT_SCHEMA_EXTENSION, { throwIfNoEntry: false })?.isFile()) throw new Error('Pi output schema extension is unavailable; build the runtime');
  }
  // Match Python: tool plugins and their environment are omitted from text-only calls.
  if (request.toolPolicy === 'disabled') return;
  for (const path of request.trustedExtensions ?? []) {
    if (!isAbsolute(path) || !statSync(path, { throwIfNoEntry: false })?.isFile()) throw new Error('trustedExtensions must name existing absolute files');
  }
  for (const name of request.trustedToolNames ?? []) {
    if (!request.trustedExtensions?.length || !/^[A-Za-z_][A-Za-z0-9_.:-]*$/.test(name) || ['bash', 'write', 'edit'].includes(name)) {
      throw new Error('trustedToolNames must name explicitly bound plugin tools, without builtin write tools');
    }
  }
  for (const [key, value] of Object.entries(request.extensionEnv ?? {})) {
    if (!/^ARGUS_PLUGIN_[A-Z0-9_]+$/.test(key) || typeof value !== 'string' || value.includes('\0')) {
      throw new Error('extensionEnv accepts only ARGUS_PLUGIN_ string values without NUL');
    }
  }
}

export function piChildEnvironment(request: PiRunRequest, base: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const env = { ...base };
  // Windows environment names are case insensitive; remove every spelling.
  for (const key of Object.keys(env)) if (key.toUpperCase() === SCHEMA_ENV) delete env[key];
  if (request.toolPolicy !== 'disabled') Object.assign(env, request.extensionEnv);
  if (request.outputSchema !== undefined) env[SCHEMA_ENV] = encodeOutputSchema(request.outputSchema);
  return env;
}
