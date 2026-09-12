// Static imports are resolved by Pi's Jiti virtual-module loader.
import {Type} from "@sinclair/typebox";
import {advisorExtension, bridgeRequest} from "./pi_tools.mjs";

export default function(pi) {
  return advisorExtension(bridgeRequest, Type)(pi);
}
