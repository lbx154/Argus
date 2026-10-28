// The hand_off tool of one call: its schema from the form, the judgment of each call, and
// a loopback MCP endpoint (plain JSON-RPC over HTTP POST) through which the CLI reaches it.

import { randomBytes, timingSafeEqual } from 'node:crypto';
import { createServer, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { HANDOFF_FORMS, isJsonObject, schemaErrors, type PayloadSchema } from '@argus/contracts';
import type { AgentRequest, Form, HandOff } from '../types.js';

/** The MCP server name the CLIs know the tool under. */
export const SERVER = 'argus_actions';
export const TOOL = HANDOFF_FORMS.action;

export function handOffTool(form: Form): { name: string; description: string; inputSchema: PayloadSchema } {
  const spec = HANDOFF_FORMS.forms[form]!;
  const core = HANDOFF_FORMS.core;
  const properties: Record<string, PayloadSchema> = Object.fromEntries(spec.core.map(name => [name, core[name]!]));
  if (spec.to.length) {
    // The operator rule goes only to a form that can hand to them.
    const rule = spec.to.includes('operator') ? ` ${HANDOFF_FORMS.operator}` : '';
    properties.to = { ...core.to, description: `${core.to!.description}${rule}`, enum: spec.to };
  }
  return {
    name: TOOL, description: spec.description,
    inputSchema: { type: 'object', properties: { ...properties, ...spec.properties }, required: spec.required, additionalProperties: false },
  };
}

/** The reason a call is refused, or the hand-off it records. */
function judge(form: Form, schema: PayloadSchema, payload: unknown, check: AgentRequest['check']): string | HandOff {
  const errors = schemaErrors(payload, schema);
  if (errors.length || !isJsonObject(payload)) return errors.join('; ');
  const { to, summary, ...fields } = payload;
  // The rule every form shares: a question to the operator is their turn, asked properly.
  const ask = isJsonObject(payload.ask) ? payload.ask : undefined;
  if ((to === 'operator') !== Boolean(ask)) return 'to=operator goes with ask, and ask only with to=operator.';
  const labels = Array.isArray(ask?.options)
    ? ask.options.map(option => String(isJsonObject(option) ? option.label : '').toLowerCase().split(/\s+/).filter(Boolean).join(' '))
    : [];
  if (labels.length === 1 || new Set(labels).size < labels.length) {
    return 'ask.options: offer two to five different answers, or none for an open question.';
  }
  const handoff: HandOff = { form, to: String(to ?? ''), summary: String(summary ?? ''), fields };
  return check?.(handoff) ?? handoff;
}

export interface HandOffEndpoint {
  url: string;
  token: string;
  /** The last call, when it was accepted. */
  handoff: HandOff | null;
  close(): Promise<void>;
}

const send = (response: ServerResponse, status: number, body?: unknown): void => {
  if (body === undefined) response.writeHead(status).end();
  else response.writeHead(status, { 'content-type': 'application/json' }).end(JSON.stringify(body));
};

export async function serveHandOff(form: Form, check: AgentRequest['check']): Promise<HandOffEndpoint> {
  const tool = handOffTool(form);
  const token = randomBytes(32).toString('base64url');
  const authorization = Buffer.from(`Bearer ${token}`);
  const reply = (text: string, isError: boolean) => ({ content: [{ type: 'text', text }], isError });

  const call = (params: Record<string, unknown>) => {
    if (params.name !== tool.name) return reply(`Unknown tool: ${String(params.name)}`, true);
    // A new call replaces the last: if it is refused, the turn does not settle on the call it was correcting.
    endpoint.handoff = null;
    const verdict = judge(form, tool.inputSchema, params.arguments ?? {}, check);
    if (typeof verdict === 'string') return reply(verdict, true);
    endpoint.handoff = verdict;
    return reply(`Recorded: the work goes to ${verdict.to || form}.`, false);
  };
  const answer = (method: string, params: Record<string, unknown>): unknown => {
    switch (method) {
      case 'initialize': return {
        protocolVersion: typeof params.protocolVersion === 'string' ? params.protocolVersion : '2025-06-18',
        capabilities: { tools: {} }, serverInfo: { name: 'argus', version: '1' },
      };
      case 'ping': return {};
      case 'tools/list': return { tools: [tool] };
      case 'tools/call': return call(params);
      default: return undefined;
    }
  };

  const server = createServer(async (request, response) => {
    const given = Buffer.from(request.headers.authorization ?? '');
    if (given.length !== authorization.length || !timingSafeEqual(given, authorization)) return send(response, 401);
    if (request.method !== 'POST') return send(response, 405);
    let message: unknown;
    try {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(chunk as Buffer);
      message = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch {
      return send(response, 400, { jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } });
    }
    // Notifications and responses need no answer.
    if (!isJsonObject(message) || message.id === undefined || typeof message.method !== 'string') return send(response, 202);
    const { id, method } = message;
    try {
      const result = answer(method, isJsonObject(message.params) ? message.params : {});
      send(response, 200, result === undefined
        ? { jsonrpc: '2.0', id, error: { code: -32601, message: `Method not found: ${method}` } }
        : { jsonrpc: '2.0', id, result });
    } catch (error) {
      send(response, 200, { jsonrpc: '2.0', id, error: { code: -32603, message: String(error) } });
    }
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const endpoint: HandOffEndpoint = {
    url: `http://127.0.0.1:${(server.address() as AddressInfo).port}/mcp`, token, handoff: null,
    close: () => new Promise(resolve => { server.closeAllConnections(); server.close(() => resolve()); }),
  };
  return endpoint;
}
