import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { TurnSteps } from '../components/TurnSteps';
import { ChatBox } from '../components/ChatBox';
import { EventStream } from '../components/EventStream';
import { MapConversation } from '../map/MapConversation';
import { mergeOptimisticManagerDelta, mergeOptimisticManagerSteps, settleOptimisticManagerTurn } from '../lib/conversationEvents';
import { renderLine } from '../../../core/src/eventRender';

const step = { kind: 'tool_use', label: 'Read report.md', status: 'completed', started_ts: 1, ended_ts: 2 };

describe('one compact work-progress area', () => {
  it('starts collapsed and stays active while the reply streams after tools finish', () => {
    const html = renderToStaticMarkup(<TurnSteps steps={[step]} live startedTs={Date.now() / 1000} />);
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('Preparing a reply');
    expect(html).not.toContain('<ol');
    expect(html).not.toContain('Done');
  });

  it('collapses on completion after the user expanded real steps', () => {
    let view: ReactTestRenderer;
    act(() => { view = create(<TurnSteps steps={[step]} live />); });
    try {
      act(() => view!.root.findByType('button').props.onClick());
      expect(view!.root.findByType('button').props['aria-expanded']).toBe(true);
      expect(view!.root.findAllByType('li')).toHaveLength(1);
      act(() => view!.update(<TurnSteps steps={[step]} live={false} />));
      expect(view!.root.findByType('button').props['aria-expanded']).toBe(false);
      expect(view!.root.findAllByType('li')).toHaveLength(0);
    } finally { act(() => view!.unmount()); }
  });

  it('does not present failure or cancellation as successful work', () => {
    expect(renderToStaticMarkup(<TurnSteps steps={[{ ...step, status: 'failed' }]} live={false} />)).toContain('An issue occurred');
    for (const status of ['stopped', 'cancelled', 'canceled']) {
      expect(renderToStaticMarkup(<TurnSteps steps={[{ ...step, status }]} live={false} />)).toContain('Stopped');
    }
  });

  it('does not call a turn failed when work went on after a failed step', () => {
    const html = renderToStaticMarkup(<TurnSteps steps={[{ ...step, status: 'failed' }, { ...step, status: 'completed' }]} live={false} />);
    expect(html).not.toContain('An issue occurred');
    expect(html).toContain('1 retried or worked around');
    expect(renderToStaticMarkup(<TurnSteps steps={[{ ...step, status: 'completed' }]} live={false} status="failed" />)).toContain('An issue occurred');
  });

  it('keeps waiting, streaming and cancellation in the same request row', () => {
    const waiting = mergeOptimisticManagerSteps([], 's-A', 1, [], 1000);
    expect(waiting).toHaveLength(1);
    expect(renderLine(waiting[0], { locale: 'en', showReasoning: false, unknownEventPolicy: 'hide', density: 'compact' })).not.toBeNull();
    const streaming = mergeOptimisticManagerDelta(waiting, 's-A', 1, 'Answer', 'web-1-argus', 2000);
    expect(streaming).toHaveLength(1);
    expect(streaming[0]).toMatchObject({ text: 'Answer', live: true });
    expect(settleOptimisticManagerTurn(streaming, 1, 3000)[0]).toMatchObject({ live: false, status: 'stopped' });
  });

  it('renders a single waiting area before tools or text, including the map conversation', () => {
    const events = mergeOptimisticManagerSteps([{ type: 'ui.operator', text: 'Question', ts: 1 }], 's-A', 1, [], 2000);
    const html = renderToStaticMarkup(<EventStream events={events} connected showReasoning={false} />);
    expect(html.match(/class="turn-steps /g)).toHaveLength(1);
    expect(html).toContain('Preparing a reply');
    const map = renderToStaticMarkup(<MapConversation events={events} connected pending artifacts={[]} zh={false}
      onClose={() => undefined} onOpenArtifact={() => undefined} onOpenDelivery={() => undefined} />);
    expect(map.match(/class="turn-steps /g)).toHaveLength(1);
    expect(map).not.toContain('map-conversation-pending');
  });

  it('does not repeat steps above the input and retains the stop control', () => {
    const html = renderToStaticMarkup(<ChatBox value="" onChange={() => undefined} onSend={() => true}
      onCancel={() => undefined} pending disabled={false} attachments={[]} onAttachmentsChange={() => undefined}
      completionSelection={0} onCompletionSelectionChange={() => undefined} />);
    expect(html).not.toContain('turn-steps');
    expect(html).not.toContain('<ol');
    expect(html).toContain('aria-label="Stop this reply"');
  });
});
