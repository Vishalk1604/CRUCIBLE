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

## Shape of the pipeline

```
ERP text → extract (rules → llm) → normalize → assay (4 verifiers)
         → fuse (learned scorer) → certify (conformal) → dial / review queue
```

Key modules: `pipeline.py` (orchestration), `api/session.py` (holds everything in memory
so the dial re-thresholds instantly), `certify/conformal.py` (the bound),
`certify/scorer.py` (signal fusion), `assay/` (the four verifiers).

## Working agreements

- `uv` for dependencies, `ruff format` + `ruff check` before every commit, `pytest` green.
- Commit messages explain **why**, including approaches that failed and what they cost.
  Several commits here record wrong turns deliberately; keep that habit.
- Report numbers honestly, with their caveats. A negative result belongs in the ablation
  table, not in the bin.

## Current state and what is next

See `docs/RESULTS.md` for measured numbers and `docs/HANDOFF.md` for setup and the
prioritised next steps.

The short version: four verifiers, AUROC 0.928, guarantee holds at every alpha it will
issue one, and 2% is unreachable by **exactly one error** — a calibration sample size
problem, not a verifier problem. More data therefore beats a fifth verifier, which makes
the Icecat ingestion the highest-value work.
