// The Manager's incoming team letters, shared by record projection and legacy-state migration.
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { handedTo } from './prompts.js';
import type { FlowEvent, ManagerLetter, Project, Store } from './store.js';
import type { Role } from './types.js';

interface Input {
  id: string;
  line: number;
  callLine: number | undefined;
  attempts: number;
  role: Role;
  task: string | undefined;
  letter: string;
}

/** A completed first attempt with a session can be corrected once in that same turn.
 * Correction takes precedence over later incoming letters; failure leaves them queued. */
export class ManagerInputs {
  readonly pending = new Map<string, Input>();
  private readonly queue: Input[] = [];
  private current: Input | undefined;
  private correctable = false;

  take(event: FlowEvent, line: number): Input | undefined {
    if (event.type === 'handoff' && event.to === 'manager' && event.role !== 'manager') {
      const input: Input = { id: `legacy-letter-${line}`, line, callLine: undefined, attempts: 0,
        role: event.role, task: event.task, letter: event.letter };
      this.pending.set(input.id, input); this.queue.push(input);
    } else if (event.type === 'call.start' && event.role === 'manager') {
      if (event.answers) { this.current = undefined; this.correctable = false; return; }
      const correction = this.correctable && (!event.input_id || event.input_id === this.current?.id);
      const input = event.input_id ? this.pending.get(event.input_id) ?? this.queue.shift()
        : this.correctable ? this.current : this.queue.shift() ?? this.current ?? this.pending.values().next().value;
      this.current = input ?? { id: `legacy-c${line}`, line, callLine: undefined, attempts: 0,
        role: 'manager', task: event.task, letter: '' };
      this.pending.delete(this.current.id);
      this.current.id = event.input_id ?? (this.current.callLine === undefined ? `legacy-c${line}` : this.current.id);
      this.current.callLine ??= line;
      this.current.attempts = correction ? this.current.attempts + 1 : 1;
      this.pending.set(this.current.id, this.current);
      this.correctable = false;
      return this.current;
    } else if (event.type === 'call.end' && event.role === 'manager') {
      this.correctable = Boolean(event.completed && event.threadId && this.current?.attempts === 1);
    } else if (event.type === 'handoff' && event.role === 'manager') {
      const accepted = event.input_id ? this.pending.get(event.input_id) : this.current;
      if (accepted) this.pending.delete(accepted.id);
      if (accepted === this.current) this.current = undefined;
      this.correctable = false;
    }
    return;
  }
}

/** Name saved strings by their pending recorded input, retaining the original call identity.
 * Matching among pending letters preserves duplicate letters in their recorded queue order. */
export function nameLegacyInputs(store: Store, project: Project): ManagerLetter[] {
  const inputs = new ManagerInputs();
  const raw = readFileSync(join(store.dir(project.id), 'events.jsonl'), 'utf8');
  let line = 0;
  for (const text of raw.slice(0, raw.lastIndexOf('\n')).split('\n')) {
    let event: FlowEvent;
    try { event = JSON.parse(text) as FlowEvent; } catch { continue; }
    inputs.take(event, line++);
  }
  const remaining = [...inputs.pending.values()].sort((a, b) => a.line - b.line).filter(input => !(project.forManager ?? []).some(letter => typeof letter !== 'string' && letter.input_id === input.id));
  return (project.forManager ?? []).map(letter => {
    if (typeof letter !== 'string') return letter;
    const index = remaining.findIndex(input => handedTo(input.role, project.tasks.find(task => task.id === input.task) ?? null, input.letter) === letter);
    if (index < 0) return { text: letter, input_id: randomUUID() };
    const [input] = remaining.splice(index, 1);
    return { text: letter, input_id: input!.id };
  });
}
