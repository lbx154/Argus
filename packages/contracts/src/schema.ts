/** The subset used by Argus' versioned event envelope validator. */
export interface PayloadSchema {
  version?: number;
  type?: string | string[];
  required?: string[];
  properties?: Record<string, PayloadSchema>;
  items?: PayloadSchema;
  const?: unknown;
  enum?: unknown[];
  minimum?: number;
  minLength?: number;
  maxLength?: number;
  description?: string;
  additionalProperties?: boolean;
}
