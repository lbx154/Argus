import {
  commandNeedsArgument,
  didYouMean,
  parseCommand,
  type CommandId,
} from '../../../core/src/commands';

export type WebCommandHandler = (rest: string) => void | Promise<void>;
/** `/ask` is deliberately absent: it is answered by the Manager, so the web
 * layer passes the line through untouched rather than handling it locally. */
export type WebCommandHandlers = Record<
  Exclude<CommandId, 'ask' | 'crystalpilot'>,
  WebCommandHandler
>;
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
  if (parsed.cmd.id === 'ask' || parsed.cmd.id === 'crystalpilot') {
    // Send it as an ordinary message: the Manager front door recognises the
    // prefix and answers inline without queuing anything. Handling it here
    // would need a second path to the same reply.
    return { kind: 'not-command' };
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
