/** Wire envelope: legacy replay can omit fields that new producers require. */
export interface EventMsg {
  type?: string;
  ts?: number;
  event_schema_version?: number;
  canonical_type?: string;
  event_validation?: { status: 'invalid'; errors: string[] };
  [key: string]: unknown;
}
