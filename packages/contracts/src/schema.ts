/** The JSON Schema subset Argus' contracts use: event payloads and hand-off forms. */
export interface PayloadSchema {
  $ref?: string;
  anyOf?: PayloadSchema[];
  $defs?: Record<string, PayloadSchema>;
  version?: number;
  type?: string | string[];
  required?: string[];
  properties?: Record<string, PayloadSchema>;
  items?: PayloadSchema;
  const?: unknown;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
  pattern?: string;
  description?: string;
  additionalProperties?: boolean;
}

export function isJsonObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function matchesType(value: unknown, expected: string): boolean {
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

/** Every way `value` breaks `schema`, each saying where; [] when it conforms. */
export function schemaErrors(value: unknown, schema: PayloadSchema, at = '', defs = schema.$defs ?? {}): string[] {
  if (schema.$ref) {
    const target = defs[schema.$ref.replace('#/$defs/', '')];
    if (!target) throw new Error(`Unknown schema reference: ${schema.$ref}`);
    const { $ref, ...overrides } = schema;
    return schemaErrors(value, { ...target, ...overrides }, at, defs);
  }
  const where = at || 'value';
  if (schema.anyOf && !schema.anyOf.some(variant => schemaErrors(value, variant, at, defs).length === 0)) {
    return [`${where} must match an allowed schema`];
  }
  const types = Array.isArray(schema.type) ? schema.type : schema.type ? [schema.type] : [];
  if (types.length && !types.some(type => matchesType(value, type))) return [`${where} must be ${types.join(' or ')}`];
  const errors: string[] = [];
  if (Object.hasOwn(schema, 'const') && value !== schema.const) errors.push(`${where} must equal ${JSON.stringify(schema.const)}`);
  if (schema.enum && !schema.enum.includes(value)) errors.push(`${where} must be one of ${JSON.stringify(schema.enum)}`);
  if (typeof value === 'string') {
    const length = [...value].length;
    if (schema.minLength !== undefined && length < schema.minLength) errors.push(`${where} must have length >= ${schema.minLength}`);
    if (schema.maxLength !== undefined && length > schema.maxLength) errors.push(`${where} must have length <= ${schema.maxLength}`);
    if (schema.pattern !== undefined && !new RegExp(schema.pattern).test(value)) errors.push(`${where} must match ${schema.pattern}`);
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) errors.push(`${where} must be finite`);
    if (schema.minimum !== undefined && value < schema.minimum) errors.push(`${where} must be >= ${schema.minimum}`);
    if (schema.maximum !== undefined && value > schema.maximum) errors.push(`${where} must be <= ${schema.maximum}`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) errors.push(`${where} must have ${schema.minItems} or more items`);
    if (schema.maxItems !== undefined && value.length > schema.maxItems) errors.push(`${where} must have ${schema.maxItems} or fewer items`);
    const items = schema.items;
    if (items) value.forEach((item, index) => errors.push(...schemaErrors(item, items, `${where}[${index}]`, defs)));
  }
  if (isJsonObject(value)) {
    const prefix = at ? `${at}.` : '';
    for (const name of schema.required ?? []) if (!Object.hasOwn(value, name)) errors.push(`${prefix}${name} is required`);
    for (const [name, field] of Object.entries(value)) {
      const spec = schema.properties && Object.hasOwn(schema.properties, name) ? schema.properties[name] : undefined;
      if (spec) errors.push(...schemaErrors(field, spec, prefix + name, defs));
      else if (schema.additionalProperties === false) errors.push(`${prefix}${name} is not allowed`);
    }
  }
  return errors;
}
