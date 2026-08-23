# Build plan — CRUCIBLE for UniHack

One step per sitting. Each step states its goal, the files it touches, and the condition
that means it is finished. Steps are ordered so that the repo is working and the tests are
green at the end of every one of them; nothing here requires holding two steps in your
head at once.

**Rule for every step:** `uv run ruff format`, `uv run ruff check`, `uv run pytest -q`
before it counts as done. If a step turns out to be wrong, record why in the commit
message rather than deleting the evidence.

---

## Where this is going

```
CSV in (6 cols)
   → ingest          RawProduct, source columns preserved verbatim
   → route           category + Dept/Class/Fine/Classpath, or honest generic
   → extract         rules → local LLM, every value grounded in a span
   → normalize       canonical form, value/UOM split for the sheet
   → assay           5 external verifiers, each may abstain
   → fuse            learned scorer over verifier signals
   → certify         conformal threshold at a chosen error rate
   → compose         deterministic prose from verified values only
   → emit            252-column XLSX/CSV + evidence sidecar + certificate
```

The deliverable is the file. The differentiator is that every cell in it can be traced,
and that the empty cells are empty on purpose.

---

## Status

| Phase | State |
|---|---|
| 0 — Unblock, ingest, column anchor | **done** |
| 1 — Taxonomy and routing | **done** |
| 2 — Schemas and normalisation | **done** |
| 3 — Emit (the checkpoint) | **3.1–3.4, 3.7 done**; 3.5 prose, 3.6 sidecar polish, 3.8 fill-mode remain |
| 4 — Verification and calibration | **4.1–4.4, 4.6 done**; 4.5, 4.7 remain |
| 5 — The judge-facing surface | **5.6 dashboard done**; 5.1–5.5, 5.7 remain |
| 6 — Documentation | not started |

Currently: **666 tests green**, lint clean. 75.5% of the 1000-row sample routes to a
category; **616/1000 have a schema behind them** and so get full verification, and the
remainder export honestly through the generic path.

**Verified working, and it is the demo's centrepiece.** Fed the cut-off disc schema a record
with diameter and thickness swapped, the dimensional verifier scored all three values
trust 1.0 — they are all valid lengths in inches — and the constraint verifier caught every
one: *"individually plausible but jointly impossible"*. That is the argument for external
verifiers over model self-critique, demonstrated rather than asserted.

---

## Phase 2 — Schemas and normalisation

### Step 2.1 — Teach `AttributeSpec` how the sheet presents a value
**Goal.** Add three optional fields so the delivery format's presentation lives in the
schema, where a category is already configured, rather than hardcoded in the writer.

- `label: str | None` — the `ATTRIBUTE_LABEL` text ("Voltage Rating")
- `display_uom: str | None` — what the sheet shows ("V", "in", "dBA")
- `order: int | None` — position in the category's attribute template

**Files.** `src/crucible/schema.py`, `tests/test_schema.py`.
**Done when.** All three default to `None`, every existing fixture still validates, and a
new test asserts a spec without them behaves exactly as before.

### Step 2.2 — The generic fallback schema
**Goal.** `ontology.generic_schema()` returning an all-`ValueKind.TEXT` schema, plus
`ontology.resolve(category_id)` that returns it for `generic` or an unknown id.

Build it **in code, not YAML**, so `load_all()` keeps meaning "the shipped categories" and
the `test_ontology` invariants stay meaningful.

**Files.** `src/crucible/ontology.py`, `tests/test_generic.py` (new).
**Done when.** A generic-routed product extracts without raising, and the dimensional and
constraint verifiers abstain on its values rather than failing.

### Step 2.3 — Split value from unit for the sheet
**Goal.** `units.split_value_uom(text, spec)` returning `("50-1/4", "in")` from
`"50-1/4 in"`, `("1/2", "in")` from `'1/2"'`, `("120", "V")` from `"120V"`,
`("5", None)` from `"5"`.

Preserve the source notation in the magnitude. The reference sheet writes `50-1/4`, not
`50.25` — the fraction is how the trade writes it and converting it loses nothing but
gains nothing either.

Also add the colour-temperature trade rule: `27k` → 2700 K, `50k` → 5000 K. Put it in an
explicit table beside the existing WOG entries so it is auditable, not buried in a regex.

**Files.** `src/crucible/units.py`, `tests/test_units_display.py` (new).
**Done when.** ~30 strings lifted from the real dataset round-trip correctly, including
`27k`, `1/2"`, `20mm`, `500'`, `.045"`.

### Step 2.4 — Persist the normalised value
**Goal.** `normalize.normalise_record` populates `AttributeValue.normalized` via
`assay.dimensional.normalize(value, spec)`.

This is pure memoisation — `constraints.build_environment` already does
`value.normalized or normalize(...)`, so the call happens today and is thrown away.

**Files.** `src/crucible/normalize.py`, `tests/test_normalize.py`.
**Done when.** Values carry `normalized` after the pipeline runs, and constraint results
are unchanged.

⚠️ Do **not** use `normalized` for display. It converts to canonical units (millimetres);
the sheet wants source notation. Three representations, three purposes: `raw` for audit,
`normalized` for constraint algebra, `split_value_uom` for the sheet.

### Step 2.5 — Author three deep category schemas
**Goal.** Full YAML for the three biggest, most constraint-rich categories:

| category | rows | the constraint that earns its keep |
|---|---|---|
| `decking.board` | 160 | `thickness <= width`, `length > width` |
| `lamp.led` | 122 | `1000 <= colour_temperature <= 10000`, `wattage > 0` |
| `abrasive.cutoff_disc` | 46 | `thickness < arbor_diameter < disc_diameter` |

The cut-off disc is the showcase: `5"x.045"x7/8"` is three lengths in a fixed order, and
swapping any two produces a physically impossible wheel the constraint verifier catches.
It is the direct analogue of `bore <= body_diameter` on the old valve schema.

**Files.** `data/ontology/*.yaml`, `tests/test_ontology.py`.
**Done when.** Each declares ≥1 constraint, ≥1 required attribute, and a canonical unit on
every quantity — the existing `test_ontology` assertions are the authoring specification.

⚠️ Quote bare `off`/`on`/`yes`/`no` in YAML lists. YAML 1.1 turns them into booleans; this
already cost time once in `data/taxonomy/unilog.yaml`.

### Step 2.6 — Author the remaining shallow schemas
**Goal.** Thinner schemas for `powertool.cordless`, `luminaire.fixture`,
`appliance.major`, `decking.railing`, `accessory.driver_bit`, `blade.saw`,
`apparel.heated`. Fewer attributes, still at least one real constraint each.

**Files.** `data/ontology/*.yaml`.
**Done when.** Every taxonomy node resolves to a schema or is deliberately left to
generic, and `route/taxonomy.py` validation passes.

### Step 2.7 — Loosen the shipped-schema test
**Goal.** `tests/test_ontology.py::test_every_shipped_category_loads` currently asserts
the schema set is *exactly* the three legacy ids. Change to a subset assertion plus the
invariants, which is the more valuable check anyway.

**Files.** `tests/test_ontology.py`.
**Done when.** Green with the new YAMLs present.

---

## Phase 3 — Emit. **This is the checkpoint that matters.**

Reach the end of Step 3.7 before doing anything in Phase 4. Everything after it is upside;
everything before it is required.

### Step 3.1 — Row model and the abstention policy
**Goal.** `emit/rows.py` with `Provenance` (passthrough / routed / extracted / composed /
model / derived), `EmittedCell`, `EmitPolicy`, and `build_row` as a set of independent
per-family populators.

**Blank by default. A populator has to earn a cell.**

**Invariant, enforced in code:** never write a value with no spans unless its provenance is
`PASSTHROUGH` (source columns echoed back) or `COMPOSED` (spans inherited from inputs).

**Files.** `src/crucible/emit/rows.py`, `tests/test_emit.py` (new).
**Done when.** A row builds with the identity and taxonomy families populated and
everything else blank.

### Step 3.2 — The attribute grid
**Goal.** For each attribute in the category template, in `order`, up to 50 slots: always
emit `ATTRIBUTE_LABEL n`; emit `ATTRIBUTE_VALUE n` / `ATTRIBUTE_UOM n` **only** when a
certified value exists.

This is the heart of the submission. Both reference rows carry the identical fifteen
labels and blank *different* values — the format is a per-Fine-class template with values
where known. A populated label beside an empty value is the sheet reporting that the
system looked and did not find, which is strictly more informative than a blank column.

**Files.** `src/crucible/emit/rows.py`, `tests/test_emit.py`.
**Done when.** A product with 4 of 12 attributes resolved emits 12 labels and 4 values.

### Step 3.3 — CSV writer
**Goal.** `emit/writer.py::write_csv` — UTF-8, CRLF, `QUOTE_MINIMAL`, values verbatim,
blanks as `""` and never `"N/A"` / `"None"` / `"nan"`.

**Files.** `src/crucible/emit/writer.py`, `tests/test_emit.py`.
**Done when.** Round-trips through `csv.DictReader` with 252 keys in order, and
`crucible verify-format` passes on the output.

### Step 3.4 — XLSX writer
**Goal.** `write_xlsx` using `openpyxl` with `write_only=True`. Set
`number_format = "@"` on the attribute-value and dimension columns.

⚠️ Without the text format Excel reads `1/2` as a date and `50-1/4` as a formula. Guard the
openpyxl import so the CSV path still works without it.

**Files.** `src/crucible/emit/writer.py`.
**Done when.** The file opens in Excel with `1/2` and `50-1/4` intact, and
`crucible verify-format runs/demo/delivery.xlsx` passes.

### Step 3.5 — Deterministic prose
**Goal.** Move `Token`/`assemble` from `corpus/generate.py` to `text/tokens.py`, then five
composers at the widths measured from the reference rows:

| field | width |
|---|---|
| `INVOICE_DESC` | 38–39 (existing `DEFAULT_FIELD_WIDTH = 40` is already right) |
| `MOBILE_DESC` | ~75 |
| `RETAIL_DESC` | ~74 |
| `SHORT_DESC` | ~115 |
| `LONG_DESC1` | unbounded |

Grounding is **inherited**: the output's span set is the union of its inputs' spans.
Because the template only concatenates verified values with fixed connective words, a
composed description provably contains no fact that was not already verified.

**Files.** `src/crucible/text/tokens.py` (new), `src/crucible/emit/compose.py` (new),
`tests/test_compose.py` (new).
**Done when.** A golden test reproduces the reference rows' descriptions, or reproduces
them within a *documented* relaxation. Do not spend a day on `®` placement — write the
relaxation down and move on.

### Step 3.6 — Evidence sidecar
**Goal.** `write_evidence` — one row per emitted cell: sku, column, value, provenance,
quoted source span, every verifier signal with its detail, nonconformity, decision.

This file *is* "explainable output", delivered literally rather than claimed.

**Files.** `src/crucible/emit/writer.py`, `tests/test_emit.py`.
**Done when.** Every non-passthrough populated cell in a full run has a quoted span.

### Step 3.7 — `crucible enrich` — **THE CHECKPOINT**
**Goal.** One command, input to deliverable.

```bash
uv run crucible enrich --input "Unihack_ Sample Dataset - Input.csv" --out runs/demo
```

Produces `delivery.xlsx`, `delivery.csv`, `evidence.xlsx`, `certificate.json`.
Flags: `--limit`, `--alpha`, `--marketing/--no-marketing`, `--calibration`.

**Files.** `src/crucible/cli.py`, `src/crucible/pipeline.py`.
**Done when.** All 1000 rows export, `verify-format` passes, and the file opens in Excel.

⚠️ Use `--limit 25` while iterating. A full pass is ~25 minutes of GPU.

### Step 3.8 — `--fill-mode`: insurance against a naive rubric
**Goal.** An `EmitPolicy` option with three settings:

| mode | behaviour |
|---|---|
| `certified` (default) | only values that pass the threshold. The thesis. |
| `grounded` | every grounded value, each marked with its confidence in the sidecar |
| `all` | grounded values plus flagged low-confidence proposals |

**Why this exists.** This is a hedge against the one way an otherwise excellent submission
loses on its merits. If "accuracy" is scored naively — cells populated, coverage percent —
our deliberate blanks could score *worse* than a team that hallucinated confidently into
all 252 columns. Being right would lose.

The hedge is cheap (about an hour) and it is not a betrayal of the thesis, because the
uncertainty is never hidden: a value emitted under `grounded` or `all` is marked as
uncertain in the evidence sidecar and excluded from the certificate's scope. The default
stays `certified`. This is a switch for a rubric, not a change of position.

**Files.** `src/crucible/emit/rows.py`, `src/crucible/cli.py`, `tests/test_emit.py`.
**Done when.** All three modes export a valid 252-column file, and the sidecar
distinguishes certified from uncertain in every mode.

---

## Phase 4 — Verification and calibration

### Step 4.1 — The identity verifier
**Goal.** `assay/identity.py::IdentityVerifier`. The domain-appropriate external check,
and unambiguously a tool rather than a second opinion from the same model.

`Part_Desc` carries a redundant copy of `Mfg_Part_Num` on **676/1000** rows — an
independent channel. Compare identity-bearing values across the two using
`difflib.SequenceMatcher` plus a confusable fold (`0↔O`, `1↔I/l`, `5↔S`, `8↔B`).

| verdict | when |
|---|---|
| `ok` | exact match after fold |
| `doubt(0.3)` | matches only under the fold — name which channel differs |
| `fail` | a claimed part number present in neither channel |
| `abstain` | non-identity attributes |

Real cases already in the data: `55226BKLFU` vs `55226BKFLU`, and `174-0CSB3-15W` vs
`174-OCSB3-15A`.

**Files.** `src/crucible/assay/identity.py` (new), `tests/test_identity.py` (new).
**Done when.** Both real typos are caught and clean part numbers pass.

### Step 4.2 — Wire five verifiers
**Goal.** Add `IdentityVerifier` to `pipeline.build_verifiers` and
`api/session.py::_verifiers`.

**Files.** `src/crucible/pipeline.py`, `src/crucible/api/session.py`.
**Done when.** The session reports five verifier names and the scorer fits over them.

### Step 4.3 — `pipeline.run_catalog`
**Goal.** Calibrate on real products. Mirror `run()`, but `ingest.read_products` +
`CascadeRouter` replace `generate_corpus`, and the clean extraction becomes a
pseudo-reference packed into `GoldRecord` so `assay_values()` and `values_agree()` work
**unmodified**. Then `faults.inject_all` supplies labels.

**Files.** `src/crucible/pipeline.py`, `tests/test_pipeline.py`.
**Done when.** A certificate is issued over real products.

⚠️ The pseudo-reference contains the extractor's own errors, so label noise is
one-directional (false negatives): it *deflates* AUROC and *inflates* realised error. Being
wrong in the conservative direction is the right way to be wrong — put that in RESULTS.md,
and label every artifact from this path SIMULATED.

### Step 4.4 — Portable calibration
**Goal.** `certify/artifact.py` — `save_calibration` / `load_calibration` as **JSON**
(logistic `coef_`, `intercept_`, `classes_`, verifier names). Not pickle: fragile across
sklearn versions and unauditable.

**Files.** `src/crucible/certify/artifact.py` (new), `tests/test_conformal.py`.
**Done when.** `crucible enrich --calibration runs/demo/calibration.json` runs an unseen
file with zero labels and without refitting the threshold.

### Step 4.5 — Per-stratum certificates
**Goal.** Generic-routed rows are **not exchangeable** with schema-routed calibration rows.
Add `stratum` and `coverage_by_stratum` to `Certificate`: one certificate for
schema-covered values, one for generic — or refuse a global bound.

Applying one threshold to out-of-distribution rows is the silent failure every other
demo will have. Refusing to is a differentiator, not a limitation.

**Files.** `src/crucible/verdict.py`, `src/crucible/certify/conformal.py`.
**Done when.** The certificate names its stratum and its coverage.

### Step 4.6 — The naive baseline. **Highest-value work after the checkpoint.**
**Goal.** Run the same 1000 products with abstention off (`--fill-mode all`), then measure
how many of the extra filled cells are wrong.

This converts the thesis from an argument into a number, and — this is the point — it is
understandable by a judge who has never heard of conformal prediction. "Filling every cell
gets you 3× the coverage and N% of it is wrong" needs no statistics background. The
certified path's blanks stop looking like gaps the moment the alternative is quantified.

Report it as a three-row table: naive fill / grounded-only / certified, each with cells
populated, error rate, and reviewer-hours implied.

**Files.** `src/crucible/pipeline.py`, `docs/RESULTS.md`.
**Done when.** The three-row comparison exists with real numbers over the real catalog.
**Cost.** ~2 hours once Step 3.8 exists. Do not skip it.

### Step 4.7 — Reviewer-hours on the certificate
**Goal.** Express the guarantee in the buyer's units, not the statistician's.

Unilog's actual pain is paying people to check 100% of records. A certificate that says
"65.5% automation at a certified 8.3% bound" is a statistics result. The same fact stated
as "6,550 of 10,000 records need no human review; of the 3,450 that do, the bound says at
most N escape" is a P&L line.

Add `review_hours_saved` and `records_auto_published` to `Certificate`, parameterised by a
configurable seconds-per-record review rate (default it and state the default).

⚠️ Restate R5 here, on the certificate itself: the guarantee is per-**value**, the
deliverable is per-**row**. Nobody should read "5% error" as "5% of rows".

**Files.** `src/crucible/verdict.py`, `src/crucible/api/session.py`.
**Done when.** The certificate carries both the statistical and the operational framing.

---

## Phase 5 — The judge-facing surface

Everything up to here makes the system correct. This phase is what the judges actually
see, and a demo is the only channel through which any of the preceding work reaches them.
Budget real time for it rather than treating it as decoration.

### Step 5.1 — Label store and sampler
**Goal.** `label/store.py` — append-only JSONL; each label carries sku, attribute, value,
verdict (`correct` / `wrong` / `unsupported`), timestamp and **schema fingerprint** so
labels invalidate when a schema moves.

`label/sampler.py` — **uniform random, seeded.**

⚠️ **The highest-consequence trap in this plan.** Do *not* reuse `session.review_queue`
as the labelling queue. It sorts by nonconformity, so the calibration set would be
enriched for errors, breaking exchangeability and silently invalidating the conformal
bound. Nothing would look wrong.

**Files.** `src/crucible/label/` (new), `tests/test_labels.py` (new).
**Done when.** Sampling is deterministic under a seed and provably uniform.

### Step 5.2 — Terminal labelling loop
**Goal.** `crucible label --input ...` writing the same JSONL. Build this before the web
UI so the labelling can start while the UI is still being written.

**Files.** `src/crucible/cli.py`.
**Done when.** 20 labels can be entered and reloaded.

### Step 5.3 — Labelling routes and page
**Goal.** `GET /api/label/next`, `POST /api/label`, `GET /api/label/stats`, `label.html`.

⚠️ Do not enter `TestClient` as a context manager in the tests for these routes — it runs
the lifespan and fires real inference at Ollama.

**Files.** `src/crucible/api/app.py`, `src/crucible/api/static/label.html`,
`tests/test_api.py`.

### Step 5.4 — The transfer experiment
**Goal.** The actual scientific claim. Fit `LearnedScorer` on injected faults only,
evaluate on hand labels it never saw, report both AUROCs side by side. Then calibrate a
second threshold on hand labels alone and issue `certificate.handlabelled.json` beside
`certificate.injected.json`, with the on-screen banner naming which is displayed.

**Sample-size reality.** `required_sample_size(α, 0.05)` is 59 at α=5%, 99 at α=3%,
**149 at α=2%** — and those must be accepted *with zero errors*. 100 labels supports the
transfer claim and roughly an α=5% certificate; below that the system refuses, which
demonstrates non-negotiable #4 in public. Budget ~300 labels for a sub-3% story.
**Decide this deliberately, not on day 3.**

**Files.** `src/crucible/pipeline.py`, `docs/RESULTS.md`.
**Done when.** Both AUROCs are reported. A collapse is a negative result and belongs in
the ablation table, not the bin.

### Step 5.5 — Close the loop in the browser
**Goal.** `POST /api/emit` and a "Download delivery file" button, so a judge can move the
dial and download the resulting sheet without touching a terminal.

**Files.** `src/crucible/api/app.py`, `src/crucible/api/static/index.html`.

### Step 5.6 — The dashboard. **Use the `ui-ux-pro-max` skill.**
**Goal.** Rebuild `src/crucible/api/static/index.html` as a dashboard that makes the thesis
legible in thirty seconds to someone who has not read a word of this repo.

**Invoke the `ui-ux-pro-max` skill before writing any markup.** It carries the palettes,
font pairings, chart types, UX guidelines and stack-specific guidance this step needs; do
not hand-roll a design when that is sitting there. This is a real frontend served by
FastAPI from `static/`, not an Artifact — self-contained HTML/CSS/JS against the existing
JSON API, no build step, no CDN.

**Why it comes here and not earlier.** The dashboard displays certified values, verifier
signals, per-stratum certificates and the naive-baseline comparison. Building it before
Phases 3 and 4 means building it twice. Building it *last* means never building it — so it
is the first thing after the data exists, not the thing that gets cut.

**What it has to show, in priority order.**

1. **The dial.** One control: maximum acceptable error rate. Everything re-thresholds
   instantly (the session already holds everything in memory for exactly this). This is the
   product; give it the space that implies.
2. **The refusal state, designed rather than defaulted.** When α is unreachable the panel
   says so and explains why. Most demos have no such state; ours has one on purpose, and it
   should look deliberate, not like an error.
3. **A row from the delivery sheet, rendered as the sheet.** With a populated
   `ATTRIBUTE_LABEL` beside an empty `ATTRIBUTE_VALUE`, and a hover or click that explains
   *why* it is empty. This is the single most important pixel in the submission.
4. **The review queue**, each item showing all five verifier verdicts with their plain-text
   reasons — including the abstentions, which must be visually distinct from passes.
   "Not checked" and "checked and fine" look different or non-negotiable #3 is broken on
   screen.
5. **The naive-baseline comparison** from Step 4.6 as a chart. Three bars, one number each.
6. **Download.** Delivery file, evidence sidecar, certificate.
7. **The provenance trail** for a selected cell: source span quoted, with the matched text
   highlighted in the original description.

**Non-negotiable #6 applies to the screen.** Anything derived from injected faults is
labelled SIMULATED in the interface, not only in the docs.

**Files.** `src/crucible/api/static/index.html`, `src/crucible/api/app.py`,
`tests/test_api.py`.
**Done when.** A judge can move the dial, see the guarantee change, click a blank cell and
be told why it is blank, and download the file — without a terminal and without
explanation.

### Step 5.7 — Demo rehearsal
**Goal.** Run the five-minute demo end to end against the real app, timed, and fix whatever
is slow, ugly, or needs apologising for. Rehearsal is where you discover that the thing you
planned to show takes forty seconds to load.

**Done when.** It runs twice, cleanly, in under five minutes.

---

## Phase 6 — Documentation

### Step 6.1 — Rewrite `docs/RESULTS.md`
Numbers for the new domain, with caveats. Include the **Icecat ablation**: 0/999 coverage
measured over 1000 real distributor SKUs (2 matches, both spurious numeric collisions —
a Hunter ceiling fan and a mason line hitting ids in a printer-supplies index). Open Icecat
is brand-sponsored and consumer-electronics-weighted; it does not cover US building-
materials distribution. Keep `corpus/icecat.py` with a docstring recording the
measurement — recording wrong turns is a stated habit of this project.

### Step 6.2 — Update `README.md` and `CONTEXT.md`
New domain, new pipeline shape, the abstention thesis stated up front.

### Step 6.3 — Demo script
Five minutes, in order: sparse input → routing with its abstentions → the dial →
a review-queue item with its five verifier reasons → download → open in Excel → point at a
populated label with an empty value and explain why that is the product.

---

## Standing risks

| | |
|---|---|
| **R1** | The composer may not reproduce human-authored rows exactly. Document a relaxation; do not chase it. |
| **R2** | Schema authoring is the long pole and gates emit quality. Three deep, seven shallow, generic for the tail. |
| **R3** | GPU: ~25 min per full pass, re-run on every prompt change. Key the harvest cache on the input file; use `--limit`. |
| **R4** | `_ground()` is strict and descriptions average 38 characters, so the grid will be sparse and will *look* like weak coverage. `ExtractionStats` already tracks grounded-vs-proposed — lead with that number as a feature. |
| **R5** | The guarantee is per-**value**; the deliverable is per-**row**. Nobody should read "5% error" as "5% of rows". State the unit on the certificate. |

## Cut order, if time runs out

Cut from the top: embedding router → `MARKETING_DESCRIPTION` → ensemble verifier on the
real catalog → LLM router tier → per-stratum certificates → the labelling *web* UI
(degrade to `crucible label`).

**Never cut:** the 252-column writer, ingest, the lexical router, the generic fallback,
the evidence sidecar, the abstention policy, **the dashboard (5.6)**, and **the naive
baseline (4.6)**.

Those last two are on the never-cut list deliberately, because both are the kind of work
that feels optional next to "real" engineering and is not. The dashboard is the only
channel through which any of the preceding work reaches a judge. The naive baseline is the
only thing that makes an empty cell legible as a decision rather than a failure. A correct
system nobody can see, next to a comparison nobody ran, loses to a worse system with a
better demo.

## If the deadline arrives early

The minimum viable submission, in order: Steps 2.1–2.5 (three categories only, skip the
shallow ones and let generic carry the rest) → 3.1–3.4 → 3.7 → 5.6.

That is a file, an honest abstention policy, and a dashboard. It skips calibration
entirely, which means no certified bound — so say so plainly rather than implying one.
A working system with an honest gap beats a broken system with an ambitious claim.
