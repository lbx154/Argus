import { QueryClientProvider } from '@tanstack/react-query';
import { roleClient, roleSnapshot } from './serverSnapshot';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import type { EventMsg } from '../../../core/src/types';
import { SessionIdContext } from '../components/EventStream';
import { MapConversation } from '../map/MapConversation';

// The map's conversation and its images read the shown project; images come
// from the project the app is showing, not from the web app's own address.
it('shows inline images and delivery thumbnails of the shown project in the map conversation', () => {
  const delivery = { schema_version: 1, delivery_id: 'd-chart', kind: 'task_completed', item_id: 't', title: 'Sales chart', summary: '', status: 'done',
    review_status: 'done', delivered_at: 2, primary_target: { path: 'sales_tool/output/chart.png', label: 'chart' }, targets: [] };
  const events = [
    { type: 'ui.operator', text: '画个图', ts: 1, message_id: 'm1' },
    { type: 'ui.argus', text: '![销量](sales_tool/output/x.png)', ts: 2, message_id: 'm2', delivery },
  ] as unknown as EventMsg[];
  const html = renderToStaticMarkup(<SessionIdContext.Provider value="s-map">
    <QueryClientProvider client={roleClient}><MapConversation snapshot={roleSnapshot([], { work_status: undefined })} events={events} connected pending={false} zh artifacts={[{ path: 'sales_tool/output/x.png', exists: true } as never]}
      onClose={() => {}} onOpenArtifact={() => {}} onOpenDelivery={() => {}} /></QueryClientProvider>
  </SessionIdContext.Provider>);
  expect(html).toContain('data-delivery-thumbnail="sales_tool/output/chart.png"');
  // Fetched through the project's files, never a bare relative <img>.
  expect(html).not.toContain('src="sales_tool/output/x.png"');
});
