import {
  commandNeedsArgument,
  didYouMean,
  parseCommand,
  type CommandId,
} from '../../../core/src/commands';

export type WebCommandHandler = (rest: string) => void | Promise<void>;
export type WebCommandHandlers = Record<CommandId, WebCommandHandler>;
export type WebCommandResult =
  | { kind: 'not-command' }
  | { kind: 'handled' }
  | { kind: 'error'; message: string };

export async function dispatchWebCommand(
  line: string,
  handlers: WebCommandHandlers,
): Promise<WebCommandResult> {
  const parsed = parseCommand(line.trim());
  if (!parsed) return { kind: 'not-command' };
  if (!parsed.cmd) {
    const suggestion = didYouMean(parsed.name);
    return {
      kind: 'error',
      message: suggestion
        ? `Unknown command ${parsed.name}. Did you mean ${suggestion}?`
        : `Unknown command ${parsed.name}. Use /help for the full list.`,
    };
  }
  if (commandNeedsArgument(parsed.cmd) && !parsed.rest) {
    return {
      kind: 'error',
      message: `Usage: ${parsed.cmd.name}${parsed.cmd.arg ? ` ${parsed.cmd.arg}` : ''}`,
    };
  }
  try {
    await handlers[parsed.cmd.id](parsed.rest);
  } catch (error) {
    return {
      kind: 'error',
      message: error instanceof Error ? error.message : String(error ?? 'Command failed'),
    };
  }
  return { kind: 'handled' };
}
