/** Metadata returned by the Argus WebAPI. */
export interface ApiRuntimeIdentity {
  /** Explicit application route owner; absent on the native Argus workbench. */
  application_routes?: string;
  /** Canonical primary data directory; absent on older servers, null before authentication. */
  state_root?: string | null;
  package_version: string;
  source_root: string;
  pid: number;
  executable: string;
  started_at: string;
}

export interface ApiMeta {
  service: string;
  protocol: {
    name: string;
    major: number;
    minor: number;
  };
  snapshot_schema_version: number;
  capabilities: string[];
  runtime: ApiRuntimeIdentity;
  /** Optional for compatibility with pre-handshake-auth servers. */
  authentication?: {
    required: boolean;
    authenticated: boolean;
  };
}
