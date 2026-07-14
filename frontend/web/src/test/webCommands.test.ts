import { describe, expect, it, vi } from 'vitest';
import { COMMANDS } from '../../../core/src/commands';
import { dispatchWebCommand, type WebCommandHandlers } from '../lib/webCommands';

function handlers() {
  return Object.fromEntries(
    COMMANDS.map((command) => [command.id, vi.fn(async () => undefined)]),
  ) as unknown as WebCommandHandlers;
}

describe('web slash dispatch', () => {
  it('routes every canonical command to its stable handler id', async () => {
    for (const command of COMMANDS) {
      const table = handlers();
      const argument = command.argument === 'required' ? 'value' : '';
      const result = await dispatchWebCommand(
        `${command.name}${argument ? ` ${argument}` : ''}`,
        table,
      );
      expect(result.kind).toBe('handled');
      expect(table[command.id]).toHaveBeenCalledWith(argument);
    }
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
