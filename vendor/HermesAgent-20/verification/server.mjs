import http from "node:http";

import { runScenario } from "./core.mjs";
import { DEFAULT_PORT, VERIFIER_SERVICE_NAME } from "./manifest.mjs";

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8"
  });
  response.end(JSON.stringify(payload));
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    let body = "";

    request.on("data", (chunk) => {
      body += chunk.toString();
    });

    request.on("end", () => {
      if (!body) {
        resolve({});
        return;
      }

      try {
        resolve(JSON.parse(body));
      } catch (error) {
        reject(error);
      }
    });

    request.on("error", reject);
  });
}

const server = http.createServer(async (request, response) => {
  try {
    if (request.method === "GET" && request.url === "/health") {
      sendJson(response, 200, {
        ok: true,
        service: VERIFIER_SERVICE_NAME,
        port: DEFAULT_PORT
      });
      return;
    }

    if (request.method === "POST" && request.url === "/run-scenario") {
      const payload = await readJson(request);

      if (typeof payload.scenarioId !== "string" || !payload.model || typeof payload.model !== "object") {
        sendJson(response, 400, {
          error: "Expected JSON payload with scenarioId and model."
        });
        return;
      }

      const result = await runScenario({
        scenarioId: payload.scenarioId,
        runId: typeof payload.runId === "string" && payload.runId ? payload.runId : "adhoc",
        model: payload.model,
        generation: payload.generation && typeof payload.generation === "object" ? payload.generation : {}
      });
      sendJson(response, 200, result);
      return;
    }

    sendJson(response, 404, {
      error: "Not found."
    });
  } catch (error) {
    sendJson(response, 500, {
      error: error instanceof Error ? error.message : "Unknown verifier error."
    });
  }
});

server.listen(DEFAULT_PORT, "0.0.0.0", () => {
  console.log(`HermesAgent-20 verifier listening on http://0.0.0.0:${DEFAULT_PORT}`);
});
