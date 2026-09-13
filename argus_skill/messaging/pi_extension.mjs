// Static imports are resolved by Pi's Jiti virtual-module loader.
import {Type} from "@sinclair/typebox";
import {bridgeRequest} from "../core/role_tool_bridge.mjs";
import {peerExtension} from "./pi_tools.mjs";

export default function(pi) {
  peerExtension((operation, payload, signal) => bridgeRequest("ARGUS_PLUGIN_PEER", operation, payload, signal), Type)(pi);
}
