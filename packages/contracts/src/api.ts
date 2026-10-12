/** Metadata returned by the current Python WebAPI; protocol-compatible during migration. */
export interface ApiRuntimeIdentity {
  /** Explicit application route owner; absent on the native Argus workbench. */
  application_routes?: string;
  /** Canonical primary data directory; absent on older servers, null before authentication. */
  state_root?: string | null;
  package_version: string;
  source_root: string;
  configured_source_root: string | null;
  source_root_matches_config: boolean | null;
  revision: string | null;
  pid: number;
  python_version: string;
  executable: string;
  started_at: string;
  release_id: string;
  manifest_source_digest: string | null;
  runtime_source_digest: string | null;
  release_matches_source: boolean | null;
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
