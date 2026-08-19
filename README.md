# CRUCIBLE

**Certified product data enrichment for industrial catalogs — AI-generated attributes with a guaranteed error rate.**

> Everyone can generate product data. Nobody can prove it's right.

Crucible takes the messy shorthand that actually sits in a distributor's ERP, enriches it into
structured commerce-ready product data, and then does the part no one else does: it **proves how
wrong it might be**, with a statistical guarantee you could put in a contract.

```
Maximum acceptable error rate:  [ 2.0% ▾ ]

  ✔ Auto-publishing 9,724 of 12,400 attribute values  (78.4%)
    Certified ≤2.0% error at 95% statistical confidence
  ⚑ 2,676 routed to review, ranked by revenue impact
```

Drag the dial to 5% and automation climbs. Drag it to 0.5% and it falls. Risk becomes a business
lever instead of a vibe.

---

## Why this exists

Frontier models already reach ~91% F1 on product attribute extraction. Generation is a solved,
commoditized problem. The unsolved half is that **nobody can tell which 9–15% is wrong** — so
distributors still pay people to check 100% of records, and the automation saves nothing.

A catalog that is 95% accurate with errors distributed randomly and invisibly is not a catalog
anyone will sign off on.

Two things that don't fix it, both established in the literature:

- **LLM confidence scores are miscalibrated.** A softmax probability is not a reliability estimate.
- **Self-correction doesn't work unaided.** LLMs struggle to correct themselves without external
  feedback from real tools — a finding reported by the same group that built the leading product
  attribute extraction benchmarks.

So Crucible treats the model as an **untrusted proposer** and puts real, external verification
behind it.

## How it works

```
 ERP junk ──▶ RESOLVE ──▶ EXTRACT ──▶ ASSAY ──▶ CERTIFY ──┬──▶ EMIT ──▶ PIM / search / agents
              identity    grounded,   5 external  conformal │
              + evidence  constrained verifiers   risk ctrl │
                                                            └──▶ ESCALATE ──▶ human ──▶ LoRA
```

Input is deliberately industrial, not e-commerce:

```
HX CAP SCR 3/8-16X1 GR5 ZP
1/2 SS BALL VLV 600WOG SCRD
BRG BALL 6205-2RS C3
```

**1. Resolve** — expand trade shorthand, resolve brand and manufacturer part number, harvest
evidence (spec PDFs, product pages, images). Everything downstream must cite one of these.

**2. Extract** — a local vision-language model reads text *and rendered spec-sheet pages*.
Grammar-constrained decoding makes output always schema-valid. Every value must emit a source
span; a value with no span is born untrusted.

**3. Assay** — five independent external verifiers, the external tool feedback the research says
is missing:

| Verifier | Catches |
|---|---|
| Evidence entailment | fabricated values not supported by the cited span |
| Dimensional algebra | unit hallucination and bad conversions — kills *"thread pitch: 4.2 kg"* |
| Constraint solver | physically impossible SKUs (`bore ≤ body_diameter`) |
| Catalog coherence | silent statistical outliers against category distributions |
| Ensemble disagreement | genuinely ambiguous cases |

**4. Certify** — the five signals feed a calibrated nonconformity score, and conformal risk control
picks a threshold so the realized error rate among auto-accepted values stays at or below your
chosen α with 95% confidence. Output carries a signed data certificate with full provenance.

**5. Escalate** — everything below threshold goes to a stronger model, then to a human queue
ranked by risk × revenue impact. Corrections become training data.

**6. Emit** — PIM-ready export, ETIM/UNSPSC codes, JSON-LD, and an MCP endpoint so AI shopping
agents can query the catalog. Marketing copy is generated only from certified attributes, so the
copy inherits the guarantee.

## Design constraints

Runs entirely on a laptop: **8 GB VRAM**, local-first inference, with hosted models used only as
an escalation tier behind a swappable interface. A million-SKU catalog should cost dollars, and
the data never has to leave the building.

## Status

Early development. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

## License

MIT
