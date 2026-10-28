import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { ChatBox } from '../components/ChatBox';
import { KeybindingHelp } from '../components/KeybindingHelp';
import { Sidebar } from '../components/Sidebar';
import { Capabilities, COMMANDS_CAPABILITY, SETTINGS_CAPABILITY } from '../lib/capabilities';

// As a server that declares only what every server has, as the TypeScript core does, and as
// one that declares the entry's capability, as the Python server does.
function both(element: ReactElement, capability: string): [string, string] {
  const render = (declared: string[]) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    try {
      return renderToStaticMarkup(<QueryClientProvider client={client}><Capabilities.Provider value={declared}>{element}</Capabilities.Provider></QueryClientProvider>);
    } finally { client.clear(); }
  };
  return [render(['manager.sse.v1']), render(['manager.sse.v1', capability])];
}

describe('entries the server does not declare are hidden', () => {
  it('the settings button', () => {
    const [without, withIt] = both(<Sidebar projects={[]} activeId="s-A" localCwd="/synthetic" onSelect={vi.fn()} onManage={vi.fn()} onOpenPanel={vi.fn()}
      onNew={vi.fn()} loading={false} onToggleCollapse={vi.fn()} themeMode="light" onCycleTheme={vi.fn()} />, SETTINGS_CAPABILITY);
    expect(without).not.toContain('aria-label="Open settings"');
    expect(withIt).toContain('aria-label="Open settings"');
  });

  it('slash command suggestions, and the command reference in help', () => {
    const [plain, commands] = both(<ChatBox attachments={[]} onAttachmentsChange={() => {}} value="/" onChange={() => undefined}
      onSend={() => false} onCancel={() => undefined} disabled={false} pending={false} slashSelection={0} onSlashSelectionChange={() => undefined} />, COMMANDS_CAPABILITY);
    expect(plain).not.toContain('role="listbox"');
    expect(commands).toContain('role="listbox"');
    const [help, reference] = both(<KeybindingHelp open onClose={() => undefined} />, COMMANDS_CAPABILITY);
    expect(help).toContain('⌘K / Ctrl+K');
    expect(help).not.toContain('/status');
    expect(reference).toContain('/status');
  });
});
