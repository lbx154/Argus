import { canonicalEventType } from './eventCatalog.js';
import payload from '../schemas/event_payload_schemas.json' with { type: 'json' };
import type { PayloadSchema } from './schema.js';

const EVENT_PAYLOAD_SCHEMAS: Record<string, PayloadSchema> = payload.events;

export function isJsonObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function matches(value: unknown, expected: string): boolean {
  switch (expected) {
    case 'null': return value === null;
    case 'string': case 'boolean': return typeof value === expected;
    case 'integer': return typeof value === 'number' && Number.isInteger(value);
    case 'number': return typeof value === 'number';
    case 'object': return isJsonObject(value);
    case 'array': return Array.isArray(value);
    default: return true;
  }
}

export interface EventValidation {
  valid: boolean;
  known: boolean;
  canonical_type: string;
  errors: string[];
}

/** Port of core.event_catalog.validate_event_envelope, including legacy replay. */
export function validateEventEnvelope(
  value: unknown,
  options: { requireKnown?: boolean; allowMissingFields?: boolean } = {},
): EventValidation {
  const event = isJsonObject(value) ? value : {};
  const raw = typeof event.type === 'string' ? event.type.trim() : '';
  const canonical = canonicalEventType(raw);
  const schema: PayloadSchema | undefined = Object.hasOwn(EVENT_PAYLOAD_SCHEMAS, canonical)
    ? EVENT_PAYLOAD_SCHEMAS[canonical] : undefined;
  const errors: string[] = [];
  if (!raw) errors.push('type is required');
  else if (!/^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$/.test(raw)) errors.push(`invalid event type: ${raw}`);
  if (options.requireKnown && !schema) errors.push(`unknown event type: ${raw}`);
  if (schema) {
    if (!options.allowMissingFields) {
      const missing = (schema.required ?? []).filter(field => !Object.hasOwn(event, field) || event[field] == null);
      if (missing.length) errors.push(`missing required fields: ${missing.join(', ')}`);
    }
    if (event.payload_schema_version != null && event.payload_schema_version !== (schema.version || 1)) {
      errors.push(`payload_schema_version must be ${schema.version || 1}`);
    }
    for (const [field, spec] of Object.entries(schema.properties ?? {})) {
      if (!Object.hasOwn(event, field)) continue;
      const fieldValue = event[field];
      const types = Array.isArray(spec.type) ? spec.type : spec.type ? [spec.type] : [];
      if (types.length && !types.some(type => matches(fieldValue, type))) {
        errors.push(`field ${field} must be ${types.join(' or ')}`);
        continue;
      }
      if (typeof fieldValue === 'number' && !Number.isFinite(fieldValue)) {
        errors.push(`field ${field} must be finite`);
        continue;
      }
      if (Object.hasOwn(spec, 'const') && fieldValue !== spec.const) errors.push(`field ${field} must equal ${JSON.stringify(spec.const)}`);
      if (spec.enum && !spec.enum.includes(fieldValue)) errors.push(`field ${field} must be one of ${JSON.stringify(spec.enum)}`);
      if (typeof fieldValue === 'string' && spec.minLength !== undefined && [...fieldValue].length < spec.minLength) {
        errors.push(`field ${field} must have length >= ${spec.minLength}`);
      }
      if (typeof fieldValue === 'number' && spec.minimum !== undefined && fieldValue < spec.minimum) errors.push(`field ${field} must be >= ${spec.minimum}`);
      const itemType = spec.items?.type;
      if (Array.isArray(fieldValue) && typeof itemType === 'string' && fieldValue.some(item => !matches(item, itemType))) {
        errors.push(`field ${field} items must be ${itemType}`);
      }
    }
  }
  if (event.ts != null) {
    if (typeof event.ts !== 'number') errors.push('ts must be numeric');
    else if (!Number.isFinite(event.ts)) errors.push('ts must be finite');
  }
  const version = event.event_schema_version;
  if (version != null && (typeof version !== 'number' || !Number.isInteger(version) || version < 1)) {
    errors.push('event_schema_version must be a positive integer');
  }
  return { valid: errors.length === 0, known: schema !== undefined, canonical_type: canonical, errors };
}
