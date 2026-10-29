import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import replay from '../../../../packages/core/test/fixtures/conversation-fixes/after.json';
import { webEvents, type Recorded } from '../../../../packages/core/src/serve/views';
import { EventStream } from './serverSnapshot';
import { ResearchCanvas } from '../components/ResearchCanvas';
import { deliveryHeadingKey } from '../lib/deliveryKind';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ArtifactInfo, DeliveryReceipt } from '../../../core/src/types';
import { artifactPathFromHref, MarkdownContent } from '../components/MarkdownContent';
import { pdfContainScale, PdfPreview } from '../components/PdfPreview';
import {
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
  it('keeps an unregistered local Markdown link in the authenticated preview flow', () => {
    const markup = renderToStaticMarkup(createElement(MarkdownContent, {
      children: '[notes](RESEARCH_NOTES.md) [external](https://example.com/notes.md)',
      artifacts: [], onOpenArtifact: () => undefined,
    }));
    expect(markup).toContain('<button');
    expect(markup).not.toContain('href="RESEARCH_NOTES.md"');
    expect(markup).toContain('href="https://example.com/notes.md"');
  });
  it('names an accepted residual risk instead of a plain pass', async () => {
    const risk = 'Live feed ingestion: live behaviour is unverified. (accepted by the operator)';
    const { renderLine } = await import('../../../core/src/eventRender');
    const context = { locale: 'en', density: 'full', showReasoning: false, unknownEventPolicy: 'hide' } as const;
    const review = renderLine({ type: 'round.review.completed', status: 'done', reason: 'Read the fixture test.', residual_risk: risk }, context);
    expect(review?.text).toContain(`verified with residual risk: ${risk}`);
    expect(review?.text).not.toContain('was satisfied');
    const completed = renderLine({
      type: 'life.mission.completed', status: 'done', success: true, outcome_class: 'completed',
      title: 'Ingest the feed', summary: `Verified with residual risk: ${risk}. Built ingest.`, residual_risk: risk,
      review_source: 'reviewer',
    }, context);
    // The risk itself is told once, on the review row.
    expect(completed?.text).toBe('completed with residual risk');
    expect(completed?.role).toBe('reviewer');
    // A task the Reviewer accepted is closed in the Reviewer's words, under its name.
    const accepted = renderLine({
      type: 'life.mission.completed', status: 'done', success: true, outcome_class: 'completed',
      review_source: 'reviewer', summary: 'I checked every listed function against the code.',
    }, context);
    expect(accepted?.role).toBe('reviewer');
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

  it('a reply recorded with a delivery replaces the page’s row for it, delivery and all', () => {
    const recorded = { type: 'ui.argus', text: 'Done', ts: 2, event_id: 5, message_id: 'm3-argus', request_id: 'r1', delivery };
    const events = mergeConversationEvents([recorded], [{ type: 'ui.argus', text: 'Done', ts: 1.5, message_id: 'm3-argus', request_id: 'r1' }]);
    expect(events).toEqual([recorded]);
  });
});

it('resolves document-relative model links and confined sandbox links', () => {
  const files = [
    { path: 'study/models/model.npz', storage_path: '/workspace/study/models/model.npz' },
    { path: 'model.npz', storage_path: '/workspace/model.npz' },
  ];
  expect(artifactPathFromHref('models/model.npz', files, 'study/README.md')).toBe('study/models/model.npz');
  expect(artifactPathFromHref('../model.npz', files, 'study/README.md')).toBe('model.npz');
  expect(artifactPathFromHref('../../model.npz', files, 'study/README.md')).toBeNull();
  expect(artifactPathFromHref('sandbox:/workspace/study/models/model.npz', files)).toBe('study/models/model.npz');
  const markup = renderToStaticMarkup(createElement(MarkdownContent, {
    artifacts: files, basePath: 'study/README.md', onOpenArtifact: () => undefined,
    children: '[model](models/model.npz) [sandbox](sandbox:/workspace/study/models/model.npz) [unsafe](javascript:alert%281%29)',
  }));
  expect(markup.match(/data-artifact-path="study\/models\/model.npz"/g)).toHaveLength(2);
  expect(markup).not.toContain('href="javascript:');
});

it('browser record: a Manager answer with a file is a neutral result in chat and Results & files', () => {
  const answer = webEvents(replay.events as Recorded[]).filter(row => row.type === 'ui.argus').at(-1)!;
  const receipt = answer.delivery as DeliveryReceipt;
  expect(receipt.kind).toBe('result');
  expect(deliveryHeadingKey(receipt)).toBe('mission.result');
  const chat = renderToStaticMarkup(createElement(EventStream, { events: [answer], connected: true, showReasoning: false }));
  const files = renderToStaticMarkup(createElement(QueryClientProvider, { client: new QueryClient() },
    createElement(ResearchCanvas, { sid: null, artifacts: [], filesFirst: true, deliveriesLoaded: true, latestDelivery: receipt, onExpand: () => undefined })));
  expect(chat).toContain('data-delivery-kind="result"');
  expect(chat).toContain('Result');
  expect(files).toContain('Latest result');
  expect(files).toContain('Result');
  expect(chat + files).not.toContain('Task completed');
});

it('replays per-file delivery occurrences, including unchanged text, in conversation cards and selectors', async () => {
  const { default: fixture } = await import('../../../../packages/core/test/fixtures/delivery-versions/record.json');
  const { deliveryOptionLabel, deliveryFileUpdated } = await import('../components/deliveryPresentation');
  const events = webEvents(fixture.text.events as Recorded[]);
  const receipts = events.flatMap(row => row.delivery ? [row.delivery as DeliveryReceipt] : []);
  const markup = renderToStaticMarkup(createElement(EventStream, { events, connected: true, showReasoning: false }));
  expect(markup).toContain('Delivery 1');
  expect(markup).toContain('Delivery 2');
  expect(markup).toContain('Delivery 3');
  expect(receipts[2].targets.map(file => file.delivery_number)).toEqual([3, 2]);
  expect(receipts[1].snapshots?.[0].version).toBe(receipts[2].snapshots?.[0].version);
  expect(receipts.map(receipt => deliveryOptionLabel(receipt, true))).toEqual([
    '版本报告 · 第 1 次交付', '版本报告 · report.md 第 2 次交付 · data.csv 第 1 次交付', '版本报告 · report.md 第 3 次交付 · data.csv 第 2 次交付',
  ]);
  expect(deliveryFileUpdated(receipts[0], receipts, 'report.md')).toBe(false);
});

it('marks a superseded binary on its old card', async () => {
  const { default: fixture } = await import('../../../../packages/core/test/fixtures/delivery-versions/record.json');
  const { deliveryFileUpdated } = await import('../components/deliveryPresentation');
  const events = webEvents(fixture.binary.events as Recorded[]);
  const receipts = events.flatMap(row => row.delivery ? [row.delivery as DeliveryReceipt] : []);
  expect(deliveryFileUpdated(receipts[0], receipts, 'data.bin')).toBe(true);
  expect(deliveryFileUpdated(receipts[1], receipts, 'data.bin')).toBe(false);
  // Arrival order and equal timestamps do not replace the per-file record order.
  expect(deliveryFileUpdated({ ...receipts[0], delivered_at: receipts[1].delivered_at }, [...receipts].reverse(), 'data.bin')).toBe(true);
  const markup = renderToStaticMarkup(createElement(EventStream, { events, connected: true, showReasoning: false }));
  expect(markup.match(/Updated by a later delivery/g)).toHaveLength(1);
  const unchanged = { ...receipts[1], snapshots: receipts[0].snapshots };
  expect(deliveryFileUpdated(receipts[0], [receipts[0], unchanged], 'data.bin')).toBe(false);
});
