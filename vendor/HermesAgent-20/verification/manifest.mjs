export const HERMES_REPOSITORY_URL = "https://github.com/nousresearch/hermes-agent.git";
// benchlocal-cli local compatibility pin: upstream HermesAgent-20 still points
// at an older hermes-agent commit, but v0.7.4 validated grading parity against
// v0.13-era Hermes after the generated verifier is synced into our sandbox.
export const HERMES_PINNED_COMMIT = "44cdf555a83c1d8d605d095442e11efd58089533";
export const VERIFIER_SERVICE_NAME = "hermesagent20-verifier";
export const DEFAULT_PORT = Number.parseInt(process.env.PORT ?? "4010", 10);
