import { canonicalEventType } from './eventCatalog.js';
import payload from '../schemas/event_payload_schemas.json' with { type: 'json' };
import { isJsonObject, schemaErrors, type PayloadSchema } from './schema.js';

const EVENT_PAYLOAD_SCHEMAS: Record<string, PayloadSchema> = payload.events;

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
      errors.push(...schemaErrors(fieldValue, spec, `field ${field}`, payload.$defs));
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
