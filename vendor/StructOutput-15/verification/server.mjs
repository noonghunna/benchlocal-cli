import http from "node:http";

import { verifyAnswer } from "./core.mjs";

const PORT = Number.parseInt(process.env.PORT || "4010", 10);

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  if (req.method !== "POST" || req.url !== "/verify-answer") {
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Not found" }));
    return;
  }

  try {
    const chunks = [];
    for await (const chunk of req) {
      chunks.push(chunk);
    }

    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    const { scenarioId, answer } = body ?? {};

    if (typeof scenarioId !== "string" || typeof answer !== "string") {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "Expected scenarioId and answer strings." }));
      return;
    }

    const result = verifyAnswer(scenarioId, answer);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(result));
  } catch (error) {
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: error instanceof Error ? error.message : "Unknown validator error." }));
  }
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`StructOutput validator listening on ${PORT}`);
});
