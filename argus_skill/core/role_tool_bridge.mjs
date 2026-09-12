import http from "node:http";

const MAX_RESPONSE_BYTES = 393216;

export function bridgeRequest(prefix, operation, payload, signal, env = process.env) {
  return new Promise((resolve, reject) => {
    const port = Number(env[`${prefix}_PORT`]), token = env[`${prefix}_TOKEN`];
    if (!Number.isInteger(port) || port <= 0 || port >= 65536 || !token) {
      reject(new Error("Tool requires a running Argus role turn.")); return;
    }
    const body = JSON.stringify(payload);
    if (Buffer.byteLength(body) > 65536) { reject(new Error("Tool request is oversized.")); return; }
    const request = http.request({
      hostname: "127.0.0.1", port, path: `/${operation}`, method: "POST", signal,
      headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body)},
    }, response => {
      const chunks = []; let bytes = 0;
      response.on("data", chunk => {
        bytes += chunk.length;
        if (bytes > MAX_RESPONSE_BYTES) { request.destroy(new Error("Tool response is oversized.")); return; }
        chunks.push(chunk);
      });
      response.on("error", reject);
      response.on("end", () => {
        try {
          const result = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          if (response.statusCode !== 200) throw new Error(result.error || "Tool request failed.");
          resolve(result);
        } catch (error) { reject(error); }
      });
    });
    request.setTimeout(operation === "cancel" ? 1000 : Math.min(1815, Number(env[`${prefix}_TIMEOUT`]) || 130) * 1000,
      () => request.destroy(new Error("Tool request timed out.")));
    request.on("error", error => reject(["ECONNRESET", "EPIPE"].includes(error?.code)
      ? new Error("Role tool is busy or closing; retry during the active turn.") : error));
    request.end(body);
  });
}
