#!/usr/bin/env node
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import type { CallAgent } from './types.js';
import { Flow } from './flow.js';
import { Store, type FlowEvent, type Project, type Question } from './store.js';

const USAGE = `Usage:
  argus run "<request>" [--dir DIR] [--backend codex|claude] [--model M]
  argus say <project> "<text>"
  argus answer <project> "<text>"
  argus status [project]
`;

const write = (line: string): void => { process.stdout.write(line + '\n'); };
const questionLines = ({ question, options }: Question): string =>
  [`question: ${question}`, ...options.map((option, i) => `  ${i + 1}. ${option.label}${option.description ? ` - ${option.description}` : ''}`)].join('\n');

function show(event: FlowEvent): void {
  const who = 'role' in event ? [event.role, event.task].filter(Boolean).join(' ') : '';
  if (event.type === 'message' && event.from === 'manager') write(`\n${event.text}\n`);
  else if (event.type === 'call.start') write(`${who} ...`);
  else if (event.type === 'handoff') write(`${who} -> ${event.to}: ${event.summary}`);
  else if (event.type === 'task.added') write(`+ ${event.task.id} ${event.task.title}${event.task.deps.length ? ` (after ${event.task.deps.join(', ')})` : ''}`);
  else if (event.type === 'status' && event.question) write(questionLines(event.question));
  else if (event.type === 'status' && event.status !== 'working') write(event.error ? `failed: ${event.error}` : event.status);
}

function status(store: Store, id: string | undefined): void {
  if (!id) {
    for (const project of store.list()) write(`${project.id}  ${project.status.padEnd(7)}  ${project.request.split('\n')[0]!.slice(0, 60)}  (${project.workdir})`);
    return;
  }
  const { status, workdir, request, ticket, tasks, question, error } = store.load(id);
  write(`${id}  ${status}  ${workdir}\nrequest: ${request}`);
  if (ticket) write(`ticket: ${ticket.goal}`);
  for (const task of tasks) write(`  ${task.id}  ${(task.status === 'open' ? `open, ${task.holder}` : task.status).padEnd(15)}  ${task.title}`);
  if (question) write(questionLines(question));
  if (error) write(`error: ${error}`);
}

async function main(): Promise<void> {
  const { values, positionals: [command, ...args] } = parseArgs({ allowPositionals: true, options: {
    dir: { type: 'string' }, backend: { type: 'string', default: 'codex' }, model: { type: 'string' }, help: { type: 'boolean', short: 'h' },
  } });
  const store = new Store();
  if (command === 'status' && !values.help) return status(store, args[0]);
  const [first, text] = args;
  if (values.help || !first || !(command === 'run' ? args.length === 1 : (command === 'say' || command === 'answer') && text !== undefined)) {
    process.stdout.write(USAGE);
    return;
  }
  const backend = values.backend;
  if (backend !== 'codex' && backend !== 'claude') throw new Error('--backend must be codex or claude');
  const release = store.own();
  const controller = new AbortController();
  process.once('SIGINT', () => controller.abort());
  try {
    // The agent layer (src/agent/) exports callAgent.
    const agent = './agent/index.js';
    const { callAgent } = await import(agent) as { callAgent: CallAgent };
    const flow = new Flow(store, callAgent, { onEvent: show, signal: controller.signal });
    let project: Project;
    if (command === 'run') {
      const workdir = resolve(values.dir ?? '.');
      mkdirSync(workdir, { recursive: true });
      project = store.create({ workdir, request: first, backend, model: values.model ?? null });
      write(`project ${project.id} in ${workdir}`);
      project = await flow.say(project.id, first);
    } else project = await (command === 'say' ? flow.say(first, text!) : flow.answer(first, text!));
    if (controller.signal.aborted) write(`interrupted; continue with: argus say ${project.id} "<text>"`);
    else if (project.status === 'asking') write(`answer with: argus answer ${project.id} "<text>"`);
    else if (project.status === 'failed') write(`continue with: argus say ${project.id} "<text>"`);
  } finally {
    release();
  }
}

main().catch(error => { process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`); process.exitCode = 1; });
