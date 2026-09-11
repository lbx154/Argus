// Read-only observer for the pinned hosted Pi CLI. This file is loaded explicitly
// by the parent adapter; no user extension, prompt, header, or credential is read.
import net from "node:net";
import {isDeepStrictEqual} from "node:util";

const PROFILE = "pi-0.85.1-hosted-workspace-v1";
const MAX_TEXT = 262144, MAX_RESULT = 65536, MAX_PAYLOAD = 4194304;

export function trainingExtension(submit) {
  return function (pi) {
    let episode = null, privateBlocks = 0, allowed = new Set();
    let running = false;
    let currentPublicContext = null;

    function bounded(value) {
      let nodes = 250000;
      const visit = (item, depth = 0) => {
        if (--nodes < 0 || depth > 30) throw Error("capture_payload_oversized");
        if (typeof item === "string" && item.length > MAX_TEXT) throw Error("capture_payload_oversized");
        if (Array.isArray(item)) item.forEach(v => visit(v, depth + 1));
        else if (item && typeof item === "object") Object.values(item).forEach(v => visit(v, depth + 1));
      };
      visit(value);
      if (Buffer.byteLength(JSON.stringify(value)) > MAX_PAYLOAD) throw Error("capture_payload_oversized");
      return value;
    }

    async function quarantine(reason) {
      if (episode === null) return;
      const key = episode; episode = null;
      try { await submit("event", {episode_id: key, kind: "quarantine", payload: {reason}}); } catch (_) {}
    }

    async function project(kind, makePayload) {
      if (episode === null) return;
      try {
        const permission = await submit("authorize", {});
        if (!permission.enabled) { episode = null; return; }
        const payload = bounded(makePayload());
        const receipt = await submit("event", {episode_id: episode, kind, payload});
        if (receipt.state !== "capturing") episode = null;
      } catch (error) {
        const reasons = new Set(["private_or_nontext_context", "tool_result_excerpt_truncated",
          "unreviewed_tool_or_document_access", "provider_context_mismatch", "runtime_profile_changed"]);
        await quarantine(reasons.has(error?.message) ? error.message : "capture_projection_failed");
      }
    }

    function blocks(value, role) {
      const items = typeof value === "string" ? [{type: "text", text: value}] : value;
      if (!Array.isArray(items) || items.length > 256) throw Error("private_or_nontext_context");
      const texts = [], calls = [];
      for (const block of items) {
        if (block?.type === "thinking" && role === "assistant") { privateBlocks++; continue; }
        if (block?.type === "text" && typeof block.text === "string") {
          if (block.text.length > (role === "toolResult" ? MAX_RESULT : MAX_TEXT))
            throw Error(role === "toolResult" ? "tool_result_excerpt_truncated" : "capture_payload_oversized");
          if (role !== "assistant" || block.text.trim()) texts.push(block.text);
        } else if (block?.type === "toolCall" && role === "assistant") {
          if (!allowed.has(block.name) || block.namespace) throw Error("unreviewed_tool_or_document_access");
          // thoughtSignature is provider-private metadata and is never copied.
          calls.push({type: "toolCall", id: block.id, name: block.name, arguments: block.arguments});
        } else throw Error("private_or_nontext_context");
      }
      const text = texts.join(role === "toolResult" ? "\n" : "");
      return [...(texts.length ? [{type: "text", text}] : []), ...calls];
    }

    function messages(value) {
      if (!Array.isArray(value) || value.length > 256) throw Error("capture_payload_oversized");
      return value.map(message => {
        if (!["user", "assistant", "toolResult"].includes(message.role)) throw Error("private_or_nontext_context");
        const item = {role: message.role, content: blocks(message.content, message.role), timestamp: message.timestamp};
        if (message.role === "assistant") item.stopReason = message.stopReason;
        if (message.role === "toolResult") Object.assign(item, {
          toolCallId: message.toolCallId, toolName: message.toolName, isError: message.isError,
        });
        return item;
      });
    }

    function providerProjection(payload) {
      if (!payload || !Array.isArray(payload.messages) || !Array.isArray(payload.tools)
          || typeof payload.model !== "string") throw Error("provider_context_mismatch");
      const calls = new Map();
      const publicMessages = [];
      for (const message of payload.messages) {
        if (["system", "developer"].includes(message.role)) continue;
        if (!["user", "assistant", "tool"].includes(message.role)) throw Error("provider_context_mismatch");
        const item = {role: message.role};
        if (typeof message.content === "string" && message.content) item.content = message.content;
        else if (Array.isArray(message.content)) {
          if (!message.content.every(block => block.type === "text" && typeof block.text === "string"))
            throw Error("private_or_nontext_context");
          item.content = message.content.map(block => block.text).join("");
        } else if (message.content !== null && message.content !== undefined && message.content !== "")
          throw Error("provider_context_mismatch");
        if (message.tool_calls) {
          item.tool_calls = message.tool_calls.map(call => {
            if (call.type !== "function" || !allowed.has(call.function?.name)
                || typeof call.function.arguments !== "string") throw Error("provider_context_mismatch");
            calls.set(call.id, call.function.name);
            return {id: call.id, type: "function", function: {
              name: call.function.name, arguments: JSON.parse(call.function.arguments),
            }};
          });
        }
        if (message.role === "tool") {
          const name = calls.get(message.tool_call_id);
          if (!name || (message.name !== undefined && message.name !== name)) throw Error("provider_context_mismatch");
          Object.assign(item, {tool_call_id: message.tool_call_id, name});
        }
        // reasoning_content/reasoning_details/signatures and every non-public
        // field are intentionally absent from this provider request projection.
        publicMessages.push(item);
      }
      // Some provider adapters turn private thinking into ordinary content.
      // Reject any such divergence in memory, before crossing the IPC boundary.
      if (!isDeepStrictEqual(publicMessages, currentPublicContext)) throw Error("provider_context_mismatch");
      return {messages: publicMessages, tools: payload.tools, model: payload.model};
    }

    pi.on("agent_start", async (_event, ctx) => {
      if (running) { await quarantine("session_compacted_or_reused"); return; }
      running = true;
      let failure = "capture_init_authorize_failed";
      try {
        if (ctx.model?.api !== "openai-completions" || ctx.model?.provider !== "argus") return;
        const permission = await submit("authorize", {});
        if (!permission.enabled) return;
        failure = "capture_init_begin_failed";
        const begun = await submit("begin", {session_id: ctx.sessionManager.getSessionId()});
        failure = "runtime_profile_changed";
        if (begun.profile !== PROFILE) throw Error(failure);
        failure = "capture_init_reply_invalid";
        if (!Number.isSafeInteger(begun.episode_id) || begun.episode_id < 1
            || !Array.isArray(begun.allowed_tools) || !begun.allowed_tools.length
            || begun.allowed_tools.some(name => typeof name !== "string")) throw Error(failure);
        episode = begun.episode_id; allowed = new Set(begun.allowed_tools);
      } catch (error) {
        episode = null;
        if (error?.message === "capture_transport_timeout" && failure.startsWith("capture_init_"))
          failure = failure.replace("_failed", "_timeout");
        // The host owns the episode binding even if its begin reply arrived
        // after our timeout. Only fixed codes cross IPC; never error text.
        try { await submit("init_failed", {reason: failure}); } catch (_) {}
      }
    });
    pi.on("context", async event => {
      await project("context", () => {
        const active = pi.getActiveTools();
        const tools = pi.getAllTools().filter(tool => active.includes(tool.name)).map(tool => ({
          name: tool.name, description: tool.description, parameters: tool.parameters,
        }));
        if (tools.length !== active.length || active.some(name => !allowed.has(name)))
          throw Error("unreviewed_tool_or_document_access");
        const projected = messages(event.messages);
        currentPublicContext = projected.map(message => {
          const item = {role: message.role === "toolResult" ? "tool" : message.role};
          const text = message.content.find(block => block.type === "text");
          if (text) item.content = text.text;
          const calls = message.content.filter(block => block.type === "toolCall");
          if (calls.length) item.tool_calls = calls.map(call => ({id: call.id, type: "function",
            function: {name: call.name, arguments: call.arguments}}));
          if (message.role === "toolResult") Object.assign(item, {tool_call_id: message.toolCallId, name: message.toolName});
          return item;
        });
        return {messages: projected, tools};
      });
    });
    pi.on("before_provider_request", async event => {
      await project("provider_request", () => providerProjection(event.payload));
    });
    pi.on("tool_call", async event => {
      await project("tool_call", () => {
        if (!allowed.has(event.toolName)) throw Error("unreviewed_tool_or_document_access");
        return {toolCallId: event.toolCallId, toolName: event.toolName, input: event.input};
      });
    });
    pi.on("tool_result", async event => {
      await project("tool_result", () => ({
        toolCallId: event.toolCallId, toolName: event.toolName, input: event.input,
        content: blocks(event.content, "toolResult"), isError: event.isError,
        output_complete: !event.details?.truncation?.truncated && !event.details?.fullOutputPath,
      }));
    });
    pi.on("agent_end", async event => {
      await project("agent_end", () => ({messages: messages(event.messages), private_blocks_excluded: privateBlocks > 0}));
    });
    pi.on("agent_settled", async () => { await project("settled", () => ({})); });
    pi.on("session_before_compact", async () => { await quarantine("session_compacted_or_reused"); });
  };
}

export default function (pi) {
  const socketPath = process.env.ARGUS_TRAINING_BRIDGE_SOCKET;
  const lease = process.env.ARGUS_TRAINING_LEASE_TOKEN;
  // Ordinary shell/file tool children do not inherit the per-call capability.
  delete process.env.ARGUS_TRAINING_LEASE_TOKEN;
  if (!socketPath || !lease) return;
  const submit = (action, value) => new Promise((resolve, reject) => {
    const socket = net.createConnection({path: socketPath});
    let data = "";
    socket.setTimeout(10000, () => socket.destroy(Error("capture_transport_timeout")));
    socket.on("connect", () => socket.end(JSON.stringify({action, lease, value}) + "\n"));
    socket.on("data", chunk => {
      data += chunk.toString("utf8");
      if (data.length > 32768) socket.destroy(Error("capture_transport_failed"));
    });
    socket.on("error", reject);
    socket.on("end", () => {
      try {
        const reply = JSON.parse(data);
        if (reply.error) reject(Error(reply.error)); else resolve(reply);
      } catch (error) { reject(error); }
    });
  });
  trainingExtension(submit)(pi);
}
