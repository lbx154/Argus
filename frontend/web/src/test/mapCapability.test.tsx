import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { Capabilities, MAP_CAPABILITY } from '../lib/capabilities';
import { useCanvas } from '../map/canvas';
import type { Dataset } from '../map/model';
import { useMapCopy } from '../map/useMapCopy';

afterEach(() => { vi.restoreAllMocks(); });

const live = { id: 'live:p-1', kind: 'live', title: 'p-1', description: '', tasks: [], events: [] } as unknown as Dataset;

let ready: boolean | undefined;
function Probe() {
  useCanvas('p-1', true);
  ({ ready } = useMapCopy(live, null, false, undefined, 'p-1'));
  return null;
}

it('reads the map\'s own records only from a server that keeps them, and waits for none from one that keeps none', async () => {
  for (const [declared, asked] of [[['manager.sse.v1'], 0], [['manager.sse.v1', MAP_CAPABILITY], 1]] as const) {
    const canvas = vi.spyOn(api, 'mapCanvas').mockReturnValue(new Promise(() => {}));
    const copy = vi.spyOn(api, 'mapCopy').mockReturnValue(new Promise(() => {}));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let renderer: ReturnType<typeof create> | undefined;
    await act(async () => {
      renderer = create(<QueryClientProvider client={client}><Capabilities.Provider value={declared}><Probe /></Capabilities.Provider></QueryClientProvider>);
    });
    expect([canvas.mock.calls.length, copy.mock.calls.length]).toEqual([asked, asked]);
    // The opening fit waits for explanations still being read, never for ones no one will send.
    expect(ready).toBe(!asked);
    act(() => renderer!.unmount());
    client.clear();
    vi.restoreAllMocks();
  }
});
