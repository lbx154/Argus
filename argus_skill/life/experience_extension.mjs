import {Type} from "@sinclair/typebox";
import {bridgeRequest} from "../core/role_tool_bridge.mjs";
import {experienceExtension} from "./experience_pi_tools.mjs";

export default function(pi) {
  experienceExtension((operation, payload, signal) => bridgeRequest(
    "ARGUS_PLUGIN_EXPERIENCE", operation, payload, signal), Type,
    process.env.ARGUS_PLUGIN_EXPERIENCE_WRITABLE === "1")(pi);
}
