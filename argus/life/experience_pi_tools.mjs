export function experienceExtension(submit, Type, writable = false) {
  return pi => {
    const register = (name, description, parameters, operation) => pi.registerTool({
      name, label: name, description, parameters,
      async execute(_toolCallId, params, signal) {
        try {
          const result = await submit(operation, params, signal);
          return {content: [{type: "text", text: JSON.stringify(result)}], details: result};
        } catch (error) {
          return {content: [{type: "text", text: error?.message || "Experience tool unavailable."}], isError: true};
        }
      },
    });
    register("search_experiences", "Find advisory experiences from this project; verify evidence before reuse.",
      Type.Object({query: Type.String()}), "search");
    register("get_experience", "Inspect current capsule, revision, provenance and claim boundaries.",
      Type.Object({experience_id: Type.String()}), "get");
    if (!writable) return;
    const common = {experience_id: Type.String(), expected_revision: Type.Integer({minimum: 1}),
      evidence_refs: Type.Array(Type.String({description: "Existing workspace: or state: relative text file; complete evidence totals at most 32 KiB."}), {minItems: 1, maxItems: 16}), reason: Type.String()};
    const changes = {};
    for (const key of ["title", "objective", "factual_outcome", "research_narrative"]) changes[key] = Type.Optional(Type.String());
    for (const key of ["passed_assumptions", "lessons", "transfer_insights", "claim_boundaries", "retry_conditions", "artifact_refs", "concepts", "causes"]) {
      changes[key] = Type.Optional(Type.Array(Type.String(), {maxItems: 24}));
    }
    register("revise_experience", "Correct an existing interpretation using new evidence and its exact current revision.",
      Type.Object({...common, changes: Type.Object(changes, {additionalProperties: false})}), "revise");
    register("retract_experience", "Withdraw an invalid capsule from future recall, retaining bounded revision provenance.",
      Type.Object(common), "retract");
  };
}
