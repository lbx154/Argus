import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { ArtifactInfo, DeliveryReceipt } from '../../../core/src/types';
import {
  LIVE_PROGRESS_PATH,
  defaultPreviewPath,
  selectPreviewArtifacts,
} from '../components/ResearchCanvas';
import { artifactPathFromHref, MarkdownContent } from '../components/MarkdownContent';
import { pdfContainScale, PdfPreview } from '../components/PdfPreview';
import {
  completionNotificationPayload,
  deliveryNotificationPayload,
} from '../lib/desktopBridge';
import { latestConversationDelivery } from '../components/EventStream';
import { mergeConversationEvents } from '../lib/conversationEvents';

const delivery: DeliveryReceipt = {
  schema_version: 1,
  delivery_id: 'delivery:item-1:task_completed',
  kind: 'task_completed',
  item_id: 'item-1',
  title: 'Create final report',
  summary: 'Reviewed report is ready.',
  status: 'done',
  review_status: 'done',
  delivered_at: 1,
  primary_target: {
    path: 'out/final.md',
    label: 'final.md',
    source: 'reviewer_evidence',
    why: 'Reviewer accepted the file.',
  },
  targets: [{
    path: 'out/final.md',
    label: 'final.md',
    source: 'reviewer_evidence',
    why: 'Reviewer accepted the file.',
  }],
};

const artifact = (path: string, source: ArtifactInfo['source']): ArtifactInfo => ({
  path,
  name: path.split('/').at(-1) || path,
  why: 'test',
  exists: true,
  kind: 'markdown',
  mime: 'text/markdown',
  size: 1,
  mtime: 1,
  source,
  storage_path: `C:\\Users\\operator\\workspace\\${path.replaceAll('/', '\\')}`,
});

describe('completed delivery presentation', () => {
  it('opens the receipt primary target before a stale live checkpoint', () => {
    const view = emptyMissionView();
    view.delivery = delivery;
    const artifacts = [
      artifact('.argus/live/status.md', 'manager_live'),
      artifact('out/final.md', 'delivery'),
    ];

    expect(defaultPreviewPath(view, artifacts)).toBe('out/final.md');
    expect(selectPreviewArtifacts(artifacts).map((item) => item.path)).toEqual([
      'out/final.md',
      '.argus/live/status.md',
    ]);
  });

  it('keeps live progress selected until work completes', () => {
    const view = emptyMissionView();
    view.mission.status = 'working';
    const files = [artifact('out/final.md', 'delivery')];

    expect(defaultPreviewPath(view, files)).toBe(LIVE_PROGRESS_PATH);

    view.mission.status = 'complete';
    expect(defaultPreviewPath(view, files)).toBe('out/final.md');
  });

  it('resolves relative and absolute completion links to an allowlisted file', () => {
    const file = artifact('out/final.md', 'delivery');

    expect(artifactPathFromHref('out/final.md', [file])).toBe('out/final.md');
    expect(artifactPathFromHref('/C:/Users/operator/workspace/out/final.md', [file])).toBe('out/final.md');
    expect(artifactPathFromHref('https://example.com/final.md', [file])).toBeNull();

    const markup = renderToStaticMarkup(createElement(
      MarkdownContent,
      {
        artifacts: [file],
        onOpenArtifact: () => undefined,
        children: '[final](/C:/Users/operator/workspace/out/final.md)',
      },
    ));
    expect(markup).toContain('data-artifact-path="out/final.md"');
    expect(markup).toContain('title="C:\\Users\\operator\\workspace\\out\\final.md"');
  });

  it('fits portrait pages by height and landscape pages proportionally', () => {
    expect(pdfContainScale(600, 900, 1600, 1000)).toBeCloseTo(968 / 900);
    expect(pdfContainScale(1200, 700, 1600, 1000)).toBeCloseTo(1568 / 1200);
  });

  it('uses a plugin-free PDF.js canvas in the sandboxed preview', () => {
    const markup = renderToStaticMarkup(createElement(PdfPreview, {
      src: 'blob:pdf-test',
      name: 'final.pdf',
    }));

    expect(markup).toContain('<canvas');
    expect(markup).not.toContain('由 PDF.js 安全渲染');
    expect(markup).not.toContain('browser PDF plugin');
    expect(markup).not.toContain('<embed');
    expect(markup).not.toContain('<object');
  });

  it('builds a concise native completion toast even without a receipt', () => {
    expect(completionNotificationPayload({
      completionId: 'completion:s:item-1',
      title: '**Create report**',
      summary: 'Ready: [final.pdf](/C:/workspace/final.pdf)',
      path: 'final.pdf',
    })).toEqual({
      deliveryId: 'completion:s:item-1',
      title: 'Create report',
      summary: 'Ready: final.pdf',
      path: 'final.pdf',
    });
  });

  it('keeps a delivery notification display-only and bounded', () => {
    expect(deliveryNotificationPayload(delivery)).toEqual({
      deliveryId: delivery.delivery_id,
      title: delivery.title,
      summary: delivery.summary,
      path: 'out/final.md',
    });
  });

  it('keeps the latest Solo delivery until the next operator turn', () => {
    expect(latestConversationDelivery([
      { type: 'ui.operator', text: 'Create a file', ts: 1 },
      { type: 'ui.argus', text: 'Done', ts: 2, delivery },
    ])).toEqual(delivery);
    expect(latestConversationDelivery([
      { type: 'ui.argus', text: 'Done', ts: 2, delivery },
      { type: 'ui.operator', text: 'New question', ts: 3 },
    ])).toBeNull();
    expect(latestConversationDelivery([])).toBeUndefined();
  });

  it('keeps delivery metadata when an optimistic reply is confirmed', () => {
    const events = mergeConversationEvents(
      [],
      [{
        role: 'argus',
        text: 'Done',
        ts: 2,
        message_id: 'turn-1-argus',
        delivery_id: delivery.delivery_id,
        delivery,
      }],
      [{
        type: 'ui.argus',
        text: 'Done',
        ts: 1.5,
        message_id: 'turn-1-argus',
      }],
    );

    expect(events).toHaveLength(1);
    expect(events[0].delivery).toEqual(delivery);
  });
});
