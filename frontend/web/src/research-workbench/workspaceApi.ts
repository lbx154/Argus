import { authHeaders } from '../api';
import { ensureResponseOk } from '../../../core/src/http';

export interface WorkspaceProfile {
  id: string;
  label: string;
  path: string;
  source: 'project' | 'system' | 'configured';
  project_sid: string;
  canonical: boolean;
}

export interface WorkspaceProfileIndex {
  profiles: WorkspaceProfile[];
  default_id: string;
}

export interface WorkspaceEntry {
  path: string;
  name: string;
  type: 'file' | 'directory' | 'symlink';
  size: number;
  mtime: number;
  extension: string;
  skipped?: boolean;
}

export interface WorkspaceTree {
  root: string;
  entries: WorkspaceEntry[];
  truncated: boolean;
}

export interface WorkspaceFile {
  root: string;
  workspace_id: string;
  path: string;
  content: string;
  size: number;
  mtime: number;
  extension: string;
}

export interface WorkspaceRemote { name: string; fetch: string; push: string }
export interface WorkspaceGit {
  available: boolean;
  branch: string;
  status: string;
  stat: string;
  diff: string;
  log: string;
  remotes: WorkspaceRemote[];
  upstream: string;
  ahead: number;
  behind: number;
  identity: { name: string; email: string; valid: boolean };
  truncated?: boolean;
}

async function get<T>(path: string, params: Record<string, string>, signal?: AbortSignal): Promise<T> {
  const query = new URLSearchParams(params);
  const response = await fetch(`${path}?${query}`, { headers: authHeaders(), signal, cache: 'no-store' });
  await ensureResponseOk(response, 'GET', path);
  return response.json() as Promise<T>;
}

const context = (sid: string, workspaceId: string) => ({ sid, workspace_id: workspaceId });

export const workspaceApi = {
  profiles: (sid: string, signal?: AbortSignal) =>
    get<WorkspaceProfileIndex>('/api/v2/workspaces', { sid }, signal),
  tree: (sid: string, workspaceId: string, signal?: AbortSignal) =>
    get<WorkspaceTree>('/api/v2/workspace/tree', context(sid, workspaceId), signal),
  file: (sid: string, workspaceId: string, path: string, signal?: AbortSignal) =>
    get<WorkspaceFile>('/api/v2/workspace/file', { ...context(sid, workspaceId), path }, signal),
  git: (sid: string, workspaceId: string, signal?: AbortSignal) =>
    get<WorkspaceGit>('/api/v2/workspace/git', context(sid, workspaceId), signal),
  rawUrl: (sid: string, workspaceId: string, path: string) =>
    `/api/v2/workspace/raw?${new URLSearchParams({ ...context(sid, workspaceId), path })}`,
  /** Any regular workspace file as a download (credential-like names are refused). */
  downloadBlob: async (sid: string, workspaceId: string, path: string, signal?: AbortSignal) => {
    const response = await fetch(`/api/v2/workspace/download?${new URLSearchParams({ ...context(sid, workspaceId), path })}`, { headers: authHeaders(), signal, cache: 'no-store' });
    await ensureResponseOk(response, 'GET', '/api/v2/workspace/download');
    return response.blob();
  },
  rawBlob: async (sid: string, workspaceId: string, path: string, signal?: AbortSignal) => {
    const response = await fetch(`/api/v2/workspace/raw?${new URLSearchParams({ ...context(sid, workspaceId), path })}`, { headers: authHeaders(), signal, cache: 'no-store' });
    await ensureResponseOk(response, 'GET', '/api/v2/workspace/raw');
    return response.blob();
  },
};

export interface FileTreeNode extends WorkspaceEntry { children: FileTreeNode[] }

/** Internal state (dot-directories such as `.argus`) and skipped heavy folders stay out of the default view. */
export function isInternalEntry(entry: Pick<WorkspaceEntry, 'path'> & { skipped?: boolean }): boolean {
  return Boolean(entry.skipped) || entry.path.split('/').some((part) => part.startsWith('.'));
}

export function visibleEntries(entries: WorkspaceEntry[], showInternal: boolean): WorkspaceEntry[] {
  return showInternal ? entries : entries.filter((entry) => !isInternalEntry(entry));
}

export function searchEntries(entries: WorkspaceEntry[], query: string, limit = 200): WorkspaceEntry[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  return entries.filter((entry) => entry.type === 'file' && entry.path.toLowerCase().includes(needle)).slice(0, limit);
}
export function buildFileTree(entries: WorkspaceEntry[]): FileTreeNode[] {
  const nodes = new Map<string, FileTreeNode>();
  entries.forEach((entry) => nodes.set(entry.path, { ...entry, children: [] }));
  const roots: FileTreeNode[] = [];
  nodes.forEach((node) => {
    const separator = node.path.lastIndexOf('/');
    const parentPath = separator >= 0 ? node.path.slice(0, separator) : '';
    const parent = parentPath ? nodes.get(parentPath) : null;
    if (parent) parent.children.push(node); else roots.push(node);
  });
  const sort = (items: FileTreeNode[]) => {
    items.sort((left, right) => left.type !== right.type ? left.type === 'directory' ? -1 : 1 : left.name.localeCompare(right.name));
    items.forEach((item) => sort(item.children));
  };
  sort(roots);
  return roots;
}
