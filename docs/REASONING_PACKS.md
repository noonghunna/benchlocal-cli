# Reasoning packs: `humaneval-plus-30` and `lcb-v6-30`

Provenance, known upstream drift, and the extension convention for the two
out-of-band reasoning code packs. Recorded per issue #121 so this stops being
private discipline.

These are the **only two packs that route through the `code-reasoning`
sandbox** (60 records total), and the only two with out-of-band reasoning.
Both run `temperature: 0`, so re-running is deterministic and yields the same
records — more `n` requires more scenarios, not more runs.

## They are NOT built by `tools/build-packs.js`

`tools/build-packs.js` only builds the vendored `stevibe/*` packs (see
[VENDOR_SYNC.md](./VENDOR_SYNC.md)). The reasoning packs have no `vendor/`
tree and no in-repo generator: they were derived once (2026-05-24, "Codex
reasoning-pack generator") from the HuggingFace datasets
`evalplus/humanevalplus` and `livecodebench/code_generation_lite`. Extending
them means re-deriving the projection by hand.

## The one non-obvious transformation

benchlocal rewrites evalplus's assertion helper when generating each `test`:

```
np.testing.assert_allclose(out, exp     ->     assert np.allclose(out, exp, rtol=1e-07
```

applied once per `test`. With that substitution, current upstream reproduces
28/30 byte-identically; without it, every generated row differs from the
shipped ones **in assertion semantics rather than data** — getting it wrong
silently changes what the verifier executes.

## Known upstream drift (as of 2026-08-02 — this keeps moving)

| Pack | Recorded | Upstream now | Detail |
|---|---|---|---|
| `lcb-v6-30` | `subset.full: 50` | **70** rows match the pack's own filter | First 30 reproduce the shipped pack exactly, in order; the extra 20 are problems added upstream since the pin. |
| `humaneval-plus-30` | 30 shipped rows | test data added to **2 of the 30** | `prompt`/`entry_point` match exactly on all 30; only `test` moved — `HumanEval/1` 8,984 → 137,328 chars, `HumanEval/28` 118,702 → 125,238. |

The LCB filter, as recorded in the pack: `release_v6`, `platform == leetcode`,
all `public_test_cases` `testtype == functional`, `contest_date >= 2025-01-01`.

Consequences:

- A regenerated pack is **not** the pack that produced any previously
  published number.
- **`subset.full` is not a reliable ceiling** for how far a pack can be
  extended — upstream grows.
- The recorded provenance IDs are HF cache/snapshot ids
  (`hf-cache-…` / `hf-snapshot-…`), **not resolved dataset snapshots**, so
  drift cannot be checked without a content diff. New packs in this lineage
  should record the resolved snapshot (revision + commit date) in metadata so
  the check becomes mechanical.

## Convention: extend with a SEPARATE pack — never regenerate in place

When more `n` is needed on a pack that already has published results, emit the
new rows as a **separate pack** (`humaneval-plus-ext134`, `lcb-v6-ext40`)
rather than regenerating the original with a larger `subset.default`. Then:

- the original stays **byte-identical**, so previously published records
  remain exactly the corpus they were reported against;
- upstream drift lands only in the new pack, where nothing has been published
  yet, and cannot contaminate a before/after comparison;
- the two are still trivially poolable for an aggregate count.

The failure mode this avoids: regenerating `humaneval-plus-30` in place to
reach n=164 would have silently changed the test suites for `HumanEval/1` and
`/28` underneath a corpus that had already been discussed in public.
