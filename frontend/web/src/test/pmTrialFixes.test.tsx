import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MarkdownContent } from '../components/MarkdownContent';
import { ChatBox } from '../components/ChatBox';

describe('pm trial fixes', () => {
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
