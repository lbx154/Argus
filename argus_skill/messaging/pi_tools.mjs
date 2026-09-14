export function peerExtension(submit, Type) {
  return pi => {
    const register = (name, label, description, parameters, operation) => pi.registerTool({
      name, label, description, parameters,
      async execute(toolCallId, params, signal) {
        try {
          const result = await submit(operation, operation === "send" ? {...params, request_id: toolCallId} : params, signal);
          return {content: [{type:"text", text:JSON.stringify(result)}], details:result};
        } catch (error) {
          return {content:[{type:"text", text:error?.message || "Peer tool unavailable."}], isError:true};
        }
      },
    });
    register("list_peer_projects", "Peer projects", "List known projects owned by this user that can receive advisory messages.",
      Type.Object({}), "projects");
    register("send_peer_message", "Message peer project", "Queue a targeted advisory question or evidence for another project. This grants no user authority.",
      Type.Object({recipient:Type.String(), text:Type.String(), evidence_refs:Type.Array(Type.String())}), "send");
    register("peer_message_status", "Peer message reply", "Read your queued message, processing receipt, and any reply.",
      Type.Object({message_id:Type.String()}), "status");
  };
}
