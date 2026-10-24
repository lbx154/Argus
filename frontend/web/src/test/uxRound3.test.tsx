import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { addComposerFiles, attachmentMime, isSupportedAttachment, MESSAGE_ATTACHMENT_ACCEPT } from '../lib/attachments';
import { Sidebar } from '../components/Sidebar';
import { api } from '../api';

// The plugin list fetches on mount; it is not under test here.
vi.mock('../components/PluginLauncher', () => ({ PluginLauncher: () => null }));

const file = (name: string, type = '') => ({ name, type, size: 10, lastModified: 1 }) as File;

describe('attachments a researcher actually has', () => {
  it('accepts archives, Office files, notebooks, code and LaTeX with the server-expected MIME', () => {
    for (const name of ['data.zip', 'run.tgz', 'table.xlsx', 'draft.docx', 'slides.pptx', 'nb.ipynb', 'train.py', 'paper.tex', 'refs.bib', 'cfg.yaml']) {
      expect(isSupportedAttachment(file(name))).toBe(true);
      expect(MESSAGE_ATTACHMENT_ACCEPT.split(',')).toContain(name.slice(name.lastIndexOf('.')));
    }
    expect(attachmentMime(file('train.ts', 'video/mp2t'))).toBe('text/plain');
    expect(attachmentMime(file('nb.ipynb'))).toBe('application/x-ipynb+json');
    expect(isSupportedAttachment(file('tool.exe'))).toBe(false);
    const { accepted, issues } = addComposerFiles([], [file('data.zip'), file('tool.exe')]);
    expect(accepted.map((row) => row.name)).toEqual(['data.zip']);
    expect(issues).toEqual([{ code: 'unsupported', fileName: 'tool.exe' }]);
  });
});

describe('sidebar knowledge entry', () => {
  it('opens the current project’s knowledge instead of the raw learning feed', async () => {
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
      const entry = view!.root.findAll((node) => node.type === 'button' && node.props['aria-label'] === 'Knowledge & skills')[0];
      act(() => entry.props.onClick());
      expect(onOpenLearning).toHaveBeenCalledWith('project');
    } finally {
      act(() => view!.unmount());
      client.clear();
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    }
  });
});
