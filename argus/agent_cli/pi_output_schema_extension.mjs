// Only explicitly bound, tools-disabled text calls load this extension.
import {writeSync} from "node:fs";

export const SCHEMA_ENV = "ARGUS_PI_OUTPUT_SCHEMA";

function failCall(reason) {
  // Pi catches ordinary before_provider_request exceptions and would continue
  // with the old payload. End this dedicated CLI call before any such fallback.
  try {
    writeSync(2, `Argus structured output: ${reason}\n`);
  } finally {
    process.exit(1);
  }
}

export function structuredOutputExtension(schema) {
  return function (pi) {
    pi.on("before_provider_request", (event, ctx) => {
      try {
        const payload = event.payload;
        const api = ctx.model?.api;
        if (!["openai-completions", "openai-responses"].includes(api)) {
          return failCall("Pi output_schema does not support this provider API");
        }
        if (!payload || (api === "openai-completions" ? !Array.isArray(payload.messages)
            : !Array.isArray(payload.input) && typeof payload.input !== "string")) {
          return failCall("OpenAI request payload is unavailable");
        }
        if (pi.getActiveTools().length || (payload.tools != null &&
            (!Array.isArray(payload.tools) || payload.tools.length))) {
          return failCall("output_schema requires a tools-disabled request");
        }
        const format = {name: "argus_output", strict: true, schema};
        if (api === "openai-responses") {
          if (payload.text != null && (typeof payload.text !== "object" || Array.isArray(payload.text))) {
            return failCall("OpenAI Responses text options are invalid");
          }
          return {...payload, text: {...payload.text, format: {type: "json_schema", ...format}}};
        }
        return {...payload, response_format: {type: "json_schema", json_schema: format}};
      } catch (_) {
        return failCall("could not apply the required output schema");
      }
    });
  };
}

export default function (pi) {
  const raw = process.env[SCHEMA_ENV];
  delete process.env[SCHEMA_ENV];
  try {
    const schema = JSON.parse(raw);
    if (!schema || Array.isArray(schema) || typeof schema !== "object") {
      return failCall("output_schema must be a JSON Schema object");
    }
    structuredOutputExtension(schema)(pi);
  } catch (_) {
    failCall("required output schema could not be loaded");
  }
}
