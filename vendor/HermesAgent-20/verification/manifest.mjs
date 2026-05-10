export const HERMES_REPOSITORY_URL = "https://github.com/nousresearch/hermes-agent.git";
// Re-pinned 2026-05-09 from ea74f61 → 44cdf555 (upstream main HEAD).
// The original ea74f61 was ~6 months old and Gemma 4 31B's tool-calling
// was unreliable through it (full A/B scored 0/20 with strict toolset
// restrictions). Bumping to upstream HEAD picks up months of tool-calling
// reliability fixes that the hermes-agent v0.9 → v0.13 line received.
export const HERMES_PINNED_COMMIT = "44cdf555a83c1d8d605d095442e11efd58089533";
export const VERIFIER_SERVICE_NAME = "hermesagent20-verifier";
export const DEFAULT_PORT = Number.parseInt(process.env.PORT ?? "4010", 10);
