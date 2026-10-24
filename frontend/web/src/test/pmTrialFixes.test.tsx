import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { TurnSteps } from '../components/TurnSteps';
import { MarkdownContent } from '../components/MarkdownContent';
import { ChatBox } from '../components/ChatBox';
import { projectOverview } from '../map/overview';
import type { MapTask } from '../map/model';

describe('pm trial fixes', () => {
  it('stops spinning "preparing a reply" once the reply text is on screen', () => {
    const html = renderToStaticMarkup(<TurnSteps steps={[]} live startedTs={Date.now() / 1000} replyShown />);
    expect(html).not.toMatch(/Preparing a reply|正在准备回复/);
    expect(html).toMatch(/Wrapping up|收尾中/);
    expect(html).not.toContain('animate-spin');
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

  it('uses the request as the goal when only the Manager did the work', () => {
    const turn = { id: 'turn:1', kind: 'turn', title: '帮我做一个小工具：按月汇总销售额，画一张折线图【开工设置：不限】', ts: 1, status: 'done' } as unknown as MapTask;
    const overview = projectOverview({ tasks: [turn], events: [], receipts: [], decisions: [], zh: true });
    expect(overview.goal).toBe('帮我做一个小工具：按月汇总销售额，画一张折线图');
  });
});
