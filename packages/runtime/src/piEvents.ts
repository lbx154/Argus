import { isJsonObject, type JsonObject } from '@argus/contracts';

const string = (value: unknown): string => typeof value === 'string' ? value : '';
const object = (value: unknown): JsonObject => isJsonObject(value) ? value : {};

/** Stateful port of _consume_pi_event; deltas are replaced by final messages. */
export class PiEventConsumer {
  threadId: string | null = null;
  agentMessages: string[] = [];
  turnCompleted = false;
  turnFailed = false;
  fatalError: string | null = null;
  providerTurns = 0;
  toolActivityObserved = false;
  private openIndex: number | null = null;

  consume(event: JsonObject): void {
    const type = string(event.type).trim();
    if (type === 'tool_execution_start' || type === 'tool_execution_end' || type === 'tool_execution_update') {
      this.toolActivityObserved = true;
    }
    if (type === 'session') {
      const id = string(event.id).trim();
      if (id) this.threadId = id;
    } else if (type === 'message_end') {
      const message = object(event.message);
      if (string(message.role).trim() !== 'assistant') return;
      this.providerTurns += 1;
      const text = (Array.isArray(message.content) ? message.content : [])
        .filter(isJsonObject).filter(part => part.type === 'text')
        .map(part => string(part.text)).filter(part => part.trim()).join('\n').trim();
      if (this.openIndex !== null) {
        this.agentMessages.splice(this.openIndex, 1);
        this.openIndex = null;
      }
      if (text && this.agentMessages.at(-1) !== text) this.agentMessages.push(text);
      const reason = string(message.stopReason).trim().toLowerCase();
      if (reason === 'error' || reason === 'aborted') this.fail(string(message.errorMessage).trim() || `Pi runner reported ${reason}.`);
    } else if (type === 'message_update') {
      const delta = object(event.assistantMessageEvent);
      if (string(delta.type).trim() === 'text_delta') {
        const text = string(delta.delta);
        if (this.openIndex !== null) this.agentMessages[this.openIndex] += text;
        else if (text.trim()) { this.openIndex = this.agentMessages.length; this.agentMessages.push(text); }
      } else if (string(delta.type).trim() === 'error') {
        this.fail(string(delta.errorMessage || delta.message || delta.reason).trim() || 'Pi runner reported a streaming error.');
      }
    } else if (type === 'auto_retry_end' && event.success === false) {
      this.fail(string(event.finalError || 'Pi retries exhausted.').trim());
    } else if (type === 'extension_error') {
      this.fail(string(event.error || 'Pi extension failed.').trim());
    } else if (type === 'agent_settled' && !this.turnFailed) {
      this.turnCompleted = true;
    }
  }

  private fail(message: string): void {
    this.turnFailed = true;
    this.fatalError ??= message;
  }
}
