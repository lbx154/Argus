import { describe, expect, it, vi } from 'vitest';
import { COMMANDS, type CommandId } from '../../../core/src/commands';
import { dispatchWebCommand, type WebCommandHandlers } from '../lib/webCommands';

function handlers() {
  return Object.fromEntries(
    COMMANDS.map((command) => [command.id, vi.fn(async () => undefined)]),
  ) as unknown as WebCommandHandlers;
}

describe('web slash dispatch', () => {
  it('routes every canonical command to its stable handler id', async () => {
    // `/ask` is answered by the Manager, not by a local handler; it has its
    // own test below.
    for (const command of COMMANDS.filter((c) => c.id !== 'ask' && c.id !== 'crystalpilot')) {
      const table = handlers();
      const argument = command.argument === 'required' ? 'value' : '';
      const result = await dispatchWebCommand(
        `${command.name}${argument ? ` ${argument}` : ''}`,
        table,
      );
      expect(result.kind).toBe('handled');
      expect(table[command.id as Exclude<CommandId, 'ask' | 'crystalpilot'>]).toHaveBeenCalledWith(
        argument,
      );
    }
  });

  it('passes /ask through as an ordinary message', async () => {
    // The Manager front door recognises the prefix and answers inline without
    // queuing anything; handling it here would need a second path to the same
    // reply.
    for (const line of ['/ask why is it slow', '/chat hello', '/crystalpilot', '/crystalpilot status']) {
      expect((await dispatchWebCommand(line, handlers())).kind).toBe('not-command');
    }
  });

  it('treats a pasted path as text, not a command', async () => {
    // Answering "Unknown command /data" to a pasted path is both wrong and
    // unhelpful — the operator meant to say something, not run something.
    for (const line of ['/data/yijia/run.py', '/tmp/out.log', '/Users/x/notes.md']) {
      expect((await dispatchWebCommand(line, handlers())).kind).toBe('not-command');
    }
  });

  it('still reports a real typo', async () => {
    // A single bare word that matches nothing is a mistyped command, and
    // silently sending it as work would be worse than saying so.
    const result = await dispatchWebCommand('/statu', handlers());

    expect(result.kind).toBe('error');
  });

  it('canonicalizes aliases', async () => {
    const table = handlers();
    await dispatchWebCommand('/rm task-1', table);
    expect(table.skip).toHaveBeenCalledWith('task-1');
  });

  it('keeps unknown and missing-argument commands local', async () => {
    const table = handlers();
    expect(await dispatchWebCommand('/staus', table)).toEqual({
      kind: 'error',
      message: 'Unknown command /staus. Did you mean /status?',
    });
    expect(await dispatchWebCommand('/rename', table)).toEqual({
      kind: 'error',
      message: 'Usage: /rename <name>',
    });
  });

  it('declines plain Manager text', async () => {
    expect(await dispatchWebCommand('continue the research', handlers())).toEqual({
      kind: 'not-command',
    });
  });
});
