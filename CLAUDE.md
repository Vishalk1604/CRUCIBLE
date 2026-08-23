# CRUCIBLE — context for Claude Code

Read this before changing anything. It exists so a fresh session does not re-derive
decisions that were already made, or undo them by accident.

## What this is

A hackathon entry for **UniHack**, run by **Unilog** (B2B product content and commerce for
industrial distributors). The brief: turn limited product information into rich, reliable,
commerce-ready product data, judged on structured generation, accuracy, AI validation and
explainable output.

**The thesis.** Generation is solved and commoditised — frontier models already reach ~91%
F1 on product attribute extraction, and two companies (Anglera, Claro) plus Unilog's own
HyperScale agents already ship "AI enrichment with a confidence score and a source link".
The unsolved half is that nobody can tell *which* 9–15% is wrong, so distributors still
pay people to check 100% of records and the automation saves nothing.

So Crucible does not compete on generation. It treats the model as an **untrusted
proposer**, verifies its output with external tools, and uses **conformal risk control** to
convert that evidence into a *guarantee*: a certified error rate at a chosen automation
level. The user-facing product is one dial — "maximum acceptable error rate" — and
everything else follows from it.

Do not drift back toward "better extraction". The differentiator is the proof, not the
generation.

## Non-negotiables

These are load-bearing. Changing one silently invalidates the results.

1. **A value is only as trustworthy as its evidence.** Every value carries a source span.
   Ungrounded values are discarded at extraction, not passed downstream.
2. **Verifiers are external tools, not a second opinion from the same model.** The
   literature is explicit that LLMs cannot correct themselves without external feedback.
3. **Abstention is not approval.** "Not checked" and "checked and fine" are separate
   features in the scorer. Conflating them auto-publishes exactly the values nothing
   examined.
4. **Refusing to certify is correct behaviour.** When the evidence cannot support an
   alpha, the system says so. Never invent a threshold to avoid an empty result.
5. **Never fabricate a value to satisfy a schema.** A missing value costs one review; a
   confidently wrong one costs trust in the whole catalog. This applies to the code too —
   `normalize.py` once added a canonical unit to bare numbers and thereby committed the
   exact fault the system exists to catch.
6. **Label synthetic numbers as synthetic**, everywhere they appear, including on screen.

## Traps that already cost time

- **The corpus is circular with the rule extractor.** Both read `corpus/tables.py`, so
  `extract/rules.py` scores 100% and that number is meaningless. Calibration runs the
  **model-only** path (`use_rules=False`) so the labels are real.
- **qwen3-vl:8b needs `think=False`.** With thinking on it burns ~4000 tokens and returns
  *empty content*. Disabling it took extraction from 122.7s to 2.0s.
- **With `think=False` the JSON arrives in the response's `thinking` field**, not
  `content`. Check both. See `extract/llm.py::_payload`.
- **The model does not expand abbreviations** — it returns `SS`, `600WOG`, `SCRD`
  unchanged. That is why `normalize.py` exists, and why rules run first in the cascade.
- **Do not enter `TestClient` as a context manager** in API tests. It runs the lifespan,
  which builds a real session and fires inference at Ollama.
- **8 GB VRAM.** qwen3-vl:8b at 6.1 GB already spills ~11% to CPU. Nothing bigger fits.
- **Check `ollama ps` before debugging slow extraction.** If anything else holds VRAM the
  model silently falls back to CPU (`98%/2% CPU/GPU`) and runs 3.5x slower with no error.
  A game left 4.6 GB resident and cost three calibration runs. `preflight.py` now refuses.
- **An outage can impersonate an abstention.** A dead Ollama makes `propose()` return `[]`,
  which becomes a blank cell — indistinguishable from a deliberate one. `enrich` raises
  `ExtractionUnavailable` above 5% transport failures. Never soften that into a warning.
- **`fingerprint()` excludes presentation fields.** Adding `label`/`display_uom`/`order` to
  `AttributeSpec` once changed every schema hash and invalidated the harvest cache, which
  costs ~25 minutes of inference on the next app launch. A column heading is not checkable
  content.
- **`extra` on `RawProduct` is keyed by the source file's own column names** (`E1_Brand`,
  `part_manuf_name`), not lowercase slugs. Guessing the slugs left four columns silently
  empty on every row.
- **Aggregate by `category_id`, never by `sheet_label`.** Labels are chosen to be
  human-friendly and are therefore not unique — `bit_type` and `appliance_type` are both
  "Product Name". Keying a diagnostic on the label invented a router bug that did not exist.
- **Never call vocabulary checking "LOV compliance."** The client's 161k-row LOV was not
  supplied; we measure against our own lists. Claiming their standard is the one move the
  guide says scores zero. A test asserts the phrase never appears.

## Shape of the pipeline

```
CSV in (6 cols) → ingest → route (taxonomy) → extract (rules → llm) → normalize
                → assay (6 verifiers) → fuse (learned scorer) → certify (conformal)
                → compose (deterministic prose) → emit (252-column XLSX/CSV + evidence)
```

Key modules: `enrich.py` (the catalog run), `emit/rows.py` (what goes in each of the 252
columns), `emit/compose.py` (the five descriptions), `route/` (taxonomy), `evaluate.py`
(the three metrics judges look for), `preflight.py` (refuses a run the hardware cannot
serve), `certify/conformal.py` (the bound), `assay/` (the six verifiers).

`pipeline.py` and `api/session.py` still hold the calibration path and the dial.

## Working agreements

- `uv` for dependencies, `ruff format` + `ruff check` before every commit, `pytest` green.
- Commit messages explain **why**, including approaches that failed and what they cost.
  Several commits here record wrong turns deliberately; keep that habit.
- Report numbers honestly, with their caveats. A negative result belongs in the ablation
  table, not in the bin.

## Current state and what is next

Read `docs/RESULTS.md` for measured numbers, `guide.md` for what the client is scoring, and
`DIARY.md` (append-only) for how each decision was reached.

**Where it stands.** 759 tests. `crucible enrich` turns the real input CSV into a
252-column delivery file; **61 of 252 columns** carry values on a 120-product run, including
all five description formats. Character-limit compliance 95-100%, controlled-vocabulary
compliance 76%. A product website (landing / sign-in / workspace) with live progress and
four downloads.

**Two corrections to earlier guidance in this file's own history:**

- **Icecat is dead.** `docs/HANDOFF.md` had it as priority 1. Measured 0 real matches out of
  999 part numbers across the whole daily index. Open Icecat does not cover US
  building-materials distribution. Keep `corpus/icecat.py` for the record.
- **AUROC 0.928 is stale and describes a domain this project no longer targets.** The
  synthetic corpus now reads 0.992 with six verifiers; the real catalog reads **0.662**. Do
  not quote the old figure anywhere.

**What is blocked.** Seven reference files named in the Solution Guide were never published
with the sample pack — the 200-row labelled ground truth, the 161k LOV, the 27k
manufacturer/brand list, the UOM standards. See `WINNING-PLAN.md` Step 0 for what each
would unblock and what substitutes for it meanwhile.

**Highest-value work still available without them:** the submission framing (Step 7), and
using the evaluation harness to keep improving the vocabularies it measures.
