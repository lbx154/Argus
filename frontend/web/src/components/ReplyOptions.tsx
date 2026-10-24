import { createContext, useContext } from 'react';

/** Sends a chosen answer as the person's next message; null where the person cannot reply. */
export const ReplyChoiceContext = createContext<((text: string) => void) | null>(null);

const HEADING = /^\s*(?:可选（回复编号即可）[：:]?|可选[：:]|Options \(reply with a number\):?)\s*$/;
const NUMBERED = /^\s*(\d{1,2})[.、)）]\s*(.+?)\s*$/;

/** A question's numbered options, split off the text so they can be buttons. */
export function splitReplyOptions(text: string): { body: string; options: string[] } {
  const lines = String(text || '').split('\n');
  const start = lines.findIndex((line) => HEADING.test(line));
  if (start < 0) return { body: text, options: [] };
  const options: string[] = [];
  let end = start + 1;
  for (; end < lines.length; end += 1) {
    const line = lines[end];
    const match = NUMBERED.exec(line);
    if (match) options.push(match[2].replace(/^\*\*(.+)\*\*$/, '$1'));
    else if (line.trim() && options.length) break;
  }
  if (!options.length) return { body: text, options: [] };
  const body = [...lines.slice(0, start), ...lines.slice(end)].join('\n').replace(/\n{3,}/g, '\n\n').trim();
  return { body, options };
}

/** A paragraph that packs several questions into one line, as a list a person can read. */
export function readableQuestions(text: string): string {
  return String(text || '').split('\n').map((line) => {
    if (/^\s*([-*]|\d+[.、])\s/.test(line) || line.length < 30) return line;
    const parts = line.split(/(?<=[？?])\s*/).map((part) => part.trim()).filter(Boolean);
    const questions = parts.filter((part) => /[？?]$/.test(part));
    if (questions.length < 3) return line;
    return '\n' + parts.map((part, index) => `${index + 1}. ${part}`).join('\n') + '\n';
  }).join('\n');
}

export function ReplyOptionButtons({ options, zh }: { options: string[]; zh: boolean }) {
  const send = useContext(ReplyChoiceContext);
  if (!options.length) return null;
  if (!send) return <ol className="mt-2 list-decimal pl-5 text-sm text-ink-dim">{options.map((option) => <li key={option}>{option}</li>)}</ol>;
  return (
    <div className="mt-2" data-testid="reply-options">
      <div className="flex flex-wrap gap-2">
        {options.map((option, index) => <button key={`${index}:${option}`} type="button" onClick={() => send(option)}
          className="rounded-md border border-blue/45 px-3 py-1.5 text-left text-sm text-ink hover:border-blue hover:bg-blue/10">
          {option}
        </button>)}
      </div>
      <p className="mt-1 text-xs text-ink-faint">{zh ? '点一个选项即可回复，也可以在下方直接写你的回答。' : 'Click an option to reply, or type your own answer below.'}</p>
    </div>
  );
}
