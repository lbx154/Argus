import { describe, expect, it } from 'vitest';
import { buildFileTree, searchEntries } from '../research-workbench/workspaceApi';

describe('workspace explorer', () => {
  const entries = [
    { path: 'results', type: 'directory' as const },
    { path: 'results/table.csv', type: 'file' as const },
    { path: '.argus', type: 'directory' as const },
    { path: '.argus/log.json', type: 'file' as const },
    { path: 'a.md', type: 'file' as const },
    { path: 'results/z.csv', type: 'file' as const },
    { path: 'results/a.csv', type: 'file' as const },
  ];
  it('builds and searches the server tree using paths alone', () => {
    const nodes = buildFileTree(entries);
    expect(nodes.map(node => node.path)).toEqual(['.argus', 'results', 'a.md']);
    expect(nodes[1].children.map(node => node.path)).toEqual(['results/a.csv', 'results/table.csv', 'results/z.csv']);
    expect(searchEntries(entries, 'LOG').map(entry => entry.path)).toEqual(['.argus/log.json']);
    expect(searchEntries(entries, '  ')).toEqual([]);
  });
});
