# Changelog

Auto-generated from commit subjects by [git-cliff](https://git-cliff.org/) on tag
push. Click any commit SHA below to see the full message body (why / how /
validation data) — those live in `git log`, not here. Don't hand-edit; the file
is regenerated on every tag. See the
[GitHub Release pages](https://github.com/noonghunna/benchlocal-cli/releases) for
the same content per-version.

---

## v0.10.0 — 2026-09-23


### ✨ Features

- feat(runner): stop a pack when the endpoint is gone instead of retrying every scenario (#160) ([7dfb5a9](https://github.com/noonghunna/benchlocal-cli/commit/7dfb5a90bbcf880bc86e1b218de5d1c78ce4857b))
- feat(sandbox): make hermes stream-timeout passthrough opt-in (#157) ([b8ab532](https://github.com/noonghunna/benchlocal-cli/commit/b8ab532599fc5e54ddf450d414a281e14d67bf74))
- feat(runner): opt-in streaming with a stall clock, and hermes stall detection (#157) ([6081d20](https://github.com/noonghunna/benchlocal-cli/commit/6081d2049a2cb99bebff1aac26fe4ec794a94644))
- feat(report): report token usage per pack and for the run (#147) ([951b600](https://github.com/noonghunna/benchlocal-cli/commit/951b6001a6031c01d9a2b2f63ac253d9fc262af5))
- feat(report): record wall-clock duration per pack and for the run (#146) ([f4b5217](https://github.com/noonghunna/benchlocal-cli/commit/f4b5217a76b8acaf9654ae699015a76afbb54bbf))
- feat(runner): --budget-from-timeout derives the token ceiling from clock x measured TPS (#145) ([0d33041](https://github.com/noonghunna/benchlocal-cli/commit/0d3304163aed2c283f887497ff261537d729583a))
- feat(report): surface runaway failures separately from verifier_fail ([960e007](https://github.com/noonghunna/benchlocal-cli/commit/960e0071dd15382bc3ec892503141b2f79b40e54))
- feat(run): detect thinking arms that didn't take effect (#126) ([21f99d3](https://github.com/noonghunna/benchlocal-cli/commit/21f99d3cb3ff176a5c1a779b5cec00bb8215d80b))
- feat(report): add native Results Card v2 output ([fcca801](https://github.com/noonghunna/benchlocal-cli/commit/fcca80150da9f685f50087f56bfba61b644540c1))
- feat(run): add retry, timeout, and reasoning guards ([d52a52a](https://github.com/noonghunna/benchlocal-cli/commit/d52a52afc82a680f4590a27cad6a6715d735ed42))


### 🐛 Bug fixes

- fix(report): flag sparse thinking arms and stop calling every runaway a budget artifact ([fc217cb](https://github.com/noonghunna/benchlocal-cli/commit/fc217cb907497524220d556a47dc83bcf5f5d5e4))
- fix(packs): judge IF-11 and SO-11 against their prompts, not upstream's rendering (#143) ([241a5cc](https://github.com/noonghunna/benchlocal-cli/commit/241a5cc0540e5c47d2ace04c89669c3039af042b))
- fix(packs): grade the 14 vacuous IF/SO scenarios with ports of upstream's evaluators (#143) ([fc7fc86](https://github.com/noonghunna/benchlocal-cli/commit/fc7fc86e7b9513a518afbf472c4ebcccfc0935e4))
- fix(hermes): make the agent watchdog honor the episode cap (completes #149) ([9e15dae](https://github.com/noonghunna/benchlocal-cli/commit/9e15dae087793656c8538c6ad22b059283060460))
- fix(runner): stop a multi-turn episode at a turn cut off by the token cap ([924de7e](https://github.com/noonghunna/benchlocal-cli/commit/924de7e5aaebc9aade70cbb88ca4d47c5c0bb7c1))
- fix(runner): classify engine rejection of model output as model_output_unparseable (#152) ([582c1e4](https://github.com/noonghunna/benchlocal-cli/commit/582c1e408e3e37db6eb73986257ae4fef80afb0c))
- fix(sandbox): let --timeout-per-case reach the hermes episode cap and log effective clocks ([2d1019a](https://github.com/noonghunna/benchlocal-cli/commit/2d1019ac81a7a37bda570c03f9ff1ade33756c4a))
- fix(release): reconcile version with v0.9.9 and guard the tag (#141) ([#141](https://github.com/noonghunna/benchlocal-cli/pull/141) by @noonghunna)
- fix(dataextract): enforce top-level shape only where the prompt declares one (#140) ([#140](https://github.com/noonghunna/benchlocal-cli/pull/140) by @noonghunna)
- fix(dataextract): score content on a shape mismatch instead of reporting 0% (#139) ([#139](https://github.com/noonghunna/benchlocal-cli/pull/139) by @noonghunna)
- fix(packs): declare field types in DataExtract, name the SO-07 wrapper (v1.1.0) ([7a16e12](https://github.com/noonghunna/benchlocal-cli/commit/7a16e12670cd7075001e65fbf52604bbd854f187))
- fix(cli-40): stop sending contradictory thinking params in the no-thinking arm ([aeeaa58](https://github.com/noonghunna/benchlocal-cli/commit/aeeaa581ba926a0f16a458a564512290008e104d))
- fix(reasonmath): accept combined unsat answer forms in RM-04 ([c120213](https://github.com/noonghunna/benchlocal-cli/commit/c12021375857f1a4c594ff4bba1baea1d40f3caf))
- fix(sandbox): treat an unpaired trailing fence as a closer, not an opener ([5aaa2d9](https://github.com/noonghunna/benchlocal-cli/commit/5aaa2d93a6d5005efed5d007a5e5c5d321153c40))
- fix: resolve model-specific thinking controls ([c6a1c75](https://github.com/noonghunna/benchlocal-cli/commit/c6a1c7537d5de233a3d70433340bfe0b9f8c820c))
- fix(reasoning): make code sandbox scoring content-first ([8113c2e](https://github.com/noonghunna/benchlocal-cli/commit/8113c2ede16d9293e78d23a8691699c9bf2d51bc))
- fix(sandbox): add preserve_reasoning_history to verify_multiturn_start ([3931d04](https://github.com/noonghunna/benchlocal-cli/commit/3931d04e20b052a28a56a288cdd7e066fe4fb6d1))


### 📝 Documentation

- docs: prompt-verifier alignment audit across all packs (2026-08-12) ([67ddb1e](https://github.com/noonghunna/benchlocal-cli/commit/67ddb1ebd2c8908aa564116485092d1f1556745e))
- docs: failure-triage policy — model miss is the default label ([10b0d9b](https://github.com/noonghunna/benchlocal-cli/commit/10b0d9bfa0c4804d4b7d5abf85ac4f5cf6f1c21c))
- docs: reasoning-pack provenance, drift record, and extend-don't-regenerate convention ([5dd58e7](https://github.com/noonghunna/benchlocal-cli/commit/5dd58e76a01940cf8ec564ce6ac7ae596d4e1608))


### 🧹 Other

- Enforce network_isolated instead of only declaring it (#142) ([#142](https://github.com/noonghunna/benchlocal-cli/pull/142) by @moritzburgard)


### 🧹 Refactoring + maintenance

- chore(release): bump version 0.9.9 -> 0.10.0 ([cdeefcf](https://github.com/noonghunna/benchlocal-cli/commit/cdeefcf611b10d09be6fd891150bd743685d11dc))
- chore: ignore benchlocal-runs/, the runner's own sandbox-log root ([1d27ae1](https://github.com/noonghunna/benchlocal-cli/commit/1d27ae19bc32ba047e9549d039c89175735f5e16))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.10.0`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.9...v0.10.0)
## v0.9.9 — 2026-07-22


### ✨ Features

- feat(run): add failure retry diagnostics (#107) ([348e2ad](https://github.com/noonghunna/benchlocal-cli/commit/348e2ad4a68765af4dc8d2355fa0264d65b9d9d3))


### 🐛 Bug fixes

- fix(cloud): span minute-window 429 throttles (#106) ([5181327](https://github.com/noonghunna/benchlocal-cli/commit/518132763d4368ae1f1818c3b3b25140c5d440c7))


### 🧹 Other

- Pin hermes-agent by default; decouple timeout clock from token budget (#105) ([#105](https://github.com/noonghunna/benchlocal-cli/pull/105) by @noonghunna)



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.9`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.8...v0.9.9)
## v0.9.8 — 2026-07-21


### ✨ Features

- feat(run): add scenario-level persistence and resume (#82) (#85) ([#85](https://github.com/noonghunna/benchlocal-cli/pull/85) by @noonghunna)
- feat(run): add scenario-level selection (#83) (#84) ([#84](https://github.com/noonghunna/benchlocal-cli/pull/84) by @noonghunna)


### 🐛 Bug fixes

- fix(reasonmath): make answers authoritative ([b5fd6fb](https://github.com/noonghunna/benchlocal-cli/commit/b5fd6fbbd3e999edb95a0f33fe3dd6793d42ec3f))
- fix(cli-40): grade stated outcomes ([3ad43cb](https://github.com/noonghunna/benchlocal-cli/commit/3ad43cb770913769bce6a272c1da36cb4e3cacf9))
- fix(hermes): support thinking-only endpoints (#86) ([#89](https://github.com/noonghunna/benchlocal-cli/pull/89) by @noonghunna)
- fix(hermes): close verifier fidelity gaps (#90-#94) ([#95](https://github.com/noonghunna/benchlocal-cli/pull/95) by @noonghunna)
- fix(runner): honest sandbox-unavailable hint (build tooling isn't pip-packaged) (#69) ([#69](https://github.com/noonghunna/benchlocal-cli/pull/69) by @noonghunna)


### 📝 Documentation

- docs: warn that managed endpoints may ignore the thinking flag (#71) ([#71](https://github.com/noonghunna/benchlocal-cli/pull/71) by @noonghunna)
- docs(readme): add "Running against a cloud / managed endpoint" section (#70) ([#70](https://github.com/noonghunna/benchlocal-cli/pull/70) by @noonghunna)


### 🧹 Other

- fix sandbox model-call controls ([402aa31](https://github.com/noonghunna/benchlocal-cli/commit/402aa31a46288720e32cc9391f8ac6b5f9b18fad))
- Bump version 0.9.7 -> 0.9.8 (scenario selection #84 + resume #85) ([72bee2a](https://github.com/noonghunna/benchlocal-cli/commit/72bee2a8fce7352fde9a5b3cd9ca316b6879eb38))
- Fix ReasonMath trace scoring for reasoning-channel responses ([#81](https://github.com/noonghunna/benchlocal-cli/pull/81) by @noonghunna)
- Fix quality harness fairness audit issues ([#79](https://github.com/noonghunna/benchlocal-cli/pull/79) by @noonghunna)



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.8`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.7...v0.9.8)
## v0.9.7 — 2026-06-15


### ✨ Features

- feat(cli): rename --reasoning -> --reasoning-packs; keep --reasoning as deprecated alias (#65) (#67) ([#67](https://github.com/noonghunna/benchlocal-cli/pull/67) by @noonghunna)
- feat(grader-fidelity): token_limit failure_mode (#61) + --negative-control probe (#62 tier-1) (#66) ([#66](https://github.com/noonghunna/benchlocal-cli/pull/66) by @noonghunna)
- feat(cloud): full cloud-endpoint support — pacing + 429 retry + sandbox-key forwarding (--full /150) (#64) ([#64](https://github.com/noonghunna/benchlocal-cli/pull/64) by @noonghunna)
- feat(cloud): Bearer auth (--api-key) + spend guard (--max-total-tokens) for cloud endpoints (#63) ([#63](https://github.com/noonghunna/benchlocal-cli/pull/63) by @noonghunna)


### 📝 Documentation

- docs(README): document the inspect subcommand; fix drifted Failure-breakdown sample ([678d6e8](https://github.com/noonghunna/benchlocal-cli/commit/678d6e871061ecd96b55986463689e3ea5b50c2d))
- docs(README): document per-case timeout sizing (precedence, probe, flags) ([9531dd2](https://github.com/noonghunna/benchlocal-cli/commit/9531dd2b77921c70e05ed24c12d9bd4e897a1405))


### 🧹 Other

- Fail fast on timeouts + unreachable-endpoint probe in _post_chat (#58) (#60) ([#60](https://github.com/noonghunna/benchlocal-cli/pull/60) by @noonghunna)
- Stream Aider per-exercise progress (#57) ([#57](https://github.com/noonghunna/benchlocal-cli/pull/57) by @noonghunna)
- Fail fast on unreachable sandbox endpoints (#56) ([#56](https://github.com/noonghunna/benchlocal-cli/pull/56) by @noonghunna)
- Apply thinking-aware timeout budget unconditionally (supersedes #55) (#59) ([#59](https://github.com/noonghunna/benchlocal-cli/pull/59) by @noonghunna)
- docs/README: document sandboxed-packs networking (closes #52) (#53) ([#53](https://github.com/noonghunna/benchlocal-cli/pull/53) by @noonghunna)
- Auto-resolve Hermes loopback endpoints (#50) ([#50](https://github.com/noonghunna/benchlocal-cli/pull/50) by @noonghunna)
- Scale agentic timeouts by measured TPS (#48) ([#48](https://github.com/noonghunna/benchlocal-cli/pull/48) by @noonghunna)
- Disambiguate CLI agent loop exhaustion (#47) ([#47](https://github.com/noonghunna/benchlocal-cli/pull/47) by @noonghunna)
- Honor agentic pack timeout defaults (#43) ([#43](https://github.com/noonghunna/benchlocal-cli/pull/43) by @noonghunna)
- Use deterministic thinking sampler for Hermes agent (#42) ([#42](https://github.com/noonghunna/benchlocal-cli/pull/42) by @noonghunna)
- Run Aider benchmark from pinned checkout (#44) ([#44](https://github.com/noonghunna/benchlocal-cli/pull/44) by @noonghunna)
- Fix deterministic sampler and verifier fidelity (#38) ([#38](https://github.com/noonghunna/benchlocal-cli/pull/38) by @noonghunna)


### 🧹 Refactoring + maintenance

- chore(release): v0.9.7 ([d515b85](https://github.com/noonghunna/benchlocal-cli/commit/d515b853bac32c0a365ed6adb6c2071c48f1b258))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.7`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.6...v0.9.7)
## v0.9.6 — 2026-05-24


### ✨ Features

- feat(cli): add --max-tokens global length cap (#28) ([0e7b35e](https://github.com/noonghunna/benchlocal-cli/commit/0e7b35e46676ae3b08ee5ddc58ac887a3dbb0efa))
- feat: honor pack-level thinking defaults (#26) ([#26](https://github.com/noonghunna/benchlocal-cli/pull/26) by @noonghunna)


### 🐛 Bug fixes

- fix(scoring): align deterministic verifier fidelity ([8606e7f](https://github.com/noonghunna/benchlocal-cli/commit/8606e7f333aa26bb8caa6089aa59431bfdd28711))
- fix(scoring): stop two grader false-negatives (toolcall dependent chains, reasonmath key synonyms) ([ed086e5](https://github.com/noonghunna/benchlocal-cli/commit/ed086e5a8ecc6c9508920b8322962d0f53d7a20f))
- fix(gpqa pack): base max_tokens 2048 → 4096 (no-think arm truncates otherwise) ([b652a3a](https://github.com/noonghunna/benchlocal-cli/commit/b652a3aad491d01026a953f4e33c53347b8e023f))


### 🧹 Other

- Document reasoning suite ([3d99d51](https://github.com/noonghunna/benchlocal-cli/commit/3d99d51ea38bb99f2eb491e8ebfaf349d12e891e))
- Add reasoning benchmark packs ([2c1d02d](https://github.com/noonghunna/benchlocal-cli/commit/2c1d02d5a421eac4f66c7b893ad0a1a448ece1a2))


### 🧹 Refactoring + maintenance

- chore(release): v0.9.6 ([7419d7c](https://github.com/noonghunna/benchlocal-cli/commit/7419d7c90eb529e11ad0d8ff7ffc234046d7fe3e))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.6`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.5...v0.9.6)
## v0.9.5 — 2026-05-23


### ✨ Features

- feat: incremental progress output (#23) (#24) ([#24](https://github.com/noonghunna/benchlocal-cli/pull/24) by @noonghunna)
- feat(cli): add --sampling-from-server to inherit serve-side sampling defaults (#21) (#22) ([#22](https://github.com/noonghunna/benchlocal-cli/pull/22) by @noonghunna)
- feat(cli): add opt-in --temperature/--top-p/--top-k/--min-p/--repeat-penalty sampling overrides (#19) (#20) ([#20](https://github.com/noonghunna/benchlocal-cli/pull/20) by @noonghunna)


### 🐛 Bug fixes

- fix(aider): default to single-thread and score partial timeouts (#18) ([#18](https://github.com/noonghunna/benchlocal-cli/pull/18) by @noonghunna)
- fix(aider): qualify only known litellm providers (#16) ([#16](https://github.com/noonghunna/benchlocal-cli/pull/16) by @noonghunna)
- fix(scoring): sanitize leaked reasoning tags (#14) ([#14](https://github.com/noonghunna/benchlocal-cli/pull/14) by @noonghunna)
- fix(runner): retry transient model endpoint failures (#12) ([#12](https://github.com/noonghunna/benchlocal-cli/pull/12) by @noonghunna)
- fix(cli): default sandbox logs for sandboxed runs (#10) ([#10](https://github.com/noonghunna/benchlocal-cli/pull/10) by @noonghunna)
- fix: durable sandbox logs (#6) + aider timeout/partial-headline (#3) (#7) ([#7](https://github.com/noonghunna/benchlocal-cli/pull/7) by @noonghunna)
- fix(sandbox): pin jest@29.7.0 in aider-polyglot Dockerfile (#5) ([#5](https://github.com/noonghunna/benchlocal-cli/pull/5) by @noonghunna)


### 📝 Documentation

- docs: README — document --temperature + --sampling-from-server sampling flags ([cc4b410](https://github.com/noonghunna/benchlocal-cli/commit/cc4b410c73295897982a738cfb444031cd38faba))


### 🧹 Refactoring + maintenance

- chore(vendor): refresh bench packs to latest upstream (#8) ([#8](https://github.com/noonghunna/benchlocal-cli/pull/8) by @noonghunna)



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.5`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.4...v0.9.5)
## v0.9.4 — 2026-05-15


### ✨ Features

- feat(vendor): bump BugFind-15 to 1.0.1 ([9376923](https://github.com/noonghunna/benchlocal-cli/commit/937692320b2d57a6f1c5d0ba7ad56d763f80a96c))


### 🧹 Refactoring + maintenance

- chore(cliff): skip auto-regen bot commits in changelog parser ([dc5d605](https://github.com/noonghunna/benchlocal-cli/commit/dc5d6058afb9b3d2a39dae491ff38aa68c0e4ad1))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.4`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.3...v0.9.4)
## v0.9.3 — 2026-05-10


### 🧹 Refactoring + maintenance

- chore(changelog): subject-only rendering + version 0.9.3 ([f0107e9](https://github.com/noonghunna/benchlocal-cli/commit/f0107e9dc72813b12f7237a3d720e3bb7c948af8))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.3`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.2...v0.9.3)
## v0.9.2 — 2026-05-10


### 🐛 Bug fixes

- fix(hermes): full localhost resolve for sandbox endpoint + drop drifted persist_session kwarg ([9c1566f](https://github.com/noonghunna/benchlocal-cli/commit/9c1566f53856d56f9bb46dd7861eb01a5bc3608b))


### 🧹 Other

- release: v0.9.2 — hermes localhost fix + cliff Option A automation ([22ad4e2](https://github.com/noonghunna/benchlocal-cli/commit/22ad4e2c75497ffeb4550ea2d3fc3cbd66d3c152))


### 🧹 Refactoring + maintenance

- chore(changelog): automate CHANGELOG + release notes from commits via cliff (Option A) ([5497b16](https://github.com/noonghunna/benchlocal-cli/commit/5497b16e3e7a09bc6367aca78815891d28e5fc51))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.2`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.1...v0.9.2)
## v0.9.1 — 2026-05-10


### 🐛 Bug fixes

- fix(aider-polyglot): bump SUBPROCESS_TIMEOUT_S 1500s → 2700s ([913f4d5](https://github.com/noonghunna/benchlocal-cli/commit/913f4d5d74b24257d012661137f0e1053c3f0423))


### 📝 Documentation

- docs: remove codex briefs + thorough README v0.9 rewrite ([b8eb442](https://github.com/noonghunna/benchlocal-cli/commit/b8eb442f49853b4fda9b0d3e606e2f8b110ad553))
- docs(readme): surface aider-polyglot-30 (v0.9) headline pack ([148974e](https://github.com/noonghunna/benchlocal-cli/commit/148974e79c92bc1465ab1af24731aeae1bd35d8d))


### 🧹 Other

- release: v0.9.1 — public release patch ([1f7c7b0](https://github.com/noonghunna/benchlocal-cli/commit/1f7c7b0041530a7b351ab72a7367849a14f03684))
- docs + runner: pre-publish cleanup ([a9c682b](https://github.com/noonghunna/benchlocal-cli/commit/a9c682b6de71e19060a41b3f73529ab52738f1fc))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.1`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.9.0...v0.9.1)
## v0.9.0 — 2026-05-10


### ✨ Features

- v0.9.0: Aider Polyglot lite — first eval-expansion slice ([058bc65](https://github.com/noonghunna/benchlocal-cli/commit/058bc6567698641b3db48c82ae853460e893e28e))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.9.0`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.8.1...v0.9.0)
## v0.8.1 — 2026-05-10


### ✨ Features

- v0.8.1: inspect --diff + inspect --logs (deferred Phase B.5) ([75cd902](https://github.com/noonghunna/benchlocal-cli/commit/75cd9026e81775d3321c2d1760298f11004d89a3))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.8.1`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.8.0...v0.8.1)
## v0.8.0 — 2026-05-10


### ✨ Features

- v0.8.0: diagnostic tooling — delta, inspect, history ([3370d0c](https://github.com/noonghunna/benchlocal-cli/commit/3370d0c05c865add3ba7130416d71e417437bbba))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.8.0`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.7.4...v0.8.0)
## v0.7.4 — 2026-05-10


### ✨ Features

- v0.7.4: Hermes grading-parity via upstream Node grader ([5322624](https://github.com/noonghunna/benchlocal-cli/commit/53226248a4bb9ec0245d7b01d6caf29949e4c81d))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.7.4`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.7.3...v0.7.4)
## v0.7.3 — 2026-05-10


### ✨ Features

- v0.7.3: Hermes upstream-runtime delegation + real-model A/B ([843bd4f](https://github.com/noonghunna/benchlocal-cli/commit/843bd4f72522ad96f71ea3e97411baa499190312))


### 📝 Documentation

- docs: v0.8 brief — diagnostic tooling (delta + inspect + history) ([259dd3f](https://github.com/noonghunna/benchlocal-cli/commit/259dd3f267fb1fca8142751ff68722ed88fec3b8))
- docs: v0.7.3 brief — make image-baked path testable + cross-validatable ([721a9dc](https://github.com/noonghunna/benchlocal-cli/commit/721a9dcbe2bd57007e65d19eea35705d561597e4))
- docs: v0.7.3 brief — bind-mount host hermes-agent (user already has local install) ([e873119](https://github.com/noonghunna/benchlocal-cli/commit/e87311987cb1063814cbb8ac791734efe212188f))
- docs: v0.7.3 brief — Hermes upstream-runtime delegation + Phase A risk surfaced ([1d2d42e](https://github.com/noonghunna/benchlocal-cli/commit/1d2d42ec5d43b8b96ea2faf37e0f38bbcaf83773))
- docs(roadmap): add v0.9+ implementation-pattern section + clean up stale Inspect AI ref ([578d113](https://github.com/noonghunna/benchlocal-cli/commit/578d113c02014ffec6e2cd2168f0d4b68af2d0f7))
- docs(roadmap): incorporate Codex review — Aider Polyglot for code gen, IDE-agent safety slice ([88b2d15](https://github.com/noonghunna/benchlocal-cli/commit/88b2d15510b0b2313a070475e92b86ec0954a321))
- docs(roadmap): nuance the Inspect AI position — port vs canonical home ([be043d3](https://github.com/noonghunna/benchlocal-cli/commit/be043d3f7b2cfb0d07a55efca49f64c6f1beab07))
- docs: roadmap update — promote Hermes upstream-runtime wiring to v0.7.3, demote Inspect AI ([74b2679](https://github.com/noonghunna/benchlocal-cli/commit/74b26792df8eac3bc676acd4338f937211c36af6))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.7.3`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.7.2...v0.7.3)
## v0.7.2 — 2026-05-10


### ✨ Features

- v0.7.2: post-run forensics — verifier_trace, sandbox container logs, multi-turn conversation ([76f8b30](https://github.com/noonghunna/benchlocal-cli/commit/76f8b300facadc8627c1abdb0740ae4432539877))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.7.2`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.7.1...v0.7.2)
## v0.7.1 — 2026-05-10


### ✨ Features

- feat(runner): drive sandbox multi-turn scenarios ([fc20a34](https://github.com/noonghunna/benchlocal-cli/commit/fc20a3427498760db6a6895ea88fd37f918c4bd4))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.7.1`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.7.0...v0.7.1)
## v0.7.0 — 2026-05-10


### ✨ Features

- feat(sandboxes): adapt to upstream verifier runtimes ([0c726f8](https://github.com/noonghunna/benchlocal-cli/commit/0c726f844641e655fb1a5be3090d6eb9e2e7f5cd))
- feat(packs): expose upstream verifier metadata ([5ffba36](https://github.com/noonghunna/benchlocal-cli/commit/5ffba36a17c0a0aef3bd68b31300e11f7ef02948))
- feat(vendor): sync upstream verifier runtimes ([027263e](https://github.com/noonghunna/benchlocal-cli/commit/027263ee1615ffbcbd0e065453ce598397872a24))
- feat(cli): --sandboxed-only flag for verifier debug iteration ([12d7be4](https://github.com/noonghunna/benchlocal-cli/commit/12d7be4123443d23d27f9dbeccc6e1c60357dc6a))


### 🐛 Bug fixes

- fix(cli): pre-create /workspace with verifier ownership instead of CLI40_WORKSPACE_DIR override ([bc52a32](https://github.com/noonghunna/benchlocal-cli/commit/bc52a325ad1383515b82dd2088e4f67508f6eb36))
- fix(sandboxes): exception handling + bash -c routing + multi-line extraction ([c5e1dbd](https://github.com/noonghunna/benchlocal-cli/commit/c5e1dbd5c7d1078d60af6ab0126ab0266c08da8c))


### 📝 Documentation

- docs: v0.7.1 brief — runner-side multi-turn delegation (unblocks public flip) ([8a121de](https://github.com/noonghunna/benchlocal-cli/commit/8a121deb90033220df0156f270b8e2c4d36228d6))
- docs: --audit as the v0.9+ release-gate mode name (avoid --full/--everything overlap) ([984bf68](https://github.com/noonghunna/benchlocal-cli/commit/984bf68c2c84197263f3518781f697b4d4cccd89))
- docs: promote diagnostic tooling above further evals + expansion-order rationale ([d0678c9](https://github.com/noonghunna/benchlocal-cli/commit/d0678c95ed77fa55b036bf490efccb942b240e10))
- docs: report v0.7 verifier-runtime lift ([4d911f3](https://github.com/noonghunna/benchlocal-cli/commit/4d911f33da0c74118b76fbe3dd187d1036d1b09a))
- docs: v0.7 brief + roadmap update — fixture-gap closure for public release ([44a5be0](https://github.com/noonghunna/benchlocal-cli/commit/44a5be06cfff69b8d16ff9b70605413b743a9faa))


### 🛠️ Tooling + CI

- ci: comment out [remote.github] in cliff.toml until repo flips public ([470d6b1](https://github.com/noonghunna/benchlocal-cli/commit/470d6b1751503c11adf6b50e1008ddc31944e9c9))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.7.0`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.6.0...v0.7.0)
## v0.6.0 — 2026-05-09


### ✨ Features

- feat(sandboxes): replace shape checks with v0.6 verifiers ([22466ff](https://github.com/noonghunna/benchlocal-cli/commit/22466ff95e1742dca0d50191d7be6d177eaa1b16))
- feat(packs): add raw sandbox scenario metadata ([749eacb](https://github.com/noonghunna/benchlocal-cli/commit/749eacb1ddba9b34427ef375ed75ec756fb0891e))


### 📝 Documentation

- docs: report v0.6 verifier lift ([b3005f4](https://github.com/noonghunna/benchlocal-cli/commit/b3005f4037043e428d0c9350280b8cddb9fdbded))
- docs: ROADMAP.md + v0.5-deltas section in v0.6 brief ([7337cd7](https://github.com/noonghunna/benchlocal-cli/commit/7337cd753cb4f9bf2d41fbfb3eb9a16b7a91d53b))
- docs: v0.6 brief — real verifier parity for sandboxed packs ([7d737ba](https://github.com/noonghunna/benchlocal-cli/commit/7d737ba2192f0a07feba1d742a801504856bde4c))


### 🛠️ Tooling + CI

- ci: drop git-cliff --github-repo flag (private repo can't access GitHub API) ([06c8ef6](https://github.com/noonghunna/benchlocal-cli/commit/06c8ef61f446ffd812ef4f45c8bb77049b7612b8))


### 🧪 Tests

- test(sandboxes): cover v0.6 verifier paths ([b45519f](https://github.com/noonghunna/benchlocal-cli/commit/b45519f9375d5ee07433aeb8cac03eadbbf291fe))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.6.0`] · [Full diff](https://github.com/noonghunna/benchlocal-cli/compare/v0.5.0...v0.6.0)
## v0.5.0 — 2026-05-09


### ✨ Features

- v0.5: --full enables sandboxed by default + reasonmath prompt fix ([eb7ddb0](https://github.com/noonghunna/benchlocal-cli/commit/eb7ddb0b1308530f564bad08722b2e0a5873199a))
- feat(hermes): implement sandbox verifier endpoint ([34e6388](https://github.com/noonghunna/benchlocal-cli/commit/34e63880d7f6f3aefd5769088fc01ecd87b38550))
- feat(cli): implement sandbox verifier endpoint ([c9cac81](https://github.com/noonghunna/benchlocal-cli/commit/c9cac8171054044288b8c53d90b616935d7631a6))
- feat(bugfind): implement sandbox verifier endpoint ([b68af8f](https://github.com/noonghunna/benchlocal-cli/commit/b68af8f1659841b77ae34a262720badc2358b3ed))
- feat(sandbox): integrate HTTP verifier clients ([9a6f3f9](https://github.com/noonghunna/benchlocal-cli/commit/9a6f3f94bf8b20e4c084c602343cade0cd99f0a5))
- feat: add reasoning-aware runner flags ([3f7c4ec](https://github.com/noonghunna/benchlocal-cli/commit/3f7c4ec405fbb734a52e8126d2acdbfa8928ad70))
- feat(packs): default generated packs to thinking off ([f8187ad](https://github.com/noonghunna/benchlocal-cli/commit/f8187ad0533d0cc4c607d5cb9918ca7ea507dff3))
- feat(extractor): preserve ToolCall reference date metadata ([11ea4ec](https://github.com/noonghunna/benchlocal-cli/commit/11ea4ec5e4c1eb9cff3f2a7d4fed07ad7f07cd1d))
- feat(packs): regenerate JSONL from vendor sources ([36a7c18](https://github.com/noonghunna/benchlocal-cli/commit/36a7c18bac67e4f48c845a2fc117bd71c1adc504))
- feat(packs): support extractor-generated assertions ([093d0e0](https://github.com/noonghunna/benchlocal-cli/commit/093d0e0850cdc057d41aad86d9fbaa8eed83bc65))
- feat(extractor): add Node build-packs generator ([d682dcc](https://github.com/noonghunna/benchlocal-cli/commit/d682dccd12939c45de553b9f82d52d96bbd83d62))
- feat(vendor): scaffold vendor mirrors and sync script ([93a299c](https://github.com/noonghunna/benchlocal-cli/commit/93a299c63641e809c94cc7210b6bd20d52320a95))
- feat: v0.1 implementation complete; see docs/CODEX_REPORT.md ([928291f](https://github.com/noonghunna/benchlocal-cli/commit/928291f09ca0e0a4135cafe86aa20a6e893ad867))
- feat: vendor BenchLocal JSONL packs ([689d907](https://github.com/noonghunna/benchlocal-cli/commit/689d9071805e9b4842d2ffb202598ea342da829d))
- feat: implement deterministic scorers ([276e70b](https://github.com/noonghunna/benchlocal-cli/commit/276e70b26de812155d7a2536286337452cb36fdd))
- feat: implement core runner and CLI ([14de749](https://github.com/noonghunna/benchlocal-cli/commit/14de74988357e6e58fda935a1286a5e146574f7b))


### 🐛 Bug fixes

- fix: apply thinking token budget to scenario requests ([7d79840](https://github.com/noonghunna/benchlocal-cli/commit/7d798409bece2fc03265189db0d30bc335784cd0))


### 📝 Documentation

- docs: report v0.4 sandbox implementation ([6a2656e](https://github.com/noonghunna/benchlocal-cli/commit/6a2656e16536d7e667f9def4285c1105b8c7e095))
- docs: v0.4 brief — unified sandbox infrastructure (BugFind + CLI + HermesAgent) ([e5bb8ff](https://github.com/noonghunna/benchlocal-cli/commit/e5bb8ffbf7bbd3f8bd3542f7b6259a139036d1d6))
- docs: report v0.3 reasoning-model handling ([60eb461](https://github.com/noonghunna/benchlocal-cli/commit/60eb46177851c9f801d3a59963e130de7bfba479))
- docs: clarify thinking token budget behavior ([f399bf8](https://github.com/noonghunna/benchlocal-cli/commit/f399bf8dca93f5ba3c6a171b6ef3523bce4e8d03))
- docs: document reasoning-model defaults ([f7544d2](https://github.com/noonghunna/benchlocal-cli/commit/f7544d260c5bc1086965157c9874936d7b7ed0f8))
- docs: add v0.3 brief — reasoning-model handling (default thinking=off + --enable-thinking flag + reasoning_content reader) ([a0ca3a5](https://github.com/noonghunna/benchlocal-cli/commit/a0ca3a554fffbf4914bcd46aeab78fc1aa63010a))
- docs: report v0.2 vendor extractor completion ([9ce2b52](https://github.com/noonghunna/benchlocal-cli/commit/9ce2b529dcab5c55ab0df76c6ab27f4c5c966527))
- docs: document v0.2 vendor sync workflow ([812a715](https://github.com/noonghunna/benchlocal-cli/commit/812a7158f20dcb5de5a784fce6c6864c3c182eaa))
- docs: add v0.2 brief — vendor/ + Node extractor for verbatim upstream fidelity + future-proof re-sync ([23e9158](https://github.com/noonghunna/benchlocal-cli/commit/23e915803a9e7c274cb76cd9810258975d2256d4))
- docs: add async report-back protocol for Codex handoff (questions / completion / report template) ([62a4dcf](https://github.com/noonghunna/benchlocal-cli/commit/62a4dcf860d039538738db3f299b434f297ab9ef))


### 🚧 Scaffolding

- scaffolding(v0.4): build + smoke-test sandbox containers (Codex implementation pending) ([5fd35ef](https://github.com/noonghunna/benchlocal-cli/commit/5fd35ef5f0915bcfbfd00342621839553f0f475a))


### 🛠️ Tooling + CI

- ci: add git-cliff release notes (SemVer) ([d16eb60](https://github.com/noonghunna/benchlocal-cli/commit/d16eb606901ca6a88442bb8b28b27343d59db473))


### 🧪 Tests

- test: cover sandbox runner dispatch ([e3c2597](https://github.com/noonghunna/benchlocal-cli/commit/e3c2597ff261139d639c0e2b78c6cc2aa8158ada))
- test: cover reasoning request handling ([9e82a7c](https://github.com/noonghunna/benchlocal-cli/commit/9e82a7c85b84719f7e113418c760fc1928d15c2e))


### 🧹 Other

- Initial scaffolding for benchlocal-cli (Codex implementation pending) ([3546c1f](https://github.com/noonghunna/benchlocal-cli/commit/3546c1fd68c3dc352f8ff4a350d05c9e2e2df049))


### 🧹 Refactoring + maintenance

- refactor: rename scripts/ → tools/ to signal maintainer-only tooling ([b29ef5e](https://github.com/noonghunna/benchlocal-cli/commit/b29ef5eebe914a5cc4d19513b8524bc4fcf7f47e))



[Install: `pip install git+https://github.com/noonghunna/benchlocal-cli.git@v0.5.0`]

