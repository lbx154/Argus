import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { TurnSteps } from '../components/TurnSteps';
import { MarkdownContent } from '../components/MarkdownContent';
import { ChatBox } from '../components/ChatBox';

describe('pm trial fixes', () => {
  it('stops spinning "preparing a reply" once the reply text is on screen', () => {
    const html = renderToStaticMarkup(<TurnSteps steps={[]} live startedTs={Date.now() / 1000} replied />);
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

  it("the composer's placeholder says only whom the message goes to", () => {
    const html = renderToStaticMarkup(<ChatBox value="" onChange={() => undefined} onSend={() => true}
      disabled={false} pending={false} attachments={[]} onAttachmentsChange={() => undefined} completionSelection={0}
      onCompletionSelectionChange={() => undefined} />);
    expect(html).toContain('placeholder="Tell Argus…"');
  });
});
