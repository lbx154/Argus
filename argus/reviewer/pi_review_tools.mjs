import {bridgeRequest} from "../core/role_tool_bridge.mjs";

export default async function(pi) {
  const {tools} = await bridgeRequest("ARGUS_PLUGIN_REVIEW", "tools", {});
  for (const tool of tools) {
    pi.registerTool({
      name: tool.name,
      label: tool.name.replaceAll("_", " "),
      description: tool.description,
      parameters: tool.inputSchema,
      async execute(_id, payload, signal) {
        const result = await bridgeRequest("ARGUS_PLUGIN_REVIEW", tool.name, payload, signal);
        return {content: [{type: "text", text: JSON.stringify(result)}], details: result};
      },
    });
  }
}
