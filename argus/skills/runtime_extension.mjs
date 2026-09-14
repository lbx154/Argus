import {Type} from "@sinclair/typebox";
import {bridgeRequest} from "../core/role_tool_bridge.mjs";
import {runtimeExtension} from "./runtime_pi_tools.mjs";

export default function(pi) {
  runtimeExtension((operation, payload, signal) => bridgeRequest(
    "ARGUS_PLUGIN_RUNTIME", operation, payload, signal), Type,
    process.env.ARGUS_PLUGIN_RUNTIME_WRITABLE === "1")(pi);
}
