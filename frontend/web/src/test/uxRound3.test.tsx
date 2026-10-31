import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { addComposerFiles, attachmentMime } from '../lib/attachments';
import { Sidebar } from '../components/Sidebar';
import { api } from '../api';


const file = (name: string, type = '') => ({ name, type, size: 10, lastModified: 1 }) as File;

describe('attachments a researcher actually has', () => {
  it('accepts every suffix and preserves the browser MIME or uses octet-stream', () => {
    for (const suffix of ['.zip', '.xlsx', '.ipynb', '.py', '.tex', '.jsonl', '.npy', '.pt', '.parquet', '.out']) {
      const chosen = file('data' + suffix);
      expect(addComposerFiles([], [chosen]).accepted).toEqual([chosen]);
      expect(attachmentMime(chosen)).toBe('application/octet-stream');
    }
    expect(attachmentMime(file('train.ts', 'video/mp2t'))).toBe('video/mp2t');
  });
});

describe('sidebar skill entry', () => {
  it('opens everything the library holds instead of the raw learning feed', async () => {
    vi.stubGlobal('localStorage', { getItem: (key: string) => key === 'argus.sidebar.advanced.v1' ? 'open' : null, setItem: vi.fn(), removeItem: vi.fn() });
    vi.stubGlobal('window', new EventTarget());
    vi.spyOn(api, 'organization').mockResolvedValue({ value: {}, revision: 0 });
    const onOpenLearning = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let view: ReturnType<typeof create>;
    try {
      await act(async () => {
        view = create(<QueryClientProvider client={client}><Sidebar projects={[]} activeId="s-A" localCwd="" onSelect={vi.fn()} onManage={vi.fn()}
          onOpenPanel={vi.fn()} onOpenLearning={onOpenLearning} onNew={vi.fn()} loading={false} onToggleCollapse={vi.fn()}
          themeMode="light" onCycleTheme={vi.fn()} /></QueryClientProvider>);
      });
      const entry = view!.root.findAll((node) => node.type === 'button' && node.props['aria-label'] === 'Skill library')[0];
      act(() => entry.props.onClick());
      expect(onOpenLearning).toHaveBeenCalledWith('feed');
    } finally {
      act(() => view!.unmount());
      client.clear();
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    }
  });
});
