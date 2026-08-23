# Working diary

A running log of what was asked, what was done, what was measured, and what was decided.
It exists so that a fresh assistant — or a fresh human — can pick this project up mid-flight
without re-deriving anything, and without repeating a wrong turn that has already been paid for.

## Protocol for whoever writes here next

1. **Append only. Never delete or rewrite an earlier entry.** A superseded decision stays,
   with a new entry recording what replaced it and why. The wrong turns are the most
   valuable content in this file — they are what stops the next person paying for them twice.
2. **One entry per interaction that changed something.** Not every message needs an entry;
   a question answered with no change to the repo does not. A measurement does, even if it
   produced a negative result. Especially then.
3. **Record the prompt.** Paraphrase is fine for long ones, but keep the user's actual
   wording where the phrasing carried intent.
4. **Record numbers with their caveats.** A figure without its caveat becomes a claim.
5. **Read `CLAUDE.md` first** for the project's thesis and its six non-negotiables, then
   `plan.md` for the ordered build steps, then the newest entry here for current state.

Entry format:

```
## Entry N — YYYY-MM-DD — short title
**Prompt.** What was asked.
**Did.** What actually happened.
**Measured.** Numbers, with caveats.
**Decided.** Choices made and why.
**State.** Tests / lint / what works now.
**Next.** The immediate next step.
```

---

## Entry 1 — 2026-08-22 — Environment setup

**Prompt.** "download all the required libraries and olama"

**Did.** Neither `uv` nor Ollama was on the machine. Installed both via winget, synced the
project, pulled the model.

- `winget install astral-sh.uv --source winget` → uv 0.12.5
- `winget install Ollama.Ollama --source winget` → Ollama 0.32.15
- `uv sync --extra models --extra api --extra dev` → 40+ packages
- `ollama pull qwen3-vl:8b` → 6.1 GB

The first winget call failed with exit 231 because the `msstore` source could not resolve
DNS; `--source winget` fixes it. Worth knowing — it will recur on this machine.

**Measured.** torch 2.11.0+cu128, `torch.cuda.is_available()` → `True`. Full suite:
**344 passed** in 143.74s.

**Decided.** Nothing architectural.

**State.** Working install. Two follow-ups noted: `uv` is not on PATH in already-open
shells (winget updated the variable, running shells do not see it — it lives at
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\`),
and `.env` had Icecat key names but the values were the user's to fill.

---

## Entry 2 — 2026-08-22 — First real run

**Prompt.** "do a test run"

**Did.** Launched `crucible-app` and exercised every API route.

**Measured.** Startup ~1.1s from cache. Every documented number in `docs/RESULTS.md`
reproduced exactly:

| | documented | measured |
|---|---|---|
| products / values | 600 / 2627 | 600 / 2627 |
| AUROC | 0.928 | 0.9281 |
| unverified error | 30.3% | 30.29% |
| α=7% automation / bound / realised | 18.7% / 3.19% / 1.22% | 18.74% / 3.186% / 1.22% |
| α=10% automation / bound / realised | 65.5% / 8.33% / 6.28% | 65.49% / 8.334% / 6.28% |

α=2% correctly returned `feasible:false` with an honest reason. The sweep refuses at
0.5%, 1%, 2%, 3% and certifies above.

**Found a defect.** `pyproject.toml` declared `crucible = "crucible.cli:app"` but
`src/crucible/cli.py` did not exist. `uv run crucible` failed with `ModuleNotFoundError`.
Only `crucible-app` worked. (Fixed later, Entry 6.)

**State.** Everything documented is real. No drift between docs and code.

---

## Entry 3 — 2026-08-22 — GPU verification

**Prompt.** "test the model and check if it is working on the gpu or not . and fix the
errors you are facing"

**Did.** Drove the project's own `LLMExtractor` (not a synthetic prompt) and sampled GPU
placement while the model was resident.

**Measured.**
- `ollama ps` → `qwen3-vl:8b  6.2 GB  13%/87% CPU/GPU  4096 ctx`, 6709 MiB on the card.
  The 13/87 split matches what `CLAUDE.md` documents for a 6.1 GB model on 8 GB of VRAM.
- torch: `sm_120` (Blackwell), 5.4 TFLOP/s fp32 on a real matmul — proving it *computes*
  on GPU, not merely that `is_available()` returns True.
- Warm extraction: 1.85s, 1.28s, 1.35s. `15/15 values grounded, 0 empty, 0 unparseable`.
  **Faster than the documented 2.3 s/product.** Cold start 5.40s including model load.

The `think=False` handling is correct against Ollama 0.32.15 — no empty responses, so the
`thinking`-field fallback in `extract/llm.py::_payload` is working.

**Errors hit were mine, in a throwaway script, not in the repo:** `load_ontology` does not
exist (it is `get_schema`), `RawProduct` takes `category_id` not `category`, and
`AttributeValue` has `.raw`/`.normalized` not `.value`. Nothing in the project needed changing.

**Observed, and it matters.** The test extraction returned `weight = '316 stainless body'`
— a body-material phrase in a weight field — and it grounds cleanly because that substring
really is in the source. **Grounding proves provenance, not correctness.** This is exactly
the case the dimensional verifier exists to catch, and it is good evidence the pipeline
sees realistic errors.

---

## Entry 4 — 2026-08-22 — The brief arrives; project pivots

**Prompt.** The user supplied the actual UniHack requirements (screenshot + text) and
said the input/output files and the Icecat daily index were downloaded, then:
"make our project the best hackathon project as per there requirements."

**Did.** Found the three files in the repo root and measured them rather than assuming.

**Measured — and this reframed the whole project.**

*The delivery format.* `Unihack_ Expected Output - Delivery Format.csv` — **252 columns**,
2 example rows, both dishwashers. They carry the **identical** `ATTRIBUTE_LABEL 1..15`
sequence but blank **different** values (row 1 blanks Model / Plug Type / Color; row 2
blanks Number of Wash Cycles / Plug Type / Maximum Height).

→ The format is a **per-Fine-class attribute template with values only where known.**
Non-negotiable #5 ("never fabricate a value to satisfy a schema") is not in tension with a
252-column sheet — *it is what the sheet already specifies*. A populated label beside an
empty value is a structured statement that the system looked and did not find. Every other
team will fill those cells. **Refusing to, correctly, is the submission.**

*The input.* 1000 rows, 6 columns. `Part_Desc` averages 38 chars (13–70).
`Unilog_Brand` is a placeholder on **all 1000** rows; `E1_Brand` on 799; `DIB_Brand` on 755.
676/1000 descriptions contain the `Mfg_Part_Num` — a redundant identity channel.
Domain: lighting, power-tool accessories, decking, appliances, lumber, electrical.
**Nothing to do with the existing valve/bearing/fastener ontology.**

*Icecat — a decisive negative result.* Scanned all 28,547 entries of `daily.index.xml.gz`
against the 999 distinct part numbers. **0/999 real matches.** Two apparent hits
(`52655`, `25762`) are spurious: a Hunter ceiling fan and a mason line colliding with
numeric ids in what is largely a printer-supplies index. Open Icecat is brand-sponsored and
consumer-electronics-weighted; it does not cover US building-materials distribution.

→ **`docs/HANDOFF.md`'s "Priority 1 — Icecat" is dead.** Keep `corpus/icecat.py` with the
measurement recorded in its docstring, per this project's habit of preserving wrong turns.

*Other verified facts.* `Classpath` is NOT `Dept>Class>Fine` — two independent taxonomies
(`Appliances / Large Appliances / Dishwashers` vs
`Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers`).
`PART_NUMBER` and `SKU - MY_PART_NUMBER` are distributor-internal ids, not derivable from
input → must be blank. `openpyxl` was **not installed**; `sentence_transformers` 6.0.0 was.

*An anomaly in the judges' own reference file.* Row 1 pairs
`MANUFACTURER_NAME = Rheem Manufacturing` with `BRAND_NAME = FRIGIDAIRE®`. Row 2 correctly
pairs Whirlpool with Whirlpool. Frigidaire is Electrolux; Rheem makes HVAC. The planned
identity verifier flags an inconsistency in the reference sheet itself. Handle tactfully.

*A latent blocker.* `corpus/harvest.py::_cache_key` fingerprinted **every** schema from
`load_all()`. Adding a single new category YAML would invalidate the existing harvest
cache, and since `CertificationSession` is built inside the FastAPI lifespan, the next
`crucible-app` launch would silently spend ~25 minutes on inference before serving.

**Decided (user chose, via AskUserQuestion).**
1. **Labels for calibration** — fault injection on real products via the existing
   schema-driven `corpus/faults.py`, plus a hand-check UI to show transfer to natural errors.
2. **Ontology** — user said "choose the best option to win hackathon"; chose **tiered**:
   authored YAML for the top categories, generic fallback for the tail. It is the only
   option satisfying the brief's explicit "must handle unseen data" while keeping all
   verifiers alive.
3. **Time budget** — 3+ days, full scope.

Plan approved and saved to `C:\Users\Paras Wadkar\.claude\plans\stateful-imagining-breeze.md`.

---

## Entry 5 — 2026-08-22 — Phase 0: unblock, ingest, column anchor

**Did.**

1. **`corpus/harvest.py::_cache_key`** now takes `category_ids` and scopes to the
   categories the corpus actually uses. **Verified the key is byte-identical today**
   (`449bb62e713e257f`, matching the on-disk cache; `sample-b7c3c09b3da8fa75` likewise),
   so nothing was invalidated — but new YAMLs can no longer trigger the 25-minute stall.
2. **`src/crucible/emit/columns.py`** — the 252-tuple, **generated mechanically** from the
   reference CSV rather than hand-typed, plus `validate_header` (strict on order as well as
   membership), `attribute_columns`, `feature_column`, `ref_url_column`.
3. **`src/crucible/ingest/csv_source.py`** — `read_products`, `to_raw_product`,
   `best_brand` (DIB→E1→Unilog, returns `None` never a placeholder), `split_part_manuf`
   (`"Freud Inc (2435)"` → name + code), `is_placeholder`, tolerant `infer_columns` that
   **raises** without a description column rather than emitting 1000 empty rows,
   and `erp_text` composing the evidence document.
4. **`src/crucible/cli.py`** — created (fixes Entry 2's defect). `verify-format`, `inspect`.
5. `openpyxl>=3.1` added to `pyproject.toml`; ruff `per-file-ignores` for B008 (Typer's
   call-valued defaults are its API, not the bug B008 targets).

**Why `erp_text` exists.** A brand read from `DIB_Brand` cannot be located in `Part_Desc`,
so `_ground()` correctly discards it — and the failure *looks* like a weak extractor when it
is really an evidence document that omitted the field the value came from. Composing the
evidence is the fix; the grounding rule stays strict. Degrades to `raw.description` when
`extra` is empty, so existing tests are unaffected.

**Measured.** 1000/1000 ingested. One duplicate part number (`AVM6EV`) disambiguated to
`AVM6EV#2` — without this the second row overwrites the first in every sku-keyed dict,
including the ensemble verifier's index. 442/1000 real brands recovered.
`crucible inspect` independently reproduced the 676/1000 identity-channel figure.

**State.** 407 tests (was 344), lint clean.

---

## Entry 6 — 2026-08-22 — Phase 1: taxonomy and routing

**Did.** Authored `data/taxonomy/unilog.yaml` (21 nodes) by reading real descriptions and
token frequencies, not by imagining a catalog. Built `route/taxonomy.py` (eager validation
in the `ontology.py` spirit) and `route/router.py` (`LexicalRouter`, `CascadeRouter`,
`RouterStats`). Added `Routing` to `schema.py` and `routing` to `ProductRecord`. Added
`crucible route`.

**Three findings, each of which changed the design.**

1. **YAML 1.1 turns bare `off` into boolean `False`.** The `abrasive.cutoff_disc` keyword
   list failed pydantic validation at load. The eager validation caught it instead of
   letting the node silently never match. Quote `off`/`on`/`yes`/`no` in YAML lists.

2. **Brand tokens must not be decisive.** `milw`/`dewalt` were `strong` terms for
   `powertool.cordless`, which made `'Milw 4-1/2"x1/8"x5/8-11" Metal Cut Off'` a tie
   between cordless tools and abrasive wheels — Milwaukee sells both the grinder and the
   wheel. Demoting them to supporting evidence dropped coverage 70.5% → 68.7%, so the loss
   was **audited rather than assumed**: the 56 brand-bearing rows that went generic are
   insulated water bottles, tool chests, mechanical pencils, hearing protectors and a laser
   level. Correctly *not* cordless tools. **The demotion lost nothing real.**
   A brand narrows a catalog; it does not name a product.

3. **Colour names collide with hardware nouns.** `gate` was a disqualifier for
   `decking.board` and cost a real board: `"1x12-12' Castle Gate - Landmark Azek PVC
   Fascia"` is decking whose *colour* is Castle Gate. Harvest, Reserve and Vintage have the
   same problem. Disqualifiers must be terms that never appear as a finish.

Also: `rail` was promoted to a decisive term after `"Assembled Black Rail Panel"` came out
one point ahead of `decking.board` — which was scoring on the `TREX` in the brand column —
and a one-point lead *inside the same product family* reads as a tie, so the router refused
a row it should have been sure about.

The audit surfaced a missed family: `bit`/`drive`/`torx` (70+ rows) → added
`accessory.driver_bit`, plus `safety.ppe`, `roofing.panel`, `lumber.dimensional`,
`masonry.mortar`, `millwork.opening`, `consumable.tape`.

**Measured.** **75.5% classified** across 21 categories; 245 generic; 40 abstained as
ambiguous. Largest: decking.board 160, lamp.led 122, powertool.cordless 80,
luminaire.fixture 71, appliance.major 65.

**Decided.** Stopped tuning the taxonomy at 75.5%. The remaining tail is genuinely diverse
and the generic path is a designed feature, not a gap — generic rows still export, with
Dept/Class/Fine deliberately blank. Marginal coverage is worth less than the emit stage.

**State.** 448 tests, lint clean.

---

## Entry 7 — 2026-08-22 — Build plan written

**Prompt.** "make a plan.md in which you will laydown the pipeline of what you should do in
a detailed manner write down steps which you can handle once at a time"

**Did.** Wrote `plan.md` — 27 steps across 5 remaining phases, each with goal, files, and a
done-when condition, ordered so the repo is green at the end of every step.

**Two traps are bolded in it because they fail silently:**
- **Step 5.1** — do not reuse `session.review_queue` as the labelling queue. It sorts by
  nonconformity, so the calibration set becomes enriched for errors, breaking
  exchangeability and invalidating the conformal bound with nothing appearing wrong.
- **Step 4.3** — the pseudo-reference carries the extractor's own errors, so label noise is
  one-directional. It *deflates* AUROC and *inflates* realised error: conservative, not
  flattering. Must be stated in RESULTS.md.

**One decision deferred to the user, in Step 5.4.** Certifying α=2% needs 149 accepted
values with **zero** errors among them. 100 hand labels buys the transfer claim and roughly
α=5%; ~300 buys a sub-3% story. Cheap to decide now, expensive to discover on the last day.

**State.** 448 tests, lint clean. **Nothing committed yet** — Phases 0–1 are uncommitted
work against a deadline. The two Unihack CSVs must be committed too: `test_columns.py`
anchors against the reference sheet and would fail on a fresh clone. `daily.index.xml.gz`
(1.3 MB, the dead end) should be gitignored.

**Note on git status.** ~40 files show as modified with **zero content changes** — pure
CRLF line-ending churn from a Windows checkout. `git diff --stat` shows only the real
edits. Do not sweep them into a commit; stage explicitly.

**Next.** `plan.md` Step 2.1 — add `label` / `display_uom` / `order` to `AttributeSpec`.

---

## Entry 8 — 2026-08-22 — This diary created

**Prompt.** "make a dairy.md with all the things that we did and the prompts that ive given
or you have answered and never delete anything from that file and only append it after every
new interaction if necessary so that multiple ai's can take up the work where you leave
incase of credit limit being reached"

**Did.** Created this file (as `DIARY.md`) and backfilled Entries 1–7 from the session.

**State.** Unchanged: 448 tests, lint clean, Phases 0–1 complete and uncommitted.

**Next.** Unchanged: `plan.md` Step 2.1.

---

## Entry 9 — 2026-08-23 — Strategy assessment, then plan.md extended

**Prompt.** "tell me how much is done and how can we make the app better and how can we make
use of the technological advantage we have over other and what do you think are the chances
that we win" — then: add those points to `plan.md` at the optimal steps, add a dashboard
step using the `ui-ux-pro-max` skill, and continue.

**Assessed, honestly.** ~35% of the retarget, but the number flatters us: the missing 65%
contains the only thing the brief asks for. **The deliverable does not exist yet.** If
judged today we would submit nothing. That fact should dominate every decision.

Chances: execution-gated. If the emitter ships and the demo is tight, genuinely strong —
top-three plausible, because the approach is differentiated in a way judges from a product-
content company will recognise. If it does not ship, near zero regardless of the thinking.
No percentage given; the field and rubric weights are unknown and a number would be false
precision.

**The risk worth restating.** If "accuracy" is scored naively — cells populated, coverage
percent — our deliberate blanks could score *worse* than a team that hallucinated into all
252 columns. Being right would lose. Two hedges added to the plan.

**Added to `plan.md`.**
- **Step 3.8** `--fill-mode` (`certified` default / `grounded` / `all`) — insurance against
  a naive rubric. Not a betrayal of the thesis: uncertainty is marked in the sidecar and
  excluded from the certificate. A switch for a rubric, not a change of position.
- **Step 4.6** the naive baseline — run the same catalog with abstention off and measure
  how many extra filled cells are wrong. Converts the thesis into a number a judge can read
  without knowing what conformal prediction is. Marked never-cut.
- **Step 4.7** reviewer-hours on the certificate — express the guarantee in the buyer's
  units, since Unilog's real pain is paying people to check 100% of records.
- **Step 5.6** the dashboard, explicitly using the **`ui-ux-pro-max`** skill (verified
  enabled: 79 styles, 192 palettes, 25 chart types, 22 stacks). Placed after Phases 3–4
  because it displays certified values and verifier signals — building it earlier means
  building it twice, building it last means never. Marked never-cut: it is the only channel
  through which the preceding work reaches a judge.
- **Step 5.7** demo rehearsal, timed.
- A **"if the deadline arrives early"** minimum-viable path: 2.1–2.5 → 3.1–3.4 → 3.7 → 5.6.

**Next.** Step 2.1.

---

## Entry 10 — 2026-08-23 — Phase 2: schemas and normalisation

**Did.** Steps 2.1–2.5 and 2.7. (2.6, the shallow schemas, still open.)

- **`AttributeSpec`** gained `label`, `display_uom`, `order`, plus `sheet_label` (falls back
  to title-cased name so snake_case never leaks onto a customer sheet).
  **`CategorySchema.template()`** returns attributes in delivery order — `ATTRIBUTE_LABEL n`
  is a positional contract, and an unrelated edit must not move Voltage Rating from slot 4
  to slot 7 or every downstream diff of the catalog becomes noise.
- **`ontology.generic_schema()`** — 6 TEXT attributes, built in code not YAML so `load_all()`
  keeps meaning "categories this distributor modelled" and the invariants keep applying to
  all of them. **`ontology.resolve()`** never raises on an unknown id.
- **`units.split_value_uom()`** — magnitude preserved exactly as written (`50-1/4`, never
  `50.25`), only the unit canonicalised to a display symbol.
- **`units.DISPLAY_ONLY_UOM`** — a deliberate asymmetry. The sheet writes `47` / `dBA`, so
  the presentation layer must know dBA, CF, Ah, RPM, grit, lm. These are **not** added to
  `UNIT_ALIASES`, because then `parse_quantity` would claim it understood a decibel-A
  weighting and the dimensional verifier would report checks it never made. Verified:
  `parse_quantity('47 dBA').unit is None` still.
- **`units.COLOUR_TEMPERATURE_SHORTHAND`** — `27k` → 2700 K. A real unit collision (k is
  kelvin *and* the kilo prefix); the naive reading makes a warm-white bulb 27,000 K, hotter
  than the sun's surface. Explicit auditable table, not a regex.
- **`normalize.normalise_record`** now persists `AttributeValue.normalized`. Pure
  memoisation — `constraints.build_environment` already computed and discarded it. Verified
  constraint results and run determinism unchanged.
- **Three deep schemas authored**: `abrasive.cutoff_disc`, `decking.board`, `lamp.led`.
- **`test_ontology`** loosened from equality to subset, plus three new invariants: category
  id matches filename, delivery labels unique within a category, template order stable.

**Measured — the demo moment, verified end to end.** Fed the cut-off disc schema a swapped
record (`diameter=.045"`, `thickness=5"`, `arbor=7/8"`):

| verifier | correct record | swapped record |
|---|---|---|
| dimensional | trust 1.0 | **trust 1.0 on all three** |
| constraint | trust 1.0 | **trust 0.0 on all three** |

The dimensional verifier cannot see it — all three are valid lengths in inches. The
constraint verifier catches every one and explains why: *"Values that are individually
plausible but jointly impossible cannot be published at any confidence level."*
This is the single best available evidence for the whole thesis and it should lead the demo.

**Decided.** Deck-board `colour` is free text, not a vocabulary — the palette is seasonal
("Castle Gate", "Whiskey Barrel", "Tide Pool") and a closed list would reject next year's
products. Lamp shapes (T9, B11, PAR38) *are* a vocabulary — ANSI designations, a closed set.

**Gotcha for whoever continues.** Verifier signature is
`verify(value, spec, record)` — spec second, record third. Passing the record second fails
with `'ProductRecord' object has no attribute 'kind'`, which reads like a schema bug and is
not. `VerifierSignal` has `trust` / `applicable` / `detail`, no `verdict` field.

**State.** **502 tests**, lint clean. Six categories load.

**Next.** Step 2.6 (shallow schemas for powertool.cordless, luminaire.fixture,
appliance.major, decking.railing, accessory.driver_bit — 288 rows, 29% of the catalog,
currently routing correctly but falling back to generic for lack of a schema), then Phase 3.

---

## Entry 9 — 2026-08-23 — Plan amended; Phase 2 complete

**Prompt.** "im sure you have an order in the plan.md so add the points that you just said
that will make the project better on the most optimal step and also add these use the ui ux
pro max skill to make a dashboard (front end) for this project to the plan.md on the most
optimal step and continue with the plan"

**Did — plan.md amendments.** Placed the four strategy points from the preceding
assessment at the steps where they are actually cheapest to build:

- **Step 3.8 `--fill-mode`** (right after the emit checkpoint) — `certified` / `grounded` /
  `all`. Insurance against a naive rubric: if "accuracy" is scored as cells populated, our
  deliberate blanks could lose to a team that hallucinated into all 252 columns. The
  uncertainty is never hidden — flagged in the sidecar, excluded from the certificate — so
  this is a switch for a rubric, not a change of position. Default stays `certified`.
- **Step 4.6 naive baseline** — run the catalog with abstention off and measure how many of
  the extra cells are wrong. Converts the thesis from argument to number, understandable
  without knowing what conformal prediction is. Marked highest-value post-checkpoint work.
- **Step 4.7 reviewer-hours on the certificate** — restate the guarantee in the buyer's
  units. Unilog's pain is paying people to check 100% of records.
- **Step 5.6 dashboard**, using the `ui-ux-pro-max` skill (confirmed enabled: 79 styles,
  192 palettes, 25 chart types, 22 stacks). Placed *after* Phases 3–4 because it displays
  certified values, verifier signals and the baseline chart — building it earlier means
  building it twice. Phase 5 renamed "The judge-facing surface".

Added a **"If the deadline arrives early"** section: 2.1–2.5 → 3.1–3.4 → 3.7 → 5.6 yields a
file, an honest abstention policy and a dashboard, with no certified bound — and says so
plainly rather than implying one. Moved the dashboard and the naive baseline onto the
**never-cut** list, because both are the kind of work that feels optional beside "real"
engineering and is not.

**Did — Phase 2 (all seven steps).**

- **2.1** `AttributeSpec.label` / `display_uom` / `order`, all optional; `sheet_label`
  falls back to title-cased name. `CategorySchema.template()` returns attributes in sheet
  order — the ATTRIBUTE_LABEL n columns are a positional contract, so a re-export must not
  move Voltage Rating from slot 4 to slot 7.
- **2.2** `ontology.generic_schema()` (6 TEXT attributes) + `resolve()`, which never raises
  on an unknown id. Built in code, not YAML, so `load_all()` keeps meaning "categories this
  distributor has modelled" and the test_ontology invariants keep applying to all of them.
- **2.3** `units.split_value_uom` — magnitude preserved **exactly as written** (`50-1/4`,
  not `50.25`; the mixed fraction is how the trade writes a dimension). Plus
  `expand_colour_temperature` for the lamp shorthand (`27k` → 2700 K), kept as an auditable
  table rather than a regex.
- **2.4** `normalise_record` now persists `AttributeValue.normalized` — pure memoisation,
  the call already happened inside `constraints.build_environment` and was discarded.
- **2.5/2.6** Six new category YAMLs: `abrasive.cutoff_disc` (the showcase),
  `powertool.cordless`, `appliance.major`, `luminaire.fixture`, `decking.railing`,
  `accessory.driver_bit`.
- **2.7** Loosened `test_every_shipped_category_loads` to a subset assertion.

**Measured.**
- **506 tests** (was 448), lint clean.
- Schema-backed verification now covers **616/1000** rows, up from ~370. 755 route to a
  category; the 139 routed-but-schema-less fall back to generic and still export.
- **The cut-off disc constraint works as designed.** Feeding a swapped
  `dia=.045 / thk=5 / arbor=7/8`: the dimensional verifier returns **trust=1.0 on all three**
  — they are all lengths in inches, so it cannot see the swap — while the constraint
  verifier returns **trust=0.0 on all three** with a readable reason
  (`thickness < arbor_diameter [arbor=22.225 mm, thickness=127 mm]`). This is the concrete
  demonstration that verifiers must be external tools: an ensemble would agree with itself
  on the same wrong assignment.

**Three findings.**

1. **`_expected_dimensionality` resolves the canonical *unit*, not the declared dimension
   string.** So `[force]` vs pint's `[mass]*[length]/[time]**2` never actually compare —
   a typo'd `dimension:` is invisible while a `canonical_unit` is present, and only blows up
   on the fallback path. Added two invariants to `test_ontology.py`: every declared
   dimension must resolve in pint, and it must **agree** with the canonical unit.
2. **Cubic feet is a real unit; dBA is not.** `21CF` (appliance capacity) went into
   `UNIT_ALIASES` as `foot ** 3` so the dimensional verifier can genuinely check it. `dBA`,
   `RPM`, `grit`, `CRI` went into a new `DISPLAY_ONLY_UOM` table used *only* by the split.
   The asymmetry is deliberate: putting dBA in `UNIT_ALIASES` would make `parse_quantity`
   claim it understood a decibel-A weighting and the dimensional verifier would start
   reporting checks it never made.
3. **The 143s full-suite time from Entry 1 was cold-start import cost**, not slow tests.
   Warm it is 8.9s for 506 tests; all 18 pipeline tests confirmed still running via
   `--collect-only` and `--durations`. Nothing is being silently skipped.

**Decided.** Stopped authoring schemas at 616/1000. The remaining tail is 13 categories of
2–24 rows each; marginal coverage is worth less than reaching the emit checkpoint, and the
generic path already exports them honestly.

**State.** 506 tests, lint clean. Phases 0–2 complete, still **uncommitted**.

**Next.** `plan.md` Phase 3 — the emit checkpoint. Step 3.1, the row model and abstention
policy. Nothing in Phase 4+ matters until `crucible enrich` writes a file.

---

## Entry 10 — 2026-08-23 — **The checkpoint is reached: a delivery file exists**

**Prompt.** "continue" (×2), continuing the plan from Phase 2.

**Did.** Phase 3 core: `emit/rows.py`, `emit/writer.py`, `enrich.py`, `crucible enrich`,
`tests/test_emit.py`. The pipeline now runs sparse CSV → 252-column XLSX/CSV end to end.

**Measured — first real run**, 12 products, `--fill-mode grounded`:

```
12 products in 28.8s (2.40s each); 12 routed, 0 generic;
39 values extracted, 216 cells populated
delivery.csv / delivery.xlsx / evidence.csv all written
crucible verify-format -> OK: 252 columns, exact order
```

**Row 1 is the whole thesis in one screen.** Input was
`DCB518ASTS06G Diablo 1/2"x18" - Sanding Belt 6pc` — 47 characters. Output:

| | |
|---|---|
| Dept / Class / Fine | Tools & Equipment / Power Tool Accessories / Sanding Belts, Discs & Sheets |
| ATTRIBUTE_LABEL 1 / VALUE 1 | Product Name / Sanding Belt |
| ATTRIBUTE_LABEL 2 / VALUE 2 | Brand / Diablo |
| **ATTRIBUTE_LABEL 3** | **Material — label present, value blank** |
| **ATTRIBUTE_LABEL 4** | **Color — label present, value blank** |
| ATTRIBUTE_LABEL 5 / VALUE 5 | Size / 1/2"x18" |
| ATTRIBUTE_LABEL 6 / VALUE 6 | Quantity / 6pc |

That is the reference sheet's exact pattern — full label template, values only where the
data supports them — reproduced by policy rather than by coincidence.

Note this row routed to `abrasive.coated`, which has a *taxonomy node but no schema YAML*,
so it fell through to the generic 6-attribute schema and still produced a good row. The
tiered design working as designed: taxonomy known, schema absent, export unaffected.

**Verified.** XLSX keeps `1/2"x18"` and `7/8` as strings with `number_format="@"` — Excel
does not turn them into dates. Sidecar carries provenance, quoted spans, and renders
abstentions as `constraint: abstained (...)` rather than as a trust number, so
non-negotiable #3 survives into the artifact a human opens.

**Design decisions worth keeping.**

- **`EmittedCell.__post_init__` refuses `EXTRACTED` provenance with no spans.** The
  abstention policy is a class invariant, not a convention a future populator can forget.
- **`FillMode.CERTIFIED` with no threshold publishes *no* attribute values** rather than
  all of them. With nothing calibrated there is no basis for a certified claim, and
  refusing is the safe direction to fail. The CLI warns and names the two ways forward.
- **`build_verifiers` in `enrich.py` runs only dimensional + constraint.** Coherence needs
  a corpus and ensemble needs resampling; requiring a study before an export would be
  backwards. The other three join on the calibrated path.
- **Sidecar is a separate file.** Confidence columns interleaved into the delivery file
  would break the 252-column contract; omitting them entirely would make "explainable
  output" a claim rather than a deliverable.

**Two things I got wrong and fixed.** `llm_extract.merge_values` does not exist (it is
`merge`, over two `ProductRecord`s); and a bash heredoc could not carry a Python file with
mixed quoting — fell back to the Write tool rather than fighting the shell.

**State.** **522 tests** (was 506), lint clean. Phases 0–2 complete, Phase 3 core complete.
Still **uncommitted** — this is now a substantial body of working code with no git history.

**Next.** Step 3.8 `--fill-mode` is already implemented as part of the row model; what
remains in Phase 3 is 3.5 (deterministic prose for the description columns) and 3.6
(sidecar polish). But the higher-value move is **Phase 4** — calibration — so that
`--fill-mode certified` has a real threshold instead of publishing nothing.

---

## Entry 11 — 2026-08-23 — Fifth verifier; calibration moves to the real catalog

**Prompt.** "continue" — after I proposed committing then starting Phase 4.

**Commit: asked, declined.** Git had no identity on this machine and the three existing
commits are authored by **Vishalk1604 <krishnakarun16@gmail.com>** — a teammate, not this
user. Rather than guess, I asked; the answer was **"Don't commit yet"**. The work is
**staged but uncommitted** (`git diff --cached --stat`: 40 files, ~7,000 insertions).
Whoever resumes: the staging is already correct and excludes the CRLF churn. `.gitignore`
now covers `runs/` and `daily.index.xml.gz`.

**Did — Step 4.1, the identity verifier** (`assay/identity.py`).

The other four verifiers reason about physics and none of them can say anything about a
part number, which is the most expensive kind of error in distribution — a wrong dimension
gets caught at the counter, a wrong part number ships the wrong product.

The opening is that `Part_Desc` redundantly repeats `Mfg_Part_Num` on **676/1000** rows.
Two channels carrying the same fact, populated by different processes. Comparing them is
comparing independent sources, so this is a *tool*, not a second opinion — no model is
consulted.

Verdicts: exact → 1.0; match only after folding confusables (0/O, 1/I/l, 5/S, 8/B) or a
near-miss ≥82% similar → 0.3; supported by neither channel → 0.0; non-identity attribute →
abstain.

**Measured on the two real pairs from the sample**, not invented ones:

| claim | source | trust | detail |
|---|---|---|---|
| `DCB518ASTS06G` | `DCB518ASTS06G` | **1.00** | matches Mfg_Part_Num exactly |
| `55226BKFLU` | `55226BKLFU` | **0.30** | 90% similar, likely a transposition |
| `174-OCSB3-15A` | `174-0CSB3-15W` | **0.30** | 91% similar |
| `XYZ-99999` | `DCB518ASTS06G` | **0.00** | closest channel 0% |

**Why `doubt` and not `fail` on a fold match.** `0` vs `O` is almost certainly a
transcription artifact; `W` vs `A` at the end of the same string is almost certainly a
different product. One verdict for both would either flag thousands of harmless glyph
variants or wave through real mismatches. So the fold is applied and *named* in the detail,
and the reviewer decides.

**Did — Step 4.2.** Wired into all three suites: `pipeline.build_verifiers`,
`api/session.py::_verifiers` (now five), `enrich.build_verifiers`.

**Did — Step 4.3, `pipeline.run_catalog`.** Calibrates on real products. Clean extraction
becomes a pseudo-reference packed into the existing `GoldRecord` shape, so `assay_values`
and `values_agree` work **unmodified**; `faults.inject_all` then supplies the labels.

⚠️ **The caveat, restated because it will be tempting to forget.** The pseudo-reference
contains the extractor's own errors, so a value it consistently misreads is labelled
"correct" and any verifier flagging it is scored as a false alarm. That noise is
**one-directional**: it deflates AUROC and inflates realised error. It cannot manufacture a
guarantee that does not hold. Being wrong in the conservative direction is the right way to
be wrong for a system whose product is a bound — and it is why `simulated=True` on every
result from this path. Default `alpha` is 0.05 here rather than `run()`'s 0.02, because
promising 2% off noisy labels would be the wrong default.

**State.** **549 tests** (was 522), lint clean. Phases 0–3 core and 4.1–4.3 done.

**In flight at time of writing.** A 120-product `run_catalog` calibration is still running
(background task `brw8nhxk8`) — longer than the ~290s the per-product rate predicts, worth
checking whether Ollama unloaded the model mid-run. **The numbers are not yet measured; do
not quote any until that finishes.**

**Next.** Read that run's output, then Step 4.4 (portable calibration as JSON, so
`--fill-mode certified` gets a real threshold instead of publishing nothing) and Step 4.6
(the naive baseline, the highest-value remaining work).

---

## Entry 12 — 2026-08-23 — **An outage can impersonate an abstention.** The sharpest bug so far.

**Prompt.** "continue" (×2).

**What happened.** Three consecutive `run_catalog` calibration runs died with *zero bytes*
of output — 120 products, then 45, then 6, the last taking >7 minutes for work that should
take 15 seconds. No traceback, no error.

**Diagnosis.** Not our code. **Ollama had wedged.** `ollama ps` eventually showed
`Stopping...`, and the run logs (once written to a file instead of a pipe) were 62 KB of
`httpx.ConnectError` — 42 failed extraction calls. Killing `ollama.exe` + `ollama app.exe`
and restarting `ollama serve` fixed it. The tray app alone does **not** bring the API up;
`ollama serve` had to be started explicitly.

Two process notes worth keeping: piping a long run through `tail` loses everything if the
process is killed — **redirect to a file**. And `logging.disable(logging.INFO)` does not
suppress `logger.exception`, which logs at ERROR.

**The finding that actually matters, and it is not the outage.**

`LLMExtractor.propose` caught the connection errors, logged them, and returned `[]`. The
pipeline then carried on and produced perfectly well-formed delivery rows — **full of blank
cells**.

Those blanks are indistinguishable, after the fact, from the blanks this system produces on
purpose. **The entire product claim is that an empty cell means "we looked and could not
establish this value."** If a dead Ollama yields the identical artifact, that claim is false
whenever the infrastructure hiccups, and nobody downstream can tell which kind of blank they
are holding.

This is the failure mode most likely to be quietly present in a demo: the sheet looks
correct, the abstention story sounds right, and the file is actually an outage report.

**Fixed.**

- `extract/llm.py`: `_is_transport_failure()` walks the `__cause__`/`__context__` chain,
  matching exception *names* (`ConnectError`, `ReadTimeout`, `RemoteProtocolError`, …) so
  the module needn't import httpx. Transport failures now increment
  `ExtractionStats.transport_failures`, separate from `call_errors`. Added
  `stats.reached_the_model`.
- `enrich.py`: new `ExtractionUnavailable`, raised when >5% of calls never reached the
  model. Deliberately **fatal** — every other failure here correctly degrades to a blank
  cell, but an outage is not that. The message says what it means and what to do:
  *"would be an outage, not an assay… Check that Ollama is running (`ollama ps`)."*
  Below 5% it warns and continues, since one lost product on a 1000-row catalog costs one
  product.

**Also did — Step 4.4, portable calibration** (`certify/artifact.py`). JSON, not pickle: a
calibration carries a promise about error rates, and a reviewer has to be able to open it.
Round-trips **exactly** (max score difference 0.0) in 1129 bytes. Schema-fingerprint
interlock refuses a mismatch precisely — `lamp.led: calibrated against 0f60cbd7a628e6cb,
now deadbeef` — while a category the calibration never saw is *not* treated as a mismatch,
because that is a stratum question (Step 4.5), not a reason to reject the file.

**State.** **585 tests** (was 549), lint clean. Still uncommitted; staging intact.

**Still unmeasured.** A 60-product `run_catalog` is running now with Ollama healthy. AUROC,
threshold and realised error on the real catalog are **not yet known** — nothing in this
project should quote them until that lands.

**Next.** Read the calibration result; then Step 4.6 (naive baseline) and Step 5.6
(dashboard), which are the two never-cut items still outstanding.

---

## Entry 13 — 2026-08-23 — The slowdown was a game, not a bug

**Prompt.** "continue" (×2).

**The GPU mystery, solved.** Extraction had dropped from **2.4 s/product to 8.6 s/product**
and I chased it through three layers before finding the cause:

```
ollama ps  ->  qwen3-vl:8b   98%/2% CPU/GPU     (was 13%/87%)
nvidia-smi ->  6815 MiB used, 4% utilisation
```

Memory allocated, compute idle: the model had fallen back to CPU. `nvidia-smi
--query-compute-apps` showed why — **Spider-Man.exe, Brave, the Epic Games Launcher and two
`llama-server.exe` processes** were all on the card. With Ollama fully stopped, **4633 of
8151 MiB was still held**, leaving ~3.5 GB against a 6.2 GB model.

Not a code fault. Not an Ollama fault. **Resource contention on the machine.** Left alone
it silently degrades inference 3.5x while everything still "works", which is exactly the
class of problem this project keeps running into: the failure is invisible in the output.

**Left the game alone** — it is the user's session to close, not mine. Killed only our own
processes. Recorded here because the next person will see the same slowdown and should
check `ollama ps` for the CPU/GPU split *first*, before touching any code.

**Did — Step 4.6, the naive baseline** (`baseline.py`). The comparison that makes an empty
cell legible to someone who has not read the argument: run the same assayed catalog under
`all` / `grounded` / `certified` and report cells populated, error rate, wrong cells
shipped, and review hours.

Two design points worth keeping:

- **All three modes are computed from one extraction.** Re-extracting per mode would let
  model nondeterminism masquerade as a policy effect.
- **`headline()` returns an empty string when there is no trade to describe.** If `all` and
  `certified` populated the same cells, inventing a headline would be spin. It also avoids
  the words *conformal*, *nonconformity*, *coverage* and *alpha* entirely — a judge should
  be able to act on it without knowing any of them. There is a test asserting that.

⚠️ **`baseline._admits` and `EmitPolicy.admits` are two implementations of one rule.** They
are separate because one reasons about labelled scores and the other about
`AttributeValue`s. `test_admits_agrees_with_emit_policy` cross-checks them over the full
mode x score x threshold grid. If they ever drift, the comparison keeps producing plausible
numbers for an exporter that no longer behaves that way.

**State.** **633 tests** (was 585), lint clean. Still uncommitted; staging intact.

**Still unmeasured, third session running.** AUROC / threshold / realised error on the real
catalog. Three attempts have now been lost — two to the Ollama wedge (Entry 12), one to
running at CPU speed. **Do not quote a number for these until a run completes with
`ollama ps` showing GPU-dominant placement.**

**Blocked on the user:** freeing VRAM (closing Spider-Man) is required before the GPU path
returns. Everything else in the project runs fine without it.

**Next.** Once VRAM is free: re-run calibration, then Step 5.6 (dashboard, `ui-ux-pro-max`)
— the last never-cut item.

---

## Entry 14 — 2026-08-23 — VRAM freed; preflight added so this cannot recur silently

**Prompt.** "ive cleared the vram so go wild" → "check now"

**Sequence.** First check still showed Spider-Man live on the card (PID 36340, 26,006 CPU
seconds, 4566 MiB held, only 3326 MiB free). Said so rather than closing it — a running
game is the user's session, and killing it could lose progress. Second check after the user
acted: **0 MiB used, 7891 MiB free, nothing on the GPU.**

Restarted `ollama serve` (again: the tray app alone does not bring the API up), forced a
load with a one-token generate, and confirmed:

```
qwen3-vl:8b   6.2 GB   13%/87% CPU/GPU
```

Back to the documented healthy state, exactly matching `CLAUDE.md`.

**Did — `preflight.py`, and it is the durable fix.** This evening cost four calibration
runs to conditions that were invisible in the output: a wedged Ollama (blank rows that look
like careful abstention) and CPU fallback (identical rows, 3.5x too slow to finish). Both
are the project's own signature failure — *the artifact looks fine while the thing that
produced it was broken* — which is precisely why the pipeline should refuse rather than
proceed.

`check_ollama()` now runs before every `crucible enrich` and raises `PreflightError` when
the server is absent or the model has fallen back to CPU (`MIN_GPU_SHARE = 0.60`).

Three deliberate choices:

- **0.60, not 1.0.** A 6.2 GB model on an 8 GB card normally spills ~11% and runs fine.
  A threshold that rejected this project's own hardware would be worse than none.
- **A cold server with nothing loaded passes.** Ollama loads on first use; refusing there
  would make the first run of the day impossible.
- **Messages name the command, not the condition.** The CPU-fallback error prints the real
  measurement (`98%/2% CPU/GPU`), the diagnostic (`nvidia-smi --query-compute-apps=...`),
  and the escape hatch (`--no-preflight`). The unreachable-server error explains *why* it
  matters: without it the delivery file is blank rows impersonating abstention.

All 16 preflight tests run **offline** via a stubbed `urlopen`. A readiness check that
needed a live Ollama would only ever be exercised on the healthy machines where it never
fires.

**State.** **649 tests** (was 633), lint clean. Still uncommitted; staging intact.

**Running now.** 150-product `run_catalog`, preflight passed, placement holding at 87% GPU.
Fifth attempt at the real-catalog numbers. Still nothing to quote.

**Note for whoever reads this next:** if extraction feels slow, run `ollama ps` **first**
and look at the CPU/GPU column. Do not debug the pipeline until that reads GPU-dominant.

---

## Entry 15 — 2026-08-23 — **AUROC collapsed to 0.532 on real data. Diagnosed and addressed.**

**The first real-catalog numbers**, 150 products, GPU healthy, `1.44 s/product` — the
fastest yet:

```
n_test=236  baseline=0.1483  auroc=0.5324  feasible=False  faults=94
REASON no threshold accepting at least 30 values could be certified at 10.0%
```

**AUROC 0.532 is a coin flip.** On the synthetic valve/bearing corpus the same scorer
reached 0.928. This is the most important measurement of the project so far and it is a
failure, so it goes in the ablation table, not the bin.

**Diagnosis — not a worse model, a coverage hole.** Measured verifier applicability over
772 real values:

| verifier | applies to |
|---|---|
| dimensional | 37.8% |
| constraint | 37.7% |
| identity | **5.7%** |

**~62% of real values received no verifier opinion at all.** Their feature vector is all
zeros, so the scorer has literally nothing to separate them with, and a logistic model over
zeros returns the base rate. AUROC 0.5 is the arithmetically correct answer to "distinguish
these values using no information".

**Why the synthetic corpus hid this.** It was quantity-heavy *by construction* — bores,
diameters, pressure ratings, tensile strengths — so the dimensional and constraint
verifiers fired on nearly everything. A real building-products catalog is dominated by
**nominal** attributes: material, finish, mounting type, wheel type, drive style,
application. Both physical verifiers correctly abstain on every one of them.

This is worth stating plainly: **the abstentions were right, and the system was still
blind.** Non-negotiable #3 says abstention is not approval — and it turns out the deeper
consequence is that a verifier suite whose abstentions dominate is not a verifier suite at
all. The old 0.928 was never a measurement of this domain.

**Addressed — `assay/vocabulary.py`, the fifth verifier.** A NOMINAL attribute declares a
closed vocabulary (`ontology.py` refuses to load one that does not), so the check is exact
and external: *is this term in the list a human authored for this category?* No model
consulted.

- exact member (case/space-folded) → **1.0**
- overlaps exactly one declared term (`"316 stainless steel"` vs `"stainless steel"`) → **0.35**
- ≥86% similar to a term but not equal (`aluminium`/`aluminum`) → **0.35**
- outside the vocabulary → **0.0**, and the detail *lists the allowed terms* so a reviewer
  can fix it without opening the schema
- anything not NOMINAL → abstain

It catches the characteristic failure of a constrained-decoding extractor: a
plausible-sounding term that is not a member of the set. `"brushed nickel"` as a wheel type
reads perfectly and is not a thing that category sells — and no confidence score flags it,
because the model is entirely sure.

**A test caught my own mistake.** The helper built `AttributeSpec(kind=QUANTITY)` with no
dimension; the schema validator rejected it. The validator was right and the test was
wrong — fixed the helper to build specs the real loader would accept, rather than
weakening the validator.

**State.** **666 tests** (was 649), lint clean. Re-calibration with the vocabulary verifier
is running now.

⚠️ **Do not quote AUROC 0.928 anywhere in the submission.** It describes a synthetic
corpus in a domain this project no longer targets. The real-catalog figure is 0.532 before
the vocabulary verifier, and whatever the current run reports after it.

**Next.** Read the re-calibration. If AUROC is still near chance, the honest conclusion is
that verifier *coverage*, not verifier quality, is the binding constraint on this domain —
which is a publishable finding and a far better story than a number that was never measured
here.

---

## Entry 16 — 2026-08-23 — Vocabulary verifier measured; cache break found and fixed; dashboard live

**Prompt.** "continue" (×3).

### 1. The vocabulary verifier works, and the finding stands

Re-ran the same 150 real products, same seed, only the verifier suite changed:

| | before | after |
|---|---|---|
| AUROC | 0.5324 | **0.5988** |
| values with ≥1 verifier opinion | 38% | **88.1%** |
| feasible at α=10% | no | no |

Applicability after: `vocabulary 47.9%`, `dimensional 37.7%`, `constraint 37.3%`,
`identity 2.5%`.

So coverage was indeed the binding constraint — closing 38% → 88% moved AUROC by +0.066.
**But 0.60 is still poor**, and α=10% remains infeasible on real data. Coverage was
necessary and is not sufficient. The honest reading: on this domain the *signals are weak*,
not merely absent. Worth stating exactly that way in RESULTS rather than implying the fix
worked.

### 2. **I broke the harvest cache and did not notice for six entries**

Adding `label`/`display_uom`/`order` to `AttributeSpec` changed `model_dump_json` output,
which changed `fingerprint()`, which changed the harvest cache key. Verified: needed
`00f95d78b9373e4b`, disk had `449bb62e713e257f`. The next `crucible-app` launch would have
spent **~25 minutes re-extracting before serving its first request** — precisely the trap
`CLAUDE.md` documents and Entry 5 claimed to have closed. Entry 5's fix (scoping the key to
used categories) was real but addressed a *different* failure; it does nothing when the
fingerprint of the same category changes.

**Fixed properly**, not by reverting: `fingerprint()` now excludes the three presentation
fields. This is the correct reading of its own docstring — *"a stable hash of a schema's
checkable content"* — because a column heading is not checkable content. Renaming
"Arbor Size" to "Arbor Diameter" changes nothing a verifier examined and must not
invalidate a guarantee.

⚠️ I then wasted several attempts trying to reverse-engineer the *original* hash so the old
cache files would match. Wrong instinct. The right question was whether the cached data is
still **valid**, and it is: `build_prompt` never reads those fields, so the extraction is
byte-identical. Copied the three cache files to their new keys (copy, not move, so the
originals survive if that reasoning is wrong). App now starts in **~1.0s**.

### 3. Dashboard is live and every panel works

Built with the `ui-ux-pro-max` design system (Data-Dense Dashboard, Fira Sans/Code,
light+dark, no build step). Verified in-browser against the running API:

- dial at α=5% → 62.2% automated, bound 0.6%, realised 0.2%, verdict *holds*
- dial at α=0.5% → **verdict switches to a designed refusal state**, stats blank to "—",
  comparison says "no trade to show", download hint updates
- sheet fragment renders `Material` / `Color` as *"not established"* + `no evidence`, and
  clicking explains why with the source text quoted
- SIMULATED banner up; 6 queue items

**Abstention never reads as approval**: dashed border + italic + the literal word
"abstained", never colour alone.

### 4. The demo moment, found by accident in the live queue

```
BRG-00027 · seal_type · proposed "C4"
  dimensional  abstained   seal_type is nominal, not a physical quantity
  constraint   abstained   no constraint in bearing.ball mentions seal_type
  identity     abstained   seal_type is not an identity claim
  vocabulary   0.00        'C4' is not a term seal_type accepts...
  coherence    1.00        'C4' appears in 10% of this category
  ensemble     1.00        identical across 3 samples
```

**The model agreed with itself across three samples. The statistical profile said it looked
normal. Both were confidently wrong.** C4 is a bearing *clearance* code, not a seal type.
Only the external check that knows the actual allowed terms caught it. This is the thesis —
self-consistency cannot correct self-error — demonstrated live rather than argued.

**State.** 666 tests, lint clean. App starts in ~1s. Still uncommitted.

⚠️ **Synthetic AUROC is now 0.992, not 0.928** — the session gained the identity and
vocabulary verifiers. Every prior 0.928 reference is stale. And it is *still the synthetic
valve/bearing corpus*: the dashboard currently shows a domain the input file does not
contain. The SIMULATED banner says the labels are injected but does **not** say the products
are from a different domain. **That gap must be closed before any demo.**

**Next.** Either point the session at the real catalog (right fix, larger), or state the
domain mismatch explicitly on screen (honest, cheap). Then Step 6.1, rewriting RESULTS.md
around 0.5988 rather than any synthetic figure.

---

## Entry 17 — 2026-08-23 — From backend dashboard to an actual product

**Prompt.** "next work on the website, make it like an actual product… interview me." Then,
after a first pass: *"the web page is very static and ai made… think of a web designer…
this is a modern 2026 webpage. use different colour palette as well. not blue."*

**Interviewed in three rounds** (12 decisions, all landed on the recommended option):
marketing landing → demo sign-in → upload-first app; single data manager; dial *after*
processing and live; read-only review with evidence; process the first 50 rows by default
with the full run one click away; all four downloads; and the **C4 catch** as the landing
page's proof.

### Backend built for it
- **`api/jobs.py`** — worker-thread job runner. Threads, not Celery: this starts with
  `uv run crucible-app` on a laptop with no network, and the thread spends its life blocked
  on Ollama's socket so the GIL is irrelevant. `eta_seconds` returns None until three
  products are done, because an estimate from one sample is worse than no estimate — a user
  believes a number and ignores a dash.
- **`enrich(on_row=…)`** — rows are handed to the caller as they finish. A long run that
  reports only at the end is indistinguishable from a hung one.
- Routes: `/`, `/signin`, `/app`, `/certify`, `POST /api/jobs`, `GET /api/jobs/{id}`,
  `GET /api/jobs/{id}/download/{kind}`, `/api/sample`. Added `python-multipart`.

### The design rebuild — and why the first attempt deserved the criticism
The first pass was blue, symmetric, evenly spaced, and could have been any B2B SaaS. It
*was* AI-made in the way that phrase is normally meant.

Second pass, from a real direction: **a crucible is a vessel for melting metal**, so the
palette follows the name rather than the category — **ember `#f4642a` and amber `#f0a202`
on warm iron `#100e0c`**. Warmth also does something functional here: this product's whole
job is admitting uncertainty, and a cold clinical palette makes hedging read as evasion.

Type does the hierarchy: **Newsreader** (editorial serif, 92px headlines) for statements,
**Inter** for interface, **JetBrains Mono** for anything a machine produced — a part number
and a prose sentence must never be confusable.

Motion is **IntersectionObserver + CSS only, no GSAP**. The skill recommended GSAP snippets
and I did not take them: a CDN script is a dependency that fails exactly when an offline
demo needs it. Implemented: staggered scroll reveals, eased count-ups, an animated forge
bloom behind the hero, a paused-on-hover marquee of the six verifiers, a sheen that crosses
the primary button, and per-row landing animation as results stream in.

⚠️ Renaming the CSS tokens silently broke `signin.html` and `app.html`, which still
referenced `--muted`, `--card`, `--border`. Rebuilt both against the new system rather than
aliasing the old names — an alias would have hidden the same break next time.

### An accessibility bug caught by measuring rather than eyeballing
`--fg-faint` was **3.35:1** on the iron base — below the 4.5:1 floor — and it carries real
content: table headers, metric captions, abstain tags. Raised to `#8d8279`, which is
**5.14:1 on `--bg` and 4.70:1 on `--surface`**, the two backgrounds it actually sits on.
Light mode's equivalent went to `#6f6357`.

Also worth recording: my first contrast reading of `--fg-dim` said 3.11:1 and was **wrong** —
it compared against a live background mid-theme-toggle. Computed directly it is 6.67:1 and
fine. Measure the pair you mean, not whatever the DOM happens to be showing.

**Verified end to end in-browser**, not just by unit test: upload → live progress → 6
products in 15s → 111 cells → all three downloads enabled → 6 expandable rows with source
quotes and "not established" markers. No horizontal scroll. Reduced-motion honoured.

**State.** **666 tests**, lint clean. Still uncommitted.

**Next.** Wire the dial page (`/certify`) onto the real-catalog session so the guarantee
shown matches the catalog uploaded — the domain-mismatch gap from Entry 16 is still open.

---

## Entry 18 — 2026-08-23 — **The Solution Guide arrives and reframes everything**

**Prompt.** User supplied the Solution Guide as screenshots (could not download the PDF),
asked for it transcribed to `guide.md` and for a detailed plan to win.

**Did.** Wrote `guide.md` (full transcription, verbatim where it matters) and
`WINNING-PLAN.md` (8 steps, re-prioritised). `plan.md`'s ordering is now superseded — it was
set before we knew what the client scores.

**The headline: our architecture is right and our inputs are wrong.**

### Five things that change

**1. There is labelled ground truth and we never had it.**
`Unilog-Sample_200_Items-Input-vs-Output.xlsx` — 200 rows fully enriched across all 252
columns. *"The most important file in the pack… the only place where you can measure whether
your output is right."*

The entire `run_catalog` construction — pseudo-reference, injected faults, one-directional
label noise, the conservative-floor caveat in Entry 11 — exists **only because we believed no
answer key existed**. It does. That machinery becomes a documented fallback.

**2. Our hand-authored vocabularies are the wrong source.** We wrote ten category YAMLs by
reading descriptions. They supply **161,000 LOV rows** keyed by classpath (with
`Normalized Label` / `Normalized Values`), **~500 approved UOM abbreviations**, and **27,000
manufacturer/brand rows** with exact legal casing, suffixes and ®/™. `VocabularyVerifier` is
architecturally correct and pointed at a table we invented.

This also reframes it for the submission: it stops being "a list we wrote" and becomes
"compliance with the client's controlled vocabulary" — which is *literally one of the three
metrics the guide says judges look for*.

**3. They explicitly reward our differentiator, in their own words:**
> *"Real data is imperfect — say so… Noticing and reporting such gaps is a strength, not a
> failure; a confidence score or a 'needs human review' flag is a genuinely valuable feature."*

And the example they give of a gap worth noticing is *"at least one row where the manufacturer
and brand look mismatched"* — which is the **Rheem Manufacturing / FRIGIDAIRE®** row recorded
back in Entry 4, already flagged by our identity verifier. The brief is describing our feature
and telling us to lead with it.

**4. "Depth beats breadth" inverts our strategy.** We optimised for coverage — 75.5% routing
across 1,000 heterogeneous rows. The guide: *"One category done fully… demonstrates more than
a thin pass over all 1,000 rows,"* and names **Faucets** as the ideal demo scope
(`FAUCETS_LOV.xlsx` has fixed attribute order, fixed title word order, permitted values,
synonyms). New plan: faucets as the argument, the 1,000-row run as the proof of generality.

**5. Format compliance is most of the marks.** *"Getting these formats right is most of the
task."* Five descriptions at five lengths and casings with character limits
(`INVOICE_DESC` ≤40 CAPS, `MOBILE_DESC` 60–80). The composer step was already priority one;
the guide makes it priority one **and supplies the formulas**.

### Measured, to make the gap concrete

Ran the column-fill analysis on our own output: **25 of 252 columns carry any value; 227 are
always empty** — including all 6 description columns and all 20 `ITEM_FEATURES`.

⚠️ **The distinction that matters and that I had been blurring:** our abstention thesis
defends a blank *attribute value* — we looked, nothing supported it. It does **not** defend a
blank *description*, because descriptions are composable from values we already verified.
Those 26 are empty because we never built the composer, not because evidence was missing. A
judge cannot tell the two kinds of blank apart and will read all 227 as one kind.

### One thing we got right by instinct

`split_value_uom` preserves `50-1/4` rather than normalising to `50.25`.
`Decimal_Fraction.xlsx` confirms that is the required direction — decimals in, fractions out,
because manufacturers publish decimals and trade buyers search fractions.

### Blocked

**Seven reference files are not in the repo** and Steps 2–5 of the winning plan cannot start
without them. Only the composers (Step 1) and concurrency (Step 6) can proceed meanwhile.

⚠️ The guide warns the sheets are messy — merged cells, multi-row headers, notes in stray
columns, and `Decimal_Fraction.xlsx` is *four stacked pairs of columns, not one*. Every
loader must inspect before it parses.

**State.** 666 tests, lint clean, nothing running, still uncommitted.

**Next.** Step 1, the description composers — the only high-value work not blocked on files.

---

## Entry 19 — 2026-08-23 — Only two files exist; composers built anyway

**Prompt.** "sorry but we only have the input and output file, i cant find the other files
anywhere on there website"

**Did.** Revised `WINNING-PLAN.md` Step 0 for the real file set, then built Step 1 — the
description composers — which was the one high-value item not blocked by the missing files.

### What the two files actually give us

The Expected Output sheet carries **2 fully enriched ground-truth rows**, both dishwashers,
and **every description field is exemplified in them**. That is enough to derive the
templates by reading rather than guessing. Substitutes for the rest:

| Missing | Substitute |
|---|---|
| 200-row ground truth | 2 exemplar rows for templates; hand-labelling for calibration |
| LOV (161k) | Our authored vocabularies — **but never call it "LOV compliance"** |
| Manufacturer list (27k) | Derive a canon from the 76 distinct `Part_Manuf` values |
| UOM standards (~500) | The guide transcribes the house rules; `units.py` has most |
| Decimal_Fraction (63) | **Reconstructible** — it is just n/64 arithmetic |

⚠️ **The line that now governs what we claim:** *"A fluent description made of invented
values scores zero."* Without the LOV we cannot verify our vocabularies are the approved
ones, so the verifier is described as *"values constrained to a per-category controlled
vocabulary"* and **never** as LOV compliance. Claiming their standard while using ours is
the one move that could actually lose this.

### Built: `emit/compose.py`

Five fields plus feature bullets, all deterministic templates over verified values. The
claim this earns is exact rather than rhetorical: **a composed description cannot contain a
fact that was not verified** — there is no path from source text to output prose that does
not go through a verified `AttributeValue`. Grounding is inherited, so the sidecar cites a
source quote per clause.

Two details only the reference pair could settle:
- **`INVOICE_DESC` closes units up** (`120V`, `50-1/4IN`, `41DBA`) while every other field
  spaces them (`120 V`). Both conventions are real; both encoded.
- **Row 2 drops the manufacturer from `MOBILE_DESC`** because manufacturer and brand are the
  same company. Concatenating blindly gives "Whirlpool Corporation Whirlpool, …".

**Deliberately not built: `MARKETING_DESCRIPTION`.** Row 2's is genuine manufacturer copy
("Load more and run less with our quietest…") and cannot be derived from six input columns.
Leaving it empty is a correct abstention; writing plausible marketing prose is precisely
what the guide says scores zero.

**Validation:** `MOBILE_DESC` reproduces the reference row **character for character**
(75 chars, identical string). `INVOICE_DESC` matches on length, casing and unit style.
`SHORT_DESC`/`RETAIL_DESC` differ only in *which* attributes they select — the reference
picks "key" attributes the LOV would designate, which is the documented relaxation.

### Three real bugs found by running it

1. **`extra` is keyed by the source file's own column names** (`E1_Brand`,
   `part_manuf_name`), not the lowercase slugs I had guessed in `rows.py`. Four passthrough
   columns — E1_Brand, DIB_Brand, Part_Manuf, MANUFACTURER_NAME — were **silently empty on
   every row**. Fixing that alone recovered four columns.
2. **Brand never populated** on the first 20 products: every brand column is a placeholder
   while the description names the brand ("Diablo"). Now falls back to the extracted,
   grounded `brand` value.
3. **Cut-off wheels produced no descriptions at all** — that schema has no noun-shaped
   attribute, so there was nothing to build a sentence around. Now falls back to the
   router's Fine class, singularised: `Cut-Off & Grinding Wheels` → `Cut-Off Wheel`. The
   reference row confirms the rule — its Product Name "Dishwasher" *is* its Fine class
   "Dishwashers" singular.

⚠️ Singularisation needed two passes. `Cut-Off & Grinding Wheels` first gave `Cut-off` —
dropping the shared head noun *and* mangling the hyphen. Fine classes put the shared head at
either end depending on how they were written, so: take the first listed term, and borrow the
head noun from the end only when that term is a bare modifier.

### Measured

**25 → 41 of 252 columns** carry a value. All five descriptions now populate **20/20**
products (was 11/20 before the fallbacks). Rate unchanged at 1.39 s/product.

### A test that was wrong twice before it was right

`test_no_clause_is_unverified` is the load-bearing test — it is what makes the "cannot
contain an invented fact" claim checkable. First version split on whitespace and rejected
`120V` (a *legitimate* closed-up unit). Second split on digit/letter boundaries and shredded
the part number `pdsh4816af`. The correct check is **substring containment against the
concatenated verified corpus with punctuation stripped**, which tolerates both legitimate
reshapings and still catches one invented word — with a companion test proving it has teeth.

**State.** **692 tests** (was 666), lint clean. Still uncommitted.

**Next.** Step 5, the remaining derivable columns (`Standard/Approvals`, `With`, `Includes`,
`Selling Qty/UOM`, dimension pairs) — should take 41 → roughly 55.

---

## Entry 20 — 2026-08-23 — Commerce columns; 61 of 252 on a broad run

**Prompt.** "continue"

**Did.** Winning-plan Step 5 — `populate_commerce` in `emit/rows.py`, covering the columns
a PIM filters and ships on.

**What it fills**, all re-presentations of already-verified values moved into the dedicated
column the format gives them:

- `LENGTH` / `WIDTH` / `HEIGHT` / `WEIGHT` / `VOLUME` + their `_UOM` pairs
- `Selling Qty` / `Selling UOM` — `6pc` → `6` + `PK`, a bare number → `EA`
- `Application` — from `material_application` / `application` / `location_rating`
- `With` — from `additional_information`

**Measured on 120 products across many categories:**

```
baseline 25  ->  composers 41  ->  +commerce  61 / 252
```

All five descriptions populate **120/120**. `ITEM_FEATURES_1` 70/120, `Application` 44/120,
`Selling Qty` 37/120. Rate 1.33 s/product.

### Two refusals worth recording, because both were tempting

**1. Image and document filenames.** The reference rows fill `Product Image` with
`FRIGIDAIRE_PDSH4816AF.jpg`, four `Alternate Image` slots, and
`..._Specification_Sheet.pdf`. The convention is plainly `{BRAND}_{MPN}[_n].ext` and I could
synthesise all seven columns for every product in about a minute — an instant +7 columns.

**Not doing it.** A filename is a claim that a file exists. We hold no images and no
datasheets, so emitting one would be a confidently-formatted assertion about something
nobody looked for — the precise failure this system exists to prevent. And
`Actual Image (Yes/No)` = `Yes` would simply be false.

That decision is pinned by `TestRefusals`, which asserts eleven such columns stay empty. If
someone later "improves coverage" by generating them, a test fails and says why.

**2. Forcing category dimensions into generic columns.** The sheet offers only LENGTH /
WIDTH / HEIGHT / WEIGHT / VOLUME. A cut-off wheel has `disc_diameter`, `thickness` and
`arbor_diameter`; a board has `nominal_thickness`. Mapping a wheel's diameter to WIDTH would
put a value under a label that does not mean it. Only genuine same-name dimensions map;
`nominal_width` → WIDTH is added because it *is* a width. Everything else stays in the
attribute grid under its own correct label.

**State.** **719 tests** (was 692), lint clean. Still uncommitted.

**Next.** Step 6 (concurrency — 1.33 s/product is 22 min for 1,000 and "scale efficiently"
is an explicit criterion), or Step 3 (an evaluation harness against the 2 ground-truth rows
plus character-limit and vocabulary-compliance metrics, which the guide says judges look for).

---

## Entry 21 — 2026-08-23 — The three metrics the guide names, measured

**Prompt.** "do it" — build the evaluation harness (winning-plan Step 3).

**Did.** `evaluate.py` + `crucible evaluate`, implementing exactly the three metrics the
guide says *"judges will look for"*, each with an honest adjustment for the data we have.

### Results — 2 labelled rows, compliance over 60 products

```
FIELD-LEVEL ACCURACY          40% exact / 40% normalised over 2 rows
  2/2  Mfg_Part_Num, MANUFACTURER_PART_NUMBER, Part_Desc, Dept, Class, Product Name
  0/2  BRAND_NAME, MANUFACTURER_NAME       (no manufacturer/brand list)
  0/2  Fine, Classpath                     (our taxonomy labels differ from theirs)
  0/2  all five descriptions               (structure matches; attribute selection differs)

CHARACTER-LIMIT COMPLIANCE
  INVOICE_DESC <= 40 chars       60/60 = 100%
  INVOICE_DESC upper case        60/60 = 100%
  MOBILE_DESC 60-80 chars        57/60 =  95%
  SHORT_DESC/LONG_DESC1/RETAIL_DESC space their units   60/60 = 100%

CONTROLLED-VOCABULARY COMPLIANCE   36/84 = 43%
```

### The 43% is the most useful number in this entry

It splits into two entirely different causes, and the examples make the split visible:

- **Our vocabularies are wrong for the trade.** `wheel_type = 'Cut-Off'` fails because our
  vocabulary lists ISO forms (`type 1 flat`, `type 27 depressed center`) while the catalog
  writes trade terms. `material_application = 'Steel'` fails against a list containing
  `metal` and `stainless steel`. These are authoring defects, not extraction errors.
- **Genuine extraction errors.** `abrasive_grain = 'Metal Cut-Off Disc'` is a product name
  in an abrasive-grain field — exactly what the verifier exists to catch.

So the vocabulary verifier is working on both counts, and 43% is a to-do list rather than a
verdict. Fixing the first cause is cheap and would move this number a long way.

### Three honesty constraints built into the code, not the prose

1. **`Accuracy.is_indicative` is False below 10 rows**, and `format_report` prints
   `over 2 labelled row(s)` next to every percentage plus a caveat line. Two rows can
   produce a percentage as readily as two hundred can; a percentage detached from its
   sample size is how a worked example gets quoted as a rate. `TestSampleSizeHonesty`
   asserts the caveat is actually rendered, not merely available.
2. **Never "LOV compliance."** The report says *"measured against the per-category
   vocabularies this project authors, not the client's LOV, which was not supplied."* A
   test asserts the string `LOV compliance` never appears. Claiming conformance to a
   standard we have never seen is the one move the guide says scores zero.
3. **A blank in the answer key is not scored.** The delivery sheet leaves cells blank on
   purpose; scoring ourselves against a blank would reward filling it, which is the whole
   behaviour this project exists to avoid.

⚠️ `normalise()` deliberately does **not** fold `50-1/4` into `50.25`. Case, whitespace and
®/™ are formatting; a fraction versus a decimal is a genuine disagreement about how a
dimension is written, and the guide says the fraction is required.

**State.** **744 tests** (was 719), lint clean. Still uncommitted.

**Next.** Either fix the vocabularies the 43% just exposed (cheap, high-value, and the
evaluation harness now measures whether it worked), or Step 6 concurrency for the scale
number.

---

## Entry 22 — 2026-08-23 — Vocabulary compliance 43% → 76%, and a diagnostic that was wrong

**Prompt.** "continue" — act on what the 43% exposed.

**Measured first, using the harness from Entry 21.** Dumped every out-of-vocabulary value
across 120 products. Three distinct causes, only two of them ours:

| Cause | Example | Fix |
|---|---|---|
| Vocabulary wrong for the trade | `wheel_type` matched **0 of 38** values | rewrite the list |
| Abbreviation never expanded | `Alm`, `Rnd`, `SS`, `Wh`, `Bltln` | extend synonyms |
| Genuine extraction error | `abrasive_grain = 'Metal Cut-Off Disc'` | leave flagged — correct |

⚠️ **My first diagnostic was wrong and nearly caused a bad fix.** It showed `bit_type`
holding `'dryer'`, `'dishwasher'`, `'washer'` — which reads as appliances being misrouted to
`accessory.driver_bit`, a serious router bug. It was not. The script keyed values by
`sheet_label`, and both `accessory.driver_bit.bit_type` and `appliance.major.appliance_type`
carry the label **"Product Name"**, so the two collided. Re-keyed by the record's actual
`category_id`, the routing is correct. **Aggregate by the identifier, never by the display
name** — display names are chosen to be human-friendly and are therefore not unique.

### Fixes

**1. Synonyms became candidate lists.** `SYNONYMS: dict[str, tuple[str, ...]]`, with the
gate taking the first candidate the attribute's own vocabulary permits. That is what lets
one abbreviation mean different things in different categories: `SS` is a 316 grade on a
valve body and plain stainless steel on an appliance finish, and **both are correct**. A
flat dict could not express that. Added trade abbreviations observed in real extractions —
`alm`, `rnd`, `sq`, `str`, `horiz`, `wh`, `bk`, `bltln`, `sq edge`, `decking`, `cut off`.

**2. `wheel_type` rewritten from measurement.** The original held ISO type codes
(`type 1 flat`, `type 27 depressed center`) and matched **0 of 38** real values, because this
catalog writes "Cut Off", "Grinding", "Dual Metal". Now: cut-off, grinding, combination,
flap, wire brush, diamond.

The note left in the YAML is the point: *a vocabulary the data never uses is not a
controlled vocabulary, it is a filter that rejects everything.* The verifier was working
perfectly and the list behind it was wrong — which is exactly the failure a compliance
metric exists to surface, and would have been invisible without one.

**3.** `material_application` gained `steel`; `board_type` gained `decking`.

### Result, same 120 products

```
CONTROLLED-VOCABULARY COMPLIANCE   43%  ->  76%   (158/207)
```

Remaining failures are now mostly genuine extraction errors — `abrasive_grain =
'Metal Cut-Off Disc'` (a product name in a grain field), `material_application = 'DKO Metal'`
— which is the verifier doing its job rather than a list to extend.

### One number went down, honestly

`MOBILE_DESC 60-80 chars` reads **77%** at n=120, against 95% at n=60. Not a regression: a
wider sample includes more products whose descriptions are too sparse to reach 60 characters
without padding, and the composer refuses to pad. That is a real limitation of the input,
not a defect, and the fix is not available to us — inventing words to hit a character floor
is precisely what the guide says scores zero.

**Character-limit compliance otherwise 100%** across all four other checks at n=120.

**State.** **744 tests**, lint clean. Still uncommitted.

---

## Entry 23 — 2026-08-23 — Concurrency: correct code, 1.09× ceiling, and the reason is VRAM

**Prompt.** "continue" — winning-plan Step 6, the scale number.

**Did.** Rewrote `enrich()` to process products concurrently on a thread pool, then measured
whether it helped. It barely does, and the reason is worth more than the speedup would have
been.

### Measured, 40 products, model pre-warmed

**Default server (`OLLAMA_NUM_PARALLEL` unset):**

```
concurrency=1   49.9s   48.1 products/min   1.00x
concurrency=2   45.6s   52.6 products/min   1.09x
concurrency=4   45.7s   52.5 products/min   1.09x
concurrency=8   45.6s   52.6 products/min   1.09x
```

**Then with `OLLAMA_NUM_PARALLEL=2` and the server restarted:**

```
concurrency=1   50.4s   47.6 products/min   1.00x
concurrency=2   46.2s   52.0 products/min   1.09x
concurrency=4   46.2s   52.0 products/min   1.09x
```

**Identical.** Setting the server's parallel-slot count changed nothing.

### Why — and this is the finding

The plateau lands at **two workers and never moves**, which is the signature of a server
serving one inference slot. The 9% that *is* there is our own CPU work — routing,
normalising, assay, row building — overlapping with the model's inference. **No inference
runs in parallel at all.**

The cause is VRAM, not scheduling. A 6.2 GB model on an 8.15 GB card leaves ~1.9 GB, and
each additional parallel slot needs its own KV cache. Ollama accepts `OLLAMA_NUM_PARALLEL=2`
and then quietly declines to grant a second slot it cannot fit. Nothing errors; throughput
simply does not move.

**1.09× is the hardware ceiling on this laptop, and no amount of client-side scheduling
changes it.** I would rather record that than present 9% as a win.

### The code stays, and here is the honest case for it

It is correct, it is tested, and it is the right shape for the machine this would actually
run on. On a card with room for four slots the same code gives real parallelism with no
change. The claim to make in the submission is therefore about the *architecture*, not this
laptop: **the pipeline is concurrency-ready; the demo hardware is the constraint.** Stating
it the other way round — quoting a projected 6-8 minute figure we never measured — is the
kind of claim this whole project exists to refuse.

Revised honest scale figure: **1.14 s/product ≈ 19 minutes per 1,000** on 8 GB.

### What the tests actually guard

Concurrency is worthless if it changes the answer, so `test_concurrency.py` is almost
entirely about determinism: output order must match input order at 1, 2, 4, 8 and 16
workers, and the full 252-column rows must be **byte-identical** between sequential and
8-way parallel runs. A delivery file whose row order depended on which HTTP response
returned first would diff differently on every run against identical input, which makes it
useless for review.

Results are buffered and released in input order rather than as they complete. The shared
lock is held only around the mutable counters on `CascadeRouter` and `LLMExtractor` — never
across the HTTP call, which would serialise the one part worth overlapping.

**State.** **759 tests** (was 744), lint clean. `crucible enrich -j N` exposes it.

**Next.** Nothing on the winning plan is both unblocked and high-value except Step 7
(submission framing). The remaining measurable gaps need the reference files.

---

## Entry 24 — 2026-08-23 — RESULTS.md rewritten from measurement; the last honesty gap closed

**Prompt.** "continue"

### 1. `docs/RESULTS.md` was actively misleading

It still described **600 generated ball valves, bearings and hex screws at AUROC 0.928** —
a different product domain, a stale figure, and the document a submission would cite.
Rewritten end to end from numbers measured on this machine, each with the command that
reproduces it and its caveat *beside* it rather than in a footnote.

Contents now: the input's real shape, coverage 25 → 41 → **61 of 252**, the three metrics
the guide names, the six verifiers and the C4 catch, real-catalog certification, and a
full ablations section — Icecat 0/999, the coverage-not-quality finding, the wrong-vocabulary
finding, and the concurrency ceiling.

**Two of my own quoted figures were wrong and I caught them by re-measuring rather than
trusting my notes:**

- `E1_Brand` usable: I wrote 201, actual **197**
- descriptions containing their own part number: I wrote 676, actual **699**

The second is not a correction so much as a disclosure: 676 came from a stricter
normalisation than the one the identity verifier actually performs. Both figures now appear
with a note explaining the discrepancy, because a reader who finds 676 in this diary and 699
in RESULTS deserves to know which is which and why.

### 2. The `/certify` domain mismatch is closed

Flagged open since Entry 16. The dial page ran on the synthetic corpus, so a judge who
uploaded sanding belts and clicked "Guarantee" was looking at ball valves with nothing on
screen saying so. The SIMULATED banner said the *labels* were injected; it never said the
*products* were from another domain.

The banner now prints the source's own label and caveat:

```
▲ SIMULATED — Generated corpus — ball valves, bearings, hex screws —
  These products are generated, not real... The domain is also not the one in
  the supplied input file: this corpus is quantity-heavy by construction, so the
  physical verifiers apply to nearly every value and the numbers are
  correspondingly better than the real catalog achieves.
```

`CatalogSource.label` and `.caveat` are **required** fields, not optional decoration, so a
future source cannot be added without saying what it is.

⚠️ Caught on inspection: the word SIMULATED printed twice, once from the markup and once
from the caveat's own opening. De-duplicated. Small, but a banner that stutters reads as
boilerplate, and this one needs to be read.

**State.** **759 tests**, lint clean.

⚠️ **Still uncommitted** — this is now 40+ files and every phase of the project. The
staging from Entry 11 is still valid and still excludes the CRLF churn; it needs only the
authorship decision (the repo's existing commits are a teammate's identity, this machine is
the user's).

---

## Entry 25 — 2026-08-23 — Entry-point docs rewritten; an invented number found in our own README

**Prompt.** "continue"

### The README contained exactly the claim this project exists to refuse

Its first screen showed an illustrative dial:

```
Maximum acceptable error rate:  [ 2.0% ]
  ✔ Auto-publishing 9,724 of 12,400 attribute values  (78.4%)
    Certified ≤2.0% error at 95% statistical confidence
```

Every one of those figures was invented for illustration, and the real system **refuses to
certify below 15%** on this catalog. A fabricated guarantee, in the first screen of the
document a judge reads, in a project whose entire argument is that fabricated confidence is
the problem. It had been there since before the pivot and I had read past it repeatedly.

Rewritten around measured numbers only, with a **"What this does not do"** section stating
the four real limits — no asset retrieval, no distributor-internal identifiers, vocabulary
measured against our own lists, calibration labels from injected faults — up front rather
than left to be discovered.

The new opening example is a real row from a real run, including its empty `Material` cell,
because that cell is the product.

### CLAUDE.md brought current

Thesis section left alone; it is still accurate. Updated the pipeline shape (ingest → route
→ … → compose → emit), the key-module list, and the state section. Two corrections written
in explicitly, because both contradict guidance this file's own history gave:

- **Icecat is dead** — `HANDOFF.md` had it as priority 1; measured 0/999.
- **AUROC 0.928 is stale** and describes a domain no longer targeted. Synthetic now reads
  0.992 with six verifiers; the real catalog reads **0.662**.

Added six traps to the list, every one paid for this session: check `ollama ps` before
debugging slow extraction; an outage can impersonate an abstention; `fingerprint()` must
exclude presentation fields; `extra` is keyed by source column names not slugs; aggregate by
`category_id` never by `sheet_label`; never say "LOV compliance".

**State.** **759 tests**, lint clean. Docs now consistent with the code and with each other.

---

## Entry 26 — 2026-08-23 — SUBMISSION.md; and I nearly shipped an unverified claim

**Prompt.** "continue" — winning-plan Step 7, submission framing.

**Did.** Wrote `SUBMISSION.md`: the argument, the C4 demonstration, a mapping to the four
Expected Outcomes and to the approaches the brief suggests, the three metrics, the negative
results, an explicit "what this does not do", and a timed five-minute demo script.

### The thing worth recording

The first draft contained this:

> *"Our identity verifier flags that pairing unprompted."*

— about the Rheem Manufacturing / FRIGIDAIRE® mismatch the guide mentions. It read well. It
was the tidiest paragraph in the document. **I tested it before shipping and it is false:**

```
brand=FRIGIDAIRE vs manufacturer=Rheem Manufacturing
  trust=1.0  detail: brand 'FRIGIDAIRE' appears verbatim in the source
```

`IdentityVerifier` checks a brand against the **description**, not against the
**manufacturer**. It has never compared those two fields. I had carried the association since
Entry 4 — *we noticed the mismatch* — and silently upgraded it to *we detect the mismatch*,
which is a different claim entirely.

In a submission whose whole argument is that confident-sounding unverified output is the
problem, that paragraph would have been the single worst thing in it.

**Why I did not just implement the check.** Brand and manufacturer legitimately differ:
Diablo really is Freud's brand, Milwaukee Accessory really does sell Milwaukee. Deciding
which pairs are legitimate needs an authority, and the authority is
`UniCat_Manufacturer_and_Brand_List.xlsx` — 27,000 approved rows, named in the guide, not
published with the sample pack. Without it the check fires on every honest pairing or on
none. Shipping a noisy check to make a sentence true would have been the same error wearing
a different hat.

Rewritten as **a precisely specified gap**: we found the row, we name the file that would
close it, and we say plainly that we do not currently catch it. That is a better paragraph
than the false one, and it is the same discipline the rest of the submission runs on.

⚠️ **Method note for whoever writes the pitch:** every capability claim in `SUBMISSION.md`
should be executed before it ships. The ones in there now have been. This one was caught
only because I ran it.

**State.** **759 tests**, lint clean. Docs complete: README, SUBMISSION, RESULTS, guide,
plan, WINNING-PLAN, CLAUDE, DIARY.

---

## Entry 27 — 2026-08-23 — Demo rehearsal. Four defects, three in my own script.

**Prompt.** "continue" — run the five-minute demo end to end and fix what breaks.

**Every step verified against the live app**, not read. Results:

| Step | Result |
|---|---|
| 1 Landing | 1.2 s to interactive; all 22 reveals fire; the C4 card renders with its six verdicts and the caught row highlighted |
| 2 Upload | 8 products in **18 s** (2.07 s each, model cold); rows land live |
| 3-4 Product detail | `Material` → *"not established"* / *"no evidence"*; filled rows carry their source quote |
| 5 Downloads | xlsx / csv / evidence all HTTP 200 with real bytes |
| 6 Dial | verified — see below |
| 7 Evaluate | works, but **47 s** at `--limit 30` |

### Three of the four defects were in the script I wrote yesterday

**1. Step 6 quoted the wrong catalog's numbers.** I had written *"drag to 15%: realised error
3.2% against a 12.3% baseline"*. Those are **real-catalog** figures from the CLI path;
`/certify` runs the **generated** corpus, where the baseline is 30.3%. Presenting one page's
numbers while pointing at another is exactly the conflation this project exists to prevent.

**2. Step 6 claimed a refusal that does not happen.** *"Drag to 2%: it refuses."* Measured:
α=2% is **feasible** at 62.2% automation. The synthetic corpus only refuses at **α ≤ 0.5%**,
which happens to be the dial's minimum. Corrected to: 5% holds (62.2% auto, bound 0.6%,
realised 0.18%), far left refuses.

**3. Step 7 is too slow to run live.** `crucible evaluate` re-runs the pipeline twice — once
against the labelled rows, once for compliance. 47 s of silence mid-demo. Now documented:
run it beforehand, or launch it at step 1 and let it finish while you talk.

### The fourth was real, and stale-process

The banner printed **SIMULATED twice**. I had de-duplicated it in Entry 24 and verified the
*file*, but the running app still held the old module. `pkill -f "crucible-app"` did not match
— the process is `python.exe` running uvicorn, not a command by that name. Killed it properly
and the banner is now clean.

⚠️ **Verifying a source file is not verifying a running service.** Restart, then check the
endpoint.

### Two operational notes now in the submission

- **Warm the model first.** Ollama unloads after a few minutes idle; a cold first upload
  costs 10-15 s before the first row appears. `ollama ps` must read `13%/87% CPU/GPU`.
- **Read the `/certify` banner aloud.** It names the generated corpus. Do not let a judge
  assume those numbers describe their uploaded file.

**State.** **759 tests**, lint clean. Demo rehearsed and the script corrected against
measurement rather than memory.

---

## Entry 28 — 2026-08-23 — All four rehearsal defects closed; vocabulary 76% → 79%

**Prompt.** "solve the errors and continue"

### The four rehearsal defects, verified closed

| Defect | Verified |
|---|---|
| Step 6 quoted real-catalog numbers on the synthetic page | `62.2% auto-published` now in SUBMISSION.md; the `12.3% baseline` claim is gone |
| Step 6 claimed a refusal at α=2% that does not happen | corrected to 5% holds / far-left refuses |
| Step 7 too slow to run live | documented with its 47 s measurement |
| Banner printed SIMULATED twice | live endpoint now returns a caveat that does not repeat the word |

### Then the remaining real one: qualified vocabulary terms

Catalogs qualify their terms. This data writes **"Metal Cut-Off"** where the vocabulary says
`cut-off`, and **"Grinding Wheel"** where it says `grinding`. Those were being rejected as
out-of-vocabulary even after the Entry 22 rewrite.

Added a third matching stage to `_from_vocabulary`: exact → unambiguous prefix → **contains
exactly one term, matched on whole words with hyphens and spaces treated as the same
separator.**

The "exactly one" is the whole safety story, and it is tested from both sides:

```
'Metal Cut-Off'          -> 'cut-off'                  resolved
'Grinding Wheel'         -> 'grinding'                 resolved
'Dual Metal'             -> 'Dual Metal'               no term inside it, left alone
'Stainless Steel Metal'  -> 'Stainless Steel Metal'    TWO terms, left for review
'Metal Cut-Off Disc'  (as abrasive_grain)              still flagged
```

That last one matters most. `Metal Cut-Off Disc` proposed as an abrasive *grain* is a genuine
extraction error, and containment must not launder it into something valid-looking just
because a wheel-type term happens to sit inside the string. It stays flagged.

**Result: 76% → 79%** on 120 products. Modest, and the remaining 21% is now almost entirely
the verifier working correctly — marketing names (`Performance+`, `Ceramic+`) and cross-field
mistakes (`Cut and Grind` in an application field). That is the right place to stop pushing
this number: further gains would mean widening vocabularies to accept values that are
genuinely wrong.

### Two process notes

⚠️ **A heredoc silently ate `\b` from my regex.** The written file had no word boundaries and
`Metal Cut-Off` kept failing while `Cut Off Disc` worked — a confusing, half-working state.
Rewrote the patch as a scratchpad `.py` file applied with the interpreter rather than fought
through shell quoting. For anything with backslashes, do not use a heredoc.

⚠️ **A blocked tool call is not a completed one.** An earlier `cat >>` appending these tests
was interrupted by a classifier timeout. `pytest` then reported 31 passed — the same count as
before — and only counting told me the tests were never written. Check the count, not the
colour.

**State.** **768 tests** (was 759), lint clean. RESULTS.md and SUBMISSION.md updated to 79%.

---

## Entry 29 — 2026-08-23 — Stress test on unseen input; two real bugs found and fixed

**Prompt.** "tell me how much is done... any errors that need fixing... is the model test ready"

**Did.** Built a deliberately hostile unseen catalog — **different column names entirely**
(`PART_DESC`, `MFG_PART_NUM`, `Manufacturer`, `Brand`, `Notes`), different order, an extra
column, empty and whitespace-only descriptions, a 400-character description, embedded commas
and quotes, Unicode (`Café™`, Japanese), and product categories with no schema (faucets,
fittings, a label printer).

**It survived.** 10/10 processed, no crash, `verify-format` clean at 252 columns in exact
order, 37 columns populated. Empty descriptions correctly produced no description rather
than a fabricated one. Tolerant column inference did its job.

### Bug 1 — a unit printed twice, in a customer-visible cell

```
SHORT_DESC: Café™ CES700P4MW2 Range, 5.7 cu ft CF Capacity, white
                                         ^^^^^^^^
```

`split_value_uom` could not parse a **two-word** unit, so "cu ft" stayed glued to the
magnitude — and then the schema's declared `CF` was appended after it.

Fixed both halves, and the second is the one that matters generally: **a declared unit may
fill an empty slot but must never be appended to a magnitude that already carries one.**
That guard now covers composites (`24 in W x 24-1/4 in D`) and unrecognised suffixes
(`6pc`) too. Six regression tests.

### Bug 2 — NFKC eats the trademark sign

`clean_text("Café™")` returned `CaféTM`. NFKC decomposes U+2122 into the letters "TM" while
leaving U+00AE (®) alone — an inconsistency in the standard, not the data. The guide requires
brand names to match the approved list *"exactly, symbols and all"*, so this is a data defect.

Shielded across normalisation and restored afterwards. ⚠️ The shielding had to go *around*
NFKC rather than into `_CHAR_FIXES`, because that table runs before normalisation for a
reason recorded in its own docstring: NFKC decomposes the double prime used for inches, and
folding it early would destroy the inch/foot distinction. Verified both still parse.

### The honest coverage finding

**Only 1 of 10 unseen products routed to a category; 9 went generic.** Faucets, pipe
fittings and label printers have no taxonomy node here, and the guide named Faucets and
Fittings as the two categories specified end to end — in files we were never given.

The generic path still produced valid rows with descriptions, so nothing fails. But on a
genuinely unfamiliar evaluation set, expect **richness to drop toward the generic six
attributes** while structure and honesty hold. That is the right failure mode and it should
be said out loud rather than discovered by a judge.

**State.** **774 tests** (was 768), lint clean.
