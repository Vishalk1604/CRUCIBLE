# The winning plan

Written after reading the Solution Guide (`guide.md`). It supersedes the priority order in
`plan.md`, which was set before we knew what the client was scoring.

**The headline: our architecture is right and our inputs are wrong.** The guide describes
our differentiator in its own words — *"a confidence score or a 'needs human review' flag is
a genuinely valuable feature"* — and then hands us 161,000 rows of controlled vocabulary,
27,000 approved manufacturer names, ~500 approved units, and **200 fully enriched rows of
labelled ground truth**. We built hand-authored substitutes for three of those four because
we did not know they existed.

Nothing needs to be thrown away. The verifiers, the scorer, the conformal layer, the
emitter, the job runner and the site all stand. What changes is what they read from, and
what we point them at.

---

## Step 0 — What we actually have *(revised 2026-08-23)*

**The seven reference files are not published anywhere we can find.** We have exactly two:
the 1,000-row input and the Expected Output sheet. That sheet carries **2 fully enriched
ground-truth rows**, both dishwashers, and every description field is exemplified in them.

So the plan below is rebuilt around two rows instead of two hundred. What that costs, and
what substitutes:

| Missing | Cost | Substitute |
|---|---|---|
| 200-row ground truth | **Severe.** No field-level accuracy at scale | 2 exemplar rows for template derivation; hand-labelling for calibration |
| LOV (161k rows) | Cannot claim LOV compliance | Our authored vocabularies stay — but we must **not** claim they are the client's |
| Manufacturer/brand list (27k) | No canonical casing or ®/™ | Derive a canon from the 76 distinct `Part_Manuf` values in the input |
| UOM standards (~500) | No approved-abbreviation list | The guide transcribes the house rules; `units.py` already implements most |
| Content guidelines .docx | No formulas | **The guide transcribes them, and the 2 rows demonstrate them** |
| FAUCETS_LOV | No faucet spec | Do depth on our best-covered category instead |
| Decimal_Fraction (63 rows) | None | **Trivially reconstructible** — it is just n/64 arithmetic |

⚠️ **One line in the guide now governs everything we say:** *"A fluent description made of
invented values scores zero."* Without the LOV we cannot verify our vocabularies are the
approved ones. We therefore describe the vocabulary verifier as *"values constrained to a
per-category controlled vocabulary"* and never as *"LOV compliance"*. Claiming their
standard while using ours is the one move that could actually lose this.

### The templates, derived from the two rows

Both rows agree, so these are structure rather than guesswork:

```
SHORT_DESC    BRAND® Series MPN ProductName [With-clause], <key attrs>      (96–115)
RETAIL_DESC   Series ProductName, <key attrs>                              (74–75)
LONG_DESC1    BRAND® ProductName [With-clause], Series, <all attrs as
              "value unit Label">[, Additional Information: …]              (390–405)
MOBILE_DESC   Manufacturer Brand, ProductName, Series, MPN[, +attrs]       (64–75)
INVOICE_DESC  PRODUCTNAME <abbreviated attrs, CAPS, unit closed up>        (38–39, ≤40)
```

Two details the examples settle:
- **`INVOICE_DESC` closes the unit up** (`120V`, `50-1/4IN`, `41DBA`) while every other
  field spaces it (`120 V`, `50-1/4 in`). Both rules are real; encode both.
- **Row 2 drops the manufacturer prefix from `MOBILE_DESC`** because manufacturer and brand
  are the same company. Deduplicate, do not concatenate blindly.

`ITEM_FEATURES_1..20` turn out to be derivable after all: 8 of row 2's 11 features are the
comma-split of its `Additional Information` attribute, plus its sound level. Row 1 has none
and also has no Additional Information — consistent.

`MARKETING_DESCRIPTION` is genuine manufacturer copy and is **not** derivable from six input
columns. Leaving it blank is a correct abstention, not a gap.

## Step 1 — The description composers ✅ **DONE** (2026-08-23)

> Built. 25 → 41 columns; all five descriptions populate 20/20 products.
> `MOBILE_DESC` reproduces the reference row character for character.
> 692 tests green.

**Why first:** *"Getting these formats right is most of the task."* Five descriptions at five
lengths and casings, plus 20 `ITEM_FEATURES`. Currently all 26 are empty — **25 of our 252
columns carry any value at all**, and this is the largest single block of the missing 227.

Build `emit/compose.py` with one function per field, each a deterministic template over
already-verified values:

| Field | Rule from the worked example |
|---|---|
| `INVOICE_DESC` | ≤40 chars, ALL CAPS, no spaces around the unit (`50-1/4IN`) |
| `MOBILE_DESC` | 60–80 chars — `Manufacturer Brand, Item Type, Series, MPN` |
| `SHORT_DESC` / Product Title | `Brand® + Series + MPN + Item Type + With-clause + key attributes` |
| `LONG_DESC1` | Unbounded; every verified attribute, in LOV order, units spaced |
| `RETAIL_DESC` | Series + Item Type + top attributes |
| `ITEM_FEATURES_1..20` | One verified attribute each, as a phrase |

**The claim this earns, which no competitor can make:** grounding is *inherited*. Because the
template only concatenates verified values with fixed connective words, a composed description
provably contains no fact that was not verified. **"A fluent description made of invented
values scores zero"** — ours cannot contain one by construction.

⚠️ A composer must **refuse to emit** rather than pad when a required value is missing. A
40-character CAPS line assembled from three verified facts and one guess is exactly the
failure mode the guide is describing.

**Done when:** the worked-example dishwasher row is reproduced, or reproduced within a
documented relaxation. Do not spend a day on ® placement — write the relaxation down.

---

## Step 2 — Load their master data, replace ours

### 2a. LOV loader → the vocabulary verifier's real authority
`reference/lov.py`: parse the 161k rows into `{classpath → {attribute → {raw value →
normalized value}}}`. Then `VocabularyVerifier` checks against **the client's own approved
list** instead of vocabularies we invented, and gains a second power: **normalisation**, since
the LOV carries `Normalized Label` and `Normalized Values`.

This also reframes the verifier for the submission. It stops being "a list we wrote" and
becomes "compliance with the client's controlled vocabulary" — directly measurable as
*percentage of values found in the LOV*, one of the three metrics the guide says judges look
for.

### 2b. UOM standards → replace our hand-rolled tables
`DISPLAY_UOM` and `DISPLAY_ONLY_UOM` in `units.py` were guesses. Replace with the ~500
approved abbreviations. Enforce the house rule: **a space between number and unit** (`24 in`,
never `24in`) — except inside `INVOICE_DESC`, where the worked example shows `50-1/4IN`
closed up. That contradiction is real; encode both.

### 2c. Manufacturer/brand list → normalisation *and* a stronger identity verifier
27k rows with exact legal casing, suffixes and ®/™. Fuzzy-match `Part_Manuf` to a canonical
manufacturer, take the paired brand, and apply the rule **"where an item has no brand, the
manufacturer name is used instead."**

This upgrades `IdentityVerifier` from "the two input columns disagree" to "**this brand is not
on the approved list**" — a genuine external authority rather than an internal consistency
check.

### 2d. Decimal ↔ fraction
63 conversions. We already preserve `50-1/4` rather than flattening to `50.25`, which the
guide confirms is the right direction. Add the reverse: manufacturers publish decimals, buyers
search fractions.

---

## Step 3 — The three metrics ✅ **DONE** (2026-08-23)

> `crucible evaluate` reports all three. Character-limit compliance **95–100%**;
> controlled-vocabulary compliance **43%** (a to-do list, see DIARY 21);
> field accuracy carries its `n=2` caveat in code. 744 tests green.

> *"Field-level accuracy against the 200 known-good rows, character-limit compliance, and
> percentage of values found in the LOV are all simple, credible metrics. Judges will look
> for them."*

Build `evaluate.py` producing exactly those three, per column and in aggregate:

1. **Field-level accuracy** — exact match, plus a normalised match that tolerates casing and
   spacing, reported separately so neither hides the other
2. **Character-limit compliance** — % of `INVOICE_DESC` ≤40, `MOBILE_DESC` in 60–80, etc.
3. **LOV compliance** — % of emitted attribute values present in the approved list

Then `crucible evaluate --truth <200-item file>` and a results panel on the site.

**This also retires a caveat that has been dragging on the whole project.** Every number we
have — AUROC 0.66, realised error 3.2% — rests on injected faults over a pseudo-reference,
with one-directional label noise. Against real ground truth, the conformal guarantee can be
calibrated on *actual* errors. `run_catalog`'s injected-fault path becomes the documented
fallback for categories the 200 rows do not cover.

---

## Step 4 — Faucets, end to end. **The strategy inversion.**

> *"Depth beats breadth. One category done fully — classified, attributed, described and
> validated — demonstrates more than a thin pass over all 1,000 rows."*

We optimised for the opposite: 75.5% routing across 1,000 heterogeneous rows. The guide names
Faucets as the ideal demo scope — four sheets, fixed attribute order, fixed title word order,
permitted values, synonyms, and a visual style guide.

Build `data/ontology/faucet.kitchen_bath.yaml` from `FAUCETS_LOV.xlsx` rather than by reading
descriptions, with the LOV's own attribute sequence as `order` and its permitted values as
`vocabulary`. Then run the full pipeline on faucets only and report every metric from Step 3
for that category.

**Keep the 1,000-row breadth run as the second exhibit**, not the first: *"and the same
pipeline processes all 1,000 rows without modification."* Depth as the argument, breadth as
the proof of generality.

---

## Step 5 — The remaining derivable columns ✅ **DONE** (2026-08-23)

> Built `populate_commerce`. **25 → 41 → 61 of 252 columns** on a 120-product run.
> Image/document filenames deliberately refused — see `TestRefusals`. 719 tests green.

~15 more from data we already hold: `Product Name`, `Standard/Approvals`, `With`, `Includes`,
`Application`, `Selling Qty`, `Selling UOM`, `Country Of Origin`, and the
`LENGTH`/`WIDTH`/`HEIGHT`/`WEIGHT` + UOM pairs.

Combined with Steps 1 and 2, populated columns go from **25 → roughly 70**, and the remaining
blanks are the ones that genuinely cannot be derived from six input columns (images, PDFs,
UPC/EAN, distributor-internal SKUs) — which is a defensible, explainable gap rather than an
apparent failure.

---

## Step 6 — Scale ⚠️ **DONE, negative result** (2026-08-23)

> Concurrency implemented and tested (determinism guaranteed). Measured ceiling **1.09×**,
> unchanged by `OLLAMA_NUM_PARALLEL=2`. The bottleneck is VRAM: 6.2 GB model on an 8.15 GB
> card cannot fit a second KV cache. **1.14 s/product ≈ 19 min per 1,000.** Claim the
> architecture is concurrency-ready; do not quote an unmeasured projection.

*"Scale efficiently across large product catalogs"* is an explicit Expected Outcome, and
1.4 s/product = 24 minutes per 1,000 is our weakest measurable number.

Concurrent Ollama requests (4–6 in flight) should reach roughly 6–8 minutes. The job runner
already streams rows, so the UI needs no change. Add a throughput figure to the results panel:
*products/minute*, and an extrapolation to 100k SKUs.

---

## Step 7 — Position it in their vocabulary

The Expected Outcomes list approaches we use but do not name:

| They say | We have |
|---|---|
| Knowledge graphs | The classpath taxonomy + LOV + attribute schemas **is** one. Say so. |
| RAG | LOV/manufacturer lookup is retrieval-augmented generation over master data. Say so. |
| Human-in-the-loop | The review queue is exactly this. Lead with it. |
| Vision-language models | qwen3-**vl**. Already true. |
| AI agents | Honestly: no. Do not claim it. |

**Lead the submission with the sentence they wrote:** *"Noticing and reporting such gaps is a
strength, not a failure; a confidence score or a 'needs human review' flag is a genuinely
valuable feature."* Then show the **Rheem Manufacturing / FRIGIDAIRE®** row — the exact
mismatch the guide mentions — flagged by our identity verifier, unprompted.

---

## Step 8 — Sourcing rules *(only if we add retrieval)*

> *"Product data must come from the manufacturer's own site or documentation. Marketplaces
> and distributor sites are explicitly excluded."*

We currently retrieve nothing, so we comply trivially. If retrieval is added, the domain
allowlist is a hard requirement — and worth stating explicitly in the submission either way,
since it shows the guidelines were read.

---

## Order of work

| Priority | Step | Blocked by |
|---|---|---|
| **1** | Description composers | nothing — **start now** |
| **2** | 200-item evaluation harness | Step 0 |
| **3** | LOV + UOM + manufacturer loaders | Step 0 |
| **4** | Faucets end to end | Step 0 |
| **5** | Remaining derivable columns | Step 2b |
| **6** | Concurrency | nothing |
| **7** | Submission framing | Steps 1–4 |

## The risk this retires

The red *"populate all the headers"* warning stops being an argument we have to win. After
Steps 1, 2 and 5, roughly 70 columns carry values, every blank has a stated reason, and the
evidence sidecar names it per cell. We are no longer asking a judge to accept that blanks are
deliberate — we are showing them a mostly-full sheet whose few gaps are annotated.

## What stays as-is

Verifiers, learned scorer, conformal certification, emit stage, job runner, preflight, the
site. The architecture was right. It was reading from tables we wrote instead of tables they
supplied, and measuring against faults we injected instead of answers they gave us.
