export function runtimeExtension(submit, schema, writable) {
  return pi => {
    const register = (name, description, parameters, operation) => pi.registerTool({
      name, label: name, description, parameters,
      async execute(_id, params, signal) {
        try {
          const result = await submit(operation, params, signal);
          return {content: [{type: "text", text: JSON.stringify(result)}], details: result};
        } catch (error) {
          if (signal?.aborted) {
            try { await submit("cancel", {}); } catch (_) {}
          }
          return {content: [{type: "text", text: error?.message || "Project runtime tool unavailable."}], isError: true};
        }
      },
    });
    register("list_learned_tools", "List this project's available JSON transformations and current revisions.",
      schema.Object({}, {additionalProperties: false}), "list");
    register("run_learned_tool", "Use a project JSON transformation. A pending candidate is published with its Skill/Wiki only after successful use on the current task input.",
      schema.Object({name: schema.String(), input: schema.Unknown()}, {additionalProperties: false}), "run");
    if (!writable) return;
    pi.on("tool_result", async event => {
      const id = String(event.toolCallId).split("|", 1)[0];
      try {
        await submit("observe", {id, tool: event.toolName});
        return {content: [...event.content, {type: "text", text: `[observation:${id}]`}]};
      } catch (_) {
        // A failed optional observation must not replace a task's actual tool result.
        return undefined;
      }
    });
    const document = title => schema.Object({[title]: schema.String(), description: schema.String(), content: schema.String()}, {additionalProperties: false});
    register("evolve_runtime", "Propose a reusable Python def run(value) JSON transformation using actual observations. No file/network/process access. Cases validate before use; only a later successful run publishes the code and supplied Skill/Wiki. Cite limits rather than claiming general correctness.",
      schema.Object({name: schema.String(), expected_revision: schema.Integer({minimum: 0}),
        source: schema.String(), evidence: schema.Array(schema.String(), {minItems: 2, maxItems: 8}),
        cases: schema.Array(schema.Object({input: schema.Unknown(), expected: schema.Unknown()}, {additionalProperties: false}), {minItems: 2, maxItems: 8}),
        skill: document("name"), wiki: document("title")}, {additionalProperties: false}), "propose");
    register("rollback_runtime", "Restore this role's previous tool revision and its Skill/Wiki, or withdraw a first revision that new evidence invalidated.",
      schema.Object({name: schema.String(), expected_revision: schema.Integer({minimum: 1})}, {additionalProperties: false}), "rollback");
  };
}
