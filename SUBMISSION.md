# Crucible — UniHack submission

**Product data enrichment that tells you which values it could not stand behind.**

Six input columns to a 252-column delivery sheet, with a source quote behind every populated
cell and a stated reason behind every empty one. Runs offline on one laptop GPU.

---

## 1. Why this, and not better extraction

Frontier models already reach roughly 91% F1 on product attribute extraction. Several
vendors sell enrichment with a confidence score attached. That score is the model's opinion
of its own work, and it is highest exactly where the model is most certain and most wrong —
so distributors still pay people to check **100%** of records and the automation saves
nothing.

A tool that is right nine times in ten, with no way to tell which nine, has not removed the
review. It has moved it.

**Crucible treats the model as an untrusted proposer.** Every value it produces is examined
by six independent external checks — unit algebra, cross-attribute constraints, controlled
vocabularies, identity cross-referencing, catalog coherence, ensemble disagreement — and
only what survives is published. The rest becomes a queue with reasons attached.

The Solution Guide asks for exactly this:

> *"Real data is imperfect — say so… Noticing and reporting such gaps is a strength, not a
> failure; a confidence score or a 'needs human review' flag is a genuinely valuable
> feature."*

---

## 2. The demonstration

From the live review queue. A ball bearing; the model proposed `seal_type = C4`:

| check | verdict | reason |
|---|---|---|
| ensemble | **1.00** | identical across 3 independent samples |
| coherence | **1.00** | "C4" appears in 10% of this category |
| dimensional | *abstained* | nominal attribute — nothing to check |
| constraint | *abstained* | no constraint mentions `seal_type` |
| identity | *abstained* | not an identity claim |
| **vocabulary** | **0.00** | **"C4" is not a term `seal_type` accepts** |

C4 is a bearing **clearance** code, not a seal type. The model agreed with itself three times
out of three. The statistical profile said it looked entirely normal. **Both were confidently
wrong.** Only the check that knows what the category actually sells caught it.

*A model cannot correct an error it is certain about. Something outside the model has to.*

Note also that three checks **abstained**, and that this is reported rather than folded in.
"Nothing to check" and "checked and satisfied" are different facts; merging them is how a
catalog ends up auto-publishing precisely the values nothing examined.

### One we found but deliberately do not claim to catch

The guide mentions *"at least one row where the manufacturer and brand look mismatched"* as
a gap worth noticing. We found it: row 1 of the supplied delivery format pairs
`MANUFACTURER_NAME = Rheem Manufacturing` with `BRAND_NAME = FRIGIDAIRE®`. Frigidaire is
Electrolux; Rheem makes HVAC.

**Our verifiers do not flag it, and we will not pretend otherwise.** Checking a brand against
its manufacturer needs an authority on which pairs are legitimate — Diablo really is Freud's
brand, and Milwaukee Accessory really does sell Milwaukee. Without that authority the check
either fires on every honest pairing or on none. The authority is
`UniCat_Manufacturer_and_Brand_List.xlsx`, 27,000 approved manufacturer/brand rows, named in
the guide but not published with the sample pack.

So this is a **precisely specified gap** rather than a capability: one file away, and we can
say exactly which file and exactly what it would enable. Reporting it that way is the same
discipline the rest of the submission runs on.

---

## 3. Against the four Expected Outcomes

| Outcome | What we deliver |
|---|---|
| **Generate structured product intelligence from limited inputs** | 6 columns → **61 of 252** populated on a 120-product run, including all five description formats at their required lengths and casings |
| **Improve product data quality and consistency** | Trade abbreviations expanded, units to a single form with the house spacing rule, values constrained to per-category vocabularies |
| **Validate and enrich with traceable outputs** | Six verifiers per value; an evidence sidecar giving every populated cell its source quote, provenance and six verdicts |
| **Scale efficiently across large catalogs** | 1.14 s/product ≈ 19 min per 1,000, fully offline. Concurrency implemented and deterministic; see §7 for the honest ceiling |

---

## 4. The three metrics the guide names

> *"Field-level accuracy against the 200 known-good rows, character-limit compliance, and
> percentage of values found in the LOV are all simple, credible metrics. Judges will look
> for them."*

```bash
uv run crucible evaluate --input "Unihack_ Sample Dataset - Input.csv" --limit 120
```

**Character-limit compliance** — needs no answer key, runs at any scale:

| | |
|---|---|
| `INVOICE_DESC` ≤ 40 chars | **100%** |
| `INVOICE_DESC` upper case | **100%** |
| units spaced (`24 in`, never `24in`) | **100%** |
| `MOBILE_DESC` 60–80 chars | 77% |

The 77% is the composer **refusing to pad**. Sparse inputs cannot always reach a
60-character floor, and inventing words to hit one is what the guide says scores zero.

**Controlled-vocabulary compliance — 79%**, improved from 43% by fixing our own lists (§7).

⚠️ Measured against the per-category vocabularies *this project authors*. The client's
161,000-row LOV was not published with the sample pack, so this is deliberately **not**
called "LOV compliance". Asserting conformance to a standard we have never seen is the one
move the guide says scores zero.

**Field-level accuracy — 40% exact, over 2 labelled rows.** The pack contained 2 fully
enriched rows, not 200. Two rows is a worked example, not a sample: our report prints the
sample size beside every percentage and refuses to treat it as a rate below ten rows.

---

## 5. Why the descriptions can be trusted

The five description fields are **deterministic templates over already-verified values**,
not model output. There is no path in the code from source text to output prose that does
not pass through a verified value. Therefore:

> **A composed description cannot contain a fact that was not verified.**

Not *unlikely to* — cannot. `tests/test_compose.py::test_no_clause_is_unverified` asserts it
over the full output, with a companion test proving the assertion has teeth.

Against the guide's *"a fluent description made of invented values scores zero"*, that is
the strongest available answer.

`MARKETING_DESCRIPTION` is left empty on purpose: it is genuine manufacturer copy and cannot
be derived from six input columns.

---

## 6. In the approaches the brief suggests

| Suggested | Ours |
|---|---|
| **Knowledge graphs** | A three-level taxonomy (Dept/Class/Fine → Classpath) over per-category attribute schemas with typed values, controlled vocabularies and cross-attribute constraints. This is the knowledge graph. |
| **RAG** | Extraction is constrained by retrieved category schemas and vocabularies rather than free generation — retrieval over master data. |
| **Human-in-the-loop** | The review queue *is* the loop: only values the checks could not support, each with the reason. |
| **Vision-language models** | `qwen3-vl:8b`, local. |
| **Document intelligence** | Not implemented. |
| **AI agents** | Not implemented — and not claimed. |

**Deployment note.** Everything runs locally against a model on the operator's own machine.
For unreleased pricing and specifications that is not a convenience, it is the requirement —
and it means no per-record API cost at catalog scale.

---

## 7. What we measured that did not work

Negative results, kept because they are load-bearing.

**Icecat is useless for this catalog — 0/999.** Scanned all 28,547 entries of the daily
index against 999 part numbers. Zero real matches; the two apparent hits are numeric
collisions. Open Icecat is consumer-electronics-weighted and does not cover US
building-materials distribution.

**Verifier *coverage*, not quality, was the binding constraint.** The first real-catalog run
scored AUROC 0.532 — a coin flip. Measured applicability: dimensional 37.8%, constraint
37.7%, identity 5.7%. **62% of values received no verifier opinion at all.** A real catalog
is dominated by nominal attributes and both physical verifiers correctly abstain on all of
them. Adding the vocabulary verifier took coverage 38% → 88% and AUROC 0.532 → 0.599 —
necessary, and not sufficient.

**A vocabulary the data never uses is a filter that rejects everything.** `wheel_type` held
ISO type codes and matched **0 of 38** real values, because this catalog writes "Cut Off"
and "Grinding". Rewriting it from measurement moved compliance 43% → 79%. The verifier had
been working perfectly against a wrong list — invisible without a compliance metric.

**Concurrency ceiling is 1.09×, and the cause is VRAM.** Identical with and without
`OLLAMA_NUM_PARALLEL=2`. A 6.2 GB model on an 8.15 GB card cannot fit a second KV cache, so
no inference runs in parallel; the 9% is our own CPU work overlapping. The implementation is
correct and deterministic and would parallelise on a larger card. **The architecture is
concurrency-ready; this laptop is the constraint** — we do not quote a projection we never
measured.

---

## 8. What this does not do

- **No image or document retrieval.** The reference rows fill `Product Image` with
  `FRIGIDAIRE_PDSH4816AF.jpg` and the convention is obvious enough to synthesise for every
  product in a minute. We hold no images. **A filename is a claim that a file exists**, and
  `Actual Image (Yes/No) = Yes` would simply be false. Eleven such columns are pinned closed
  by a test so a later "coverage improvement" fails and reads the reason.
- **No distributor-internal identifiers** — `PART_NUMBER`, `SKU - MY_PART_NUMBER` are not in
  the input under any name.
- **Calibration labels come from injected faults** over the system's own clean extraction,
  because the 200-row answer key was not published. Noise runs one way — it deflates AUROC
  and inflates realised error — and every artifact from that path is labelled SIMULATED.

---

## 9. Running it

```bash
uv sync --extra models --extra api --extra dev
ollama pull qwen3-vl:8b

uv run crucible enrich   --input "Unihack_ Sample Dataset - Input.csv" --out runs/demo
uv run crucible evaluate --input "Unihack_ Sample Dataset - Input.csv" --limit 120
uv run crucible-app                        # http://127.0.0.1:8000
uv run pytest -q                           # 759 tests
```

---

## 10. Five-minute demo script

1. **Landing page** (`/`) — the problem in three claims, then the C4 catch rendered as a
   verdict card. *"The model agreed with itself three times. Only the external check caught
   it."*
2. **Upload** (`/app`) — drop the sample catalog, 50 products, ~1 minute. Rows land live.
3. **Open one product** — point at `Material`: *"not established"*. **Say the line:** the
   attribute applies to this class and nothing in the source supported a value. Every other
   tool fills that cell.
4. **Show a filled one** — the source quote beside it. Every populated cell has one.
5. **Download the evidence sidecar** — open it. One row per cell: provenance, quote, six
   verdicts, abstentions marked as abstentions.
6. **The dial** (`/certify`) — read the amber banner first: this page runs the **generated**
   corpus, and it says so. Drag to 5%: the guarantee holds — **62.2% auto-published, bound
   0.6%, realised 0.18%**, against a 30.3% unverified baseline. Drag to the far left (0.5%):
   **it refuses**, and says why. *"Most demos have no refusal state. This one is designed."*

   The real-catalog figures (12.3% → 3.2% realised) come from the CLI path, not this page.
   Say which is which — the banner already does.
7. **Close on the evaluation** — `crucible evaluate`: 100% character-limit compliance,
   76-86% vocabulary compliance, and a field-accuracy figure that prints its own sample size.

**Total: five minutes.** The whole thing runs offline.

### Before you present — rehearsed, and these two will bite

**Warm the model.** Ollama unloads after a few minutes idle, and a cold first upload pays
~10-15 s of load before the first row appears. Run any small enrichment beforehand so the
model is resident. `ollama ps` should read `13%/87% CPU/GPU`; if it reads `98%/2%`, something
else is holding VRAM and everything will run 3.5x slower.

**Do not run step 7 live.** `crucible evaluate` re-runs the pipeline twice — once against the
labelled rows, once for compliance — and takes **~47 s at `--limit 30`**. Either run it
before you start and show the output, or launch it at step 1 and let it finish in the
background while you talk.

Timings measured in rehearsal: landing ~1.2 s to interactive; 8 products enriched in 18 s
(2.07 s each, model cold); downloads instant.

⚠️ The `/certify` page runs the **generated** corpus and its banner says so. Read the banner
aloud. The real-catalog figures live in `docs/RESULTS.md`, not on that page.

---

| Document | |
|---|---|
| [`docs/RESULTS.md`](docs/RESULTS.md) | Every measured number, with its command and caveat |
| [`guide.md`](guide.md) | The client's Solution Guide, transcribed |
| [`DIARY.md`](DIARY.md) | Append-only log — every decision, measurement and wrong turn |
| [`CLAUDE.md`](CLAUDE.md) | The non-negotiables |
