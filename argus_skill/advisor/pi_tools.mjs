import {bridgeRequest as callBridge} from "../core/role_tool_bridge.mjs";

export function bridgeRequest(operation, payload, signal, env = process.env) {
  return callBridge("ARGUS_PLUGIN_ADVISOR", operation, payload, signal, env);
}

export function advisorExtension(submit, Type) {
  return pi => pi.registerTool({
    name: "consult_advisor", label: "Consult advisor",
    description: "Ask the independently configured advisor for a second opinion based on explicit project evidence. Advice does not change task state or replace your judgment.",
    parameters: Type.Object({
      question: Type.String({description: "The concrete question or decision to challenge."}),
      evidence_refs: Type.Array(Type.String(), {description: "Workspace-relative text paths, or state: paths; at most 16."}),
    }),
    async execute(toolCallId, params, signal) {
      try {
        const result = await submit("consult", {...params, request_id: toolCallId}, signal);
        return {content: [{type: "text", text: JSON.stringify(result)}], details: result,
          isError: result.status !== "completed"};
      } catch (error) {
        try { await submit("cancel", {request_id: toolCallId}); } catch (_) {}
        return {content: [{type: "text", text: error?.message || "Advisor unavailable."}], isError: true};
      }
    },
  });
}

