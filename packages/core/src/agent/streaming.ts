// Live display only. Nothing here accepts a hand-off, settles usage, or executes tools.
import { isJsonObject } from '@argus/contracts';
import type { AgentRequest } from '../types.js';
import { writing } from './steps.js';

export class MessageStream {
  private blocks = new Map<number, { text: string; form: boolean }>();
  constructor(private readonly emit: AgentRequest['onWords']) {}
  clear(): void { this.blocks.clear(); this.emit?.(''); }
  text(index: number, value: string, replace = false, form = false): void {
    if (!Number.isSafeInteger(index) || index < 0 || index > 127) return;
    const previous = this.blocks.get(index);
    this.blocks.set(index, { text: (replace ? value : (previous?.text ?? '') + value).slice(0, 65_536), form: previous?.form ?? form });
    const blocks = [...this.blocks.entries()].sort(([a], [b]) => a - b).map(([, row]) => row);
    const body = blocks.map(row => writing(row.text.replace(/^```(?:json)?\s*\n/, ''))).join('\n').slice(0, 65_536);
    const answer = blocks.some(row => row.form || row.text.trimStart().startsWith('{'));
    this.emit?.(body, answer ? 'answer' : 'process');
  }
  /** Pi JSON/RPC wire events are delta-only; do not rely on SDK .partial snapshots. */
  pi(event: Record<string, unknown>): void {
    if (event.type === 'message_start' && isJsonObject(event.message) && event.message.role === 'assistant') this.clear();
    if (event.type !== 'message_update' || !isJsonObject(event.assistantMessageEvent)) return;
    const e = event.assistantMessageEvent, index = Number(e.contentIndex);
    if (e.type === 'text_start') this.text(index, '', true);
    if (e.type === 'text_delta' && typeof e.delta === 'string') this.text(index, e.delta);
    if (e.type === 'text_end' && typeof e.content === 'string') this.text(index, e.content, true);
    if (e.type === 'toolcall_start' && e.toolName === 'hand_off') this.text(index, '', true, true);
    if (e.type === 'toolcall_delta' && this.blocks.get(index)?.form && typeof e.delta === 'string') this.text(index, e.delta);
  }
  /** Called only after the Claude adapter has matched this invocation's UUID. */
  claude(event: Record<string, unknown>): void {
    if (event.parent_tool_use_id || event.type !== 'stream_event' || !isJsonObject(event.event)) return;
    const e = event.event, index = Number(e.index);
    if (e.type === 'message_start') this.clear();
    const block = isJsonObject(e.content_block) ? e.content_block : {};
    if (e.type === 'content_block_start' && block.type === 'text') this.text(index, typeof block.text === 'string' ? block.text : '', true);
    if (e.type === 'content_block_delta' && isJsonObject(e.delta) && e.delta.type === 'text_delta' && typeof e.delta.text === 'string') this.text(index, e.delta.text);
  }
}
