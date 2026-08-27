import { once } from 'node:events';
import { createInterface } from 'node:readline';
import type { Readable, Writable } from 'node:stream';

import type { TypedArgusEvent } from '../../core/src/eventPayloads.generated.js';
import {
  renderEvent,
  renderText,
  type RenderContext,
  type RenderModel,
} from '../../core/src/eventRender/index.js';

const ROLE_LABELS: Record<string, string> = {
  manager: 'Manager',
  planner: 'Planner',
  engineer: 'Engineer',
  reviewer: 'Reviewer',
  critic: 'Critic',
  system: 'Argus',
};

export function parseRenderEventsArgs(argv: string[]): RenderContext {
  const context: RenderContext = {
    locale: 'en',
    showReasoning: false,
    unknownEventPolicy: 'greppable',
    density: 'full',
  };
  for (let i = 0; i < argv.length; i += 1) {
    const option = argv[i];
    if (option === '--show-reasoning') {
      context.showReasoning = true;
      continue;
    }
    const value = argv[i + 1];
    if (!value || value.startsWith('-')) throw new Error(`${option} requires a value`);
    if (option === '--locale' && (value === 'en' || value === 'zh-CN')) {
      context.locale = value;
    } else if (option === '--unknown-event-policy' && (value === 'hide' || value === 'greppable')) {
      context.unknownEventPolicy = value;
    } else if (option === '--density' && (value === 'compact' || value === 'full')) {
      context.density = value;
    } else {
      throw new Error(`invalid render-events option: ${option} ${value}`);
    }
    i += 1;
  }
  return context;
}

function modelLabel(model: RenderModel, context: RenderContext): string {
  if (model.labelKey === 'role.operator') return context.locale === 'zh-CN' ? '你' : 'You';
  return ROLE_LABELS[model.role]
    ?? `${model.role.slice(0, 1).toUpperCase()}${model.role.slice(1)}`;
}

export function renderEventLine(event: TypedArgusEvent, context: RenderContext): string {
  const model = renderEvent(event, context);
  if (model.visibility === 'hidden') return '';
  const body = renderText(model).replace(/\s+/g, ' ').trim();
  return [model.glyph, `[${modelLabel(model, context)}]`, body].filter(Boolean).join(' ');
}

/** Each non-empty NDJSON input record produces exactly one physical output line. */
export async function runRenderEvents(
  input: Readable,
  output: Writable,
  context: RenderContext,
): Promise<void> {
  const lines = createInterface({ input, crlfDelay: Infinity });
  let lineNumber = 0;
  for await (const line of lines) {
    lineNumber += 1;
    if (!line.trim()) continue;
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch (error) {
      throw new Error(`invalid NDJSON at line ${lineNumber}: ${(error as Error).message}`);
    }
    if (typeof value !== 'object' || value === null || Array.isArray(value)
        || typeof (value as { type?: unknown }).type !== 'string') {
      throw new Error(`invalid event at line ${lineNumber}: expected an object with a string type`);
    }
    if (!output.write(`${renderEventLine(value as TypedArgusEvent, context)}\n`)) {
      await once(output, 'drain');
    }
  }
}
