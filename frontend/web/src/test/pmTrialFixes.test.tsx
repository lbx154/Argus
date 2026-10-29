import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { TurnSteps } from '../components/TurnSteps';
import { mergeOptimisticManagerDelta } from '../lib/conversationEvents';
import { isDraftReply } from '../../../core/src/events';
import { MarkdownContent } from '../components/MarkdownContent';
import { ChatBox } from '../components/ChatBox';

describe('pm trial fixes', () => {
  it('stops spinning "preparing a reply" once the reply text is on screen', () => {
    const html = renderToStaticMarkup(<TurnSteps steps={[]} live startedTs={Date.now() / 1000} replied />);
    expect(html).not.toMatch(/Preparing a reply|正在准备回复/);
    expect(html).toMatch(/Wrapping up|收尾中/);
    expect(html).not.toContain('animate-spin');
  });

  it('keeps the turn working between steps while only an interim reply is on screen', () => {
    // A quick file read finished; the model is now thinking about its next
    // step after streaming "我先看一下你的数据。", which is not the final reply.
    // The sending tab holds it as a local row; another tab, device or a reload
    // sees the Manager's draft row in the activity stream. Both are drafts.
    const now = Date.now() / 1000;
    const steps = [{ call_id: 'c1', kind: 'command_execution', label: '读取项目文件', status: 'completed', started_ts: now - 13, ended_ts: now - 12 }];
    const local = mergeOptimisticManagerDelta([], 's1', 1, '我先看一下你的数据。', 'm1', now * 1000, 'snapshot', true)[0];
    const streamed = { type: 'ui.argus', text: '我先看一下你的数据。', message_id: 'm1', in_progress: true, live: true, ts: now - 15 };
    for (const turn of [local, streamed]) {
      const html = renderToStaticMarkup(<TurnSteps steps={steps as never} live startedTs={now - 15}
        replied={Boolean(turn.text) && !isDraftReply(turn)} />);
      expect(html).not.toMatch(/Wrapping up|收尾中/);
      expect(html).toContain('animate-spin');
      expect(html).toMatch(/15s/);
    }
    const final = mergeOptimisticManagerDelta([local], 's1', 1, '图已经画好。', 'm1', now * 1000, 'snapshot')[0];
    expect(isDraftReply(final)).toBe(false);
  });

  it('folds a run block under "run it yourself"', () => {
    const html = renderToStaticMarkup(<MarkdownContent>{'Drop your CSV here.\n\n```run\npython tool.py data.csv\n```'}</MarkdownContent>);
    expect(html).toContain('<details');
    expect(html).toMatch(/Run it yourself|自己运行/);
    const plain = renderToStaticMarkup(<MarkdownContent>{'```bash\nls\n```'}</MarkdownContent>);
    expect(plain).not.toContain('<details');
  });

  it('names the project a message goes to in the composer', () => {
    const html = renderToStaticMarkup(<ChatBox value="" onChange={() => undefined} onSend={() => true} onCancel={() => undefined}
      disabled={false} pending={false} attachments={[]} onAttachmentsChange={() => undefined} slashSelection={0}
      onSlashSelectionChange={() => undefined} projectName="按产品对比月度销售走势" />);
    expect(html).toContain('按产品对比月度销售走势');
  });
});
