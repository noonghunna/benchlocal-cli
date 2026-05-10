import http from "node:http";

import { runCanonicalVariants, verifyAnswer } from "./core.mjs";

const PORT = Number.parseInt(process.env.PORT ?? "4010", 10);

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
        service: "bugfind15-verifier",
        port: PORT
      });
      return;
    }

    if (request.method === "POST" && request.url === "/verify-canonical") {
      const payload = await readJson(request);
      const scenarioIds = Array.isArray(payload.scenarioIds) ? payload.scenarioIds : [];
      const variant = typeof payload.variant === "string" ? payload.variant : "all";
      const result = await runCanonicalVariants({ scenarioIds, variant });
      sendJson(response, 200, result);
      return;
    }

    if (request.method === "POST" && request.url === "/verify-answer") {
      const payload = await readJson(request);

      if (typeof payload.scenarioId !== "string" || typeof payload.answer !== "string") {
        sendJson(response, 400, {
          error: "Expected JSON payload with string fields: scenarioId and answer."
        });
        return;
      }

      const result = await verifyAnswer(payload.scenarioId, payload.answer);
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

server.listen(PORT, "0.0.0.0", () => {
  console.log(`BugFind verifier listening on http://0.0.0.0:${PORT}`);
});
