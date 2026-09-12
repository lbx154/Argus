// Observe every actual Argus role call. Dataset quality is evaluated after storage.
import net from "node:net";

const PROFILE = "pi-0.85.1-hosted-workspace-v1";
const MAX_PAYLOAD = 16 * 1024 * 1024;

export function trainingExtension(submit) {
  return function (pi) {
    let episode = null, privateBlocks = 0;

    async function warning(reason, kind) {
      if (episode === null) return;
      try {
        await submit("event", {episode_id: episode, kind: "capture_warning", payload: {reason, kind}});
      } catch (_) {}
    }

    async function project(kind, makePayload) {
      if (episode === null) return;
      try {
        const payload = makePayload();
        if (Buffer.byteLength(JSON.stringify(payload)) > MAX_PAYLOAD) throw Error("capture_payload_oversized");
        const receipt = await submit("event", {episode_id: episode, kind, payload});
        if (receipt.state !== "capturing") episode = null;
      } catch (error) {
        // A malformed/oversized observation must not erase earlier observations
        // or prevent subsequent calls and final output from being collected.
        await warning(error?.message === "capture_payload_oversized" ? error.message : "capture_projection_failed", kind);
      }
    }

    function blocks(value, role) {
      if (typeof value === "string") return [{type: "text", text: value}];
      if (!Array.isArray(value)) return value;
      const output = [];
      for (const block of value) {
        if (["thinking", "redacted_thinking", "reasoning"].includes(block?.type) && role === "assistant") {
          privateBlocks++; continue;
        }
        if (block?.type === "text" && typeof block.text === "string") {
          output.push({type: "text", text: block.text});
        } else if (block?.type === "toolCall") {
          output.push({type: "toolCall", id: block.id, name: block.name, arguments: block.arguments,
            ...(block.namespace ? {namespace: block.namespace} : {})});
        } else if (block && typeof block === "object") {
          const {thoughtSignature, thinkingSignature, signature, ...content} = block;
          output.push(content);
        } else output.push(block);
      }
      return output;
    }

    function messages(value) {
      if (!Array.isArray(value)) return value;
      return value.map(message => {
        const item = {role: message.role, content: blocks(message.content, message.role), timestamp: message.timestamp};
        if (message.role === "assistant") {
          item.stopReason = message.stopReason;
          if (message.errorMessage) item.errorMessage = message.errorMessage;
        }
        if (message.role === "toolResult") Object.assign(item, {
          toolCallId: message.toolCallId, toolName: message.toolName, isError: message.isError,
        });
        return item;
      });
    }

    function providerProjection(payload) {
      const calls = new Map();
      const observed = (payload.messages || []).map(message => {
        // These system/developer messages belong to this application's actual
        // provider request and are part of its training input.
        const item = {role: message.role};
        if (typeof message.content === "string") item.content = message.content;
        else if (message.content !== undefined && message.content !== null) item.content = blocks(message.content, message.role);
        if (Array.isArray(message.tool_calls)) {
          item.tool_calls = message.tool_calls.map(call => {
            const fn = call.function || {};
            calls.set(call.id, fn.name);
            let args = fn.arguments;
            if (typeof args === "string") {
              try { args = JSON.parse(args); } catch (_) { /* Preserve the malformed arguments as observed. */ }
            }
            return {id: call.id, type: call.type, function: {name: fn.name, arguments: args}};
          });
        }
        if (message.role === "tool") Object.assign(item, {
          tool_call_id: message.tool_call_id, name: message.name || calls.get(message.tool_call_id),
        });
        return item;
      });
      return {messages: observed, tools: payload.tools || [], model: payload.model};
    }

    pi.on("agent_start", async (_event, ctx) => {
      if (episode !== null) {
        await project("quarantine", () => ({reason: "runtime_call_unsettled"}));
        episode = null;
      }
      privateBlocks = 0;
      let failure = "capture_init_authorize_failed";
      try {
        const permission = await submit("authorize", {});
        if (!permission.enabled) return;
        failure = "capture_init_begin_failed";
        const begun = await submit("begin", {
          session_id: ctx.sessionManager.getSessionId(), allowed_tools: pi.getActiveTools(),
        });
        failure = "runtime_profile_changed";
        if (begun.profile !== PROFILE) throw Error(failure);
        failure = "capture_init_reply_invalid";
        if (!Number.isSafeInteger(begun.episode_id) || begun.episode_id < 1) throw Error(failure);
        episode = begun.episode_id;
      } catch (error) {
        episode = null;
        if (error?.message === "capture_transport_timeout" && failure.startsWith("capture_init_"))
          failure = failure.replace("_failed", "_timeout");
        try { await submit("init_failed", {reason: failure}); } catch (_) {}
      }
    });
    pi.on("context", async event => {
      await project("context", () => {
        const active = pi.getActiveTools();
        const tools = pi.getAllTools().filter(tool => active.includes(tool.name)).map(tool => ({
          name: tool.name, description: tool.description, parameters: tool.parameters,
        }));
        return {messages: messages(event.messages), tools};
      });
    });
    pi.on("before_provider_request", async event => {
      await project("provider_request", () => providerProjection(event.payload));
    });
    pi.on("tool_call", async event => {
      await project("tool_call", () => ({toolCallId: event.toolCallId, toolName: event.toolName, input: event.input}));
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
    pi.on("session_before_compact", async () => { await warning("session_context_compacted", "context"); });
  };
}

export default function (pi) {
  const socketPath = process.env.ARGUS_TRAINING_BRIDGE_SOCKET;
  const lease = process.env.ARGUS_TRAINING_LEASE_TOKEN;
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
