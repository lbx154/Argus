#!/usr/bin/env node
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { callAgent } from './agent/call.js';
import { Flow } from './flow.js';
import { serve } from './serve/index.js';
import { Store, type FlowEvent, type Project } from './store.js';
import { BACKENDS, type Backend } from './types.js';

const USAGE = `Usage:
  argus run "<request>" [--dir DIR] [--backend codex|claude|copilot|pi] [--model M]
  argus say <project> "<text>"
  argus answer <project> "<text>"
  argus status [project]
  argus serve [--port N] [--web DIR] [--backend codex|claude|copilot|pi] [--model M]   (ARGUS_WEB_TOKEN: require this bearer token)
`;

const write = (line: string): void => { process.stdout.write(line + '\n'); };
const firstLine = (text: string): string => text.split('\n')[0]!;

function show(event: FlowEvent): void {
  const who = 'role' in event ? [event.role, event.task].filter(Boolean).join(' ') : '';
  // A letter to the operator is read in full (a question when the work waits on it); others by their first line.
  if (event.type === 'call.start') write(`${who} ...`);
  else if (event.type === 'handoff') write(event.to === 'operator' ? `\n${event.letter}\n` : `${who} -> ${event.to}: ${firstLine(event.letter)}`);
  else if (event.type === 'task.added') write(`+ ${event.task.id} ${event.task.title}`);
  else if (event.type === 'status' && event.question) write(`\n${event.question.question}\n`);
  else if (event.type === 'status' && event.status !== 'working') write(event.error ? `failed: ${event.error}` : event.status);
}

function status(store: Store, id: string | undefined): void {
  if (!id) {
    for (const project of store.list()) write(`${project.id}  ${project.status.padEnd(7)}  ${project.request.split('\n')[0]!.slice(0, 60)}  (${project.workdir})`);
    return;
  }
  const { status, workdir, request, tasks, question, error } = store.load(id);
  write(`${id}  ${status}  ${workdir}\nrequest: ${request}`);
  for (const task of tasks) write(`  ${task.id}  ${(task.status === 'open' ? `open, ${task.holder}` : task.status).padEnd(15)}  ${task.title}`);
  if (question) write(`question: ${question.question}`);
  if (error) write(`error: ${error}`);
}

/** Serve the web cockpit until interrupted; the work of its projects runs in this process. */
async function web(store: Store, backend: Backend, options: { port: string; web?: string; model?: string }): Promise<void> {
  const release = store.own();
  const token = process.env.ARGUS_WEB_TOKEN || undefined;
  const served = await serve({
    store, callAgent, backend, model: options.model ?? null, port: Number(options.port), ...(token ? { token } : {}),
    web: resolve(options.web ?? fileURLToPath(new URL('../web', import.meta.url))),
  }).catch((error: unknown) => { release(); throw error; });
  write(`Argus is at ${served.url}/${token ? `?token=${encodeURIComponent(token)}` : ''}`);
  const stop = (): void => { void served.close().finally(release); };
  process.once('SIGINT', stop).once('SIGTERM', stop);
}

async function main(): Promise<void> {
  const { values, positionals: [command, ...args] } = parseArgs({ allowPositionals: true, options: {
    dir: { type: 'string' }, backend: { type: 'string', default: 'codex' }, model: { type: 'string' }, help: { type: 'boolean', short: 'h' },
    port: { type: 'string', default: '8799' }, web: { type: 'string' },
  } });
  const store = new Store();
  if (command === 'status' && !values.help) return status(store, args[0]);
  const [first, text] = args;
  const backend = values.backend as Backend;
  if (!BACKENDS.includes(backend)) throw new Error(`--backend must be one of ${BACKENDS.join(', ')}`);
  if (command === 'serve' && !values.help && !args.length) return web(store, backend, values);
  if (values.help || !first || !(command === 'run' ? args.length === 1 : (command === 'say' || command === 'answer') && text !== undefined)) {
    process.stdout.write(USAGE);
    return;
  }
  const release = store.own();
  const controller = new AbortController();
  process.once('SIGINT', () => controller.abort());
  try {
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
