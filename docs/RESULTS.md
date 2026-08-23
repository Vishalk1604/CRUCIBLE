# Results

Every number here was measured on this machine and is reproducible with the command shown
beside it. Where a figure rests on a small sample or a substitute for data we were not
given, the caveat sits next to the number rather than in a footnote.

Hardware: RTX 5070 Laptop, 8.15 GB VRAM. Model: `qwen3-vl:8b` (6.2 GB), local Ollama, fully
offline.

---

## 1. The input

| | |
|---|---|
| Products | 1,000 |
| Input columns | 6 — `Mfg_Part_Num`, `Part_Desc`, `E1_Brand`, `Unilog_Brand`, `DIB_Brand`, `Part_Manuf` |
| Description length | 13–70 chars, median **35** |
| `Unilog_Brand` usable | **0 / 1000** — placeholder on every row |
| `E1_Brand` usable | 197 / 1000 |
| `DIB_Brand` usable | 245 / 1000 |
| Descriptions containing their own part number | **699 / 1000** |

That last row is not trivia. It is a second, independently populated channel carrying the
same fact, and it is what the identity verifier checks against.

(An earlier note in `DIARY.md` records this as 676; that count used a stricter
normalisation. 699 is the figure under the comparison the verifier actually performs.)

---

## 2. Output coverage

```bash
uv run crucible enrich --input "Unihack_ Sample Dataset - Input.csv" --out runs/demo --limit 120
```

| Stage | Columns carrying a value |
|---|---|
| Identity, taxonomy, attribute grid only | 25 / 252 |
| \+ description composers | 41 / 252 |
| \+ commerce columns | **61 / 252** |

On 120 products: all five descriptions populate **120/120**. `ITEM_FEATURES_1` 70/120,
`Application` 44/120, `Selling Qty` 37/120.

**The remaining 191 columns are not a backlog.** They divide into three groups:

- **Cannot be derived from six input columns** — `UPC`, `EAN`, `GTIN`, `Country Of Origin`,
  `Warranty`, `Standard/Approvals`. These come from manufacturer documentation.
- **Distributor-internal** — `PART_NUMBER`, `SKU - MY_PART_NUMBER`. Not present in the input
  under any name.
- **Assets we hold none of** — `Product Image`, `Alternate Image 1..4`,
  `Specification Sheet`, `MFR URL`, `Ref URL 1..5`.

That last group was the tempting one. The reference rows fill `Product Image` with
`FRIGIDAIRE_PDSH4816AF.jpg`; the convention is plainly `{BRAND}_{MPN}.jpg` and seven columns
could be synthesised for every product in about a minute. **A filename is a claim that a
file exists.** We hold no images, so emitting one would be a confidently-formatted assertion
about something nobody looked for. `tests/test_commerce.py::TestRefusals` pins eleven such
columns closed so a later "coverage improvement" fails a test and reads the reason.

### On unseen data

A deliberately hostile catalog — different column names entirely, empty and whitespace-only
descriptions, a 400-character description, embedded quotes, Unicode, and categories absent
from the sample:

| | before taxonomy expansion | after |
|---|---|---|
| products routed to a category | 1 / 10 | **5 / 10** |
| columns populated | 37 | **40** |
| crashes | 0 | 0 |

The five that remain generic are correct: two empty descriptions, a 400-character string, a
bare "Widget", and a Japanese description. Nothing was invented for any of them.

⚠️ Expect richness to fall toward the generic six attributes on a genuinely unfamiliar
catalog while structure and honesty hold. That is the designed failure mode.

---

## 3. The three metrics the client's guide names

> *"Field-level accuracy against the 200 known-good rows, character-limit compliance, and
> percentage of values found in the LOV are all simple, credible metrics. Judges will look
> for them."*

```bash
uv run crucible evaluate --input "Unihack_ Sample Dataset - Input.csv" --limit 120
```

### Character-limit compliance — the strongest of the three

Needs no answer key, runs at any scale, means exactly what it says. Over 120 products:

| Check | Result |
|---|---|
| `INVOICE_DESC` ≤ 40 chars | **120/120 = 100%** |
| `INVOICE_DESC` upper case | **120/120 = 100%** |
| `SHORT_DESC` spaces its units | **120/120 = 100%** |
| `LONG_DESC1` spaces its units | **120/120 = 100%** |
| `RETAIL_DESC` spaces its units | **120/120 = 100%** |
| `MOBILE_DESC` 60–80 chars | 92/120 = **77%** |

The 77% is a real limitation and not a defect. Sparse inputs cannot always reach a
60-character floor, and the composer **refuses to pad**. Inventing words to hit a character
count is precisely what the guide says scores zero.

### Controlled-vocabulary compliance

**163 / 207 nominal values = 79%**, improved from 43% (see §6).

⚠️ Measured against the per-category vocabularies **this project authors**, not the client's
161,000-row LOV, which was not published with the sample pack. This is deliberately *not*
called "LOV compliance": asserting conformance to a standard we have never seen is the one
move the guide says scores zero. A test asserts that phrase never appears in the report.

### Field-level accuracy

**40% exact over 2 labelled rows.** The pack we received contains 2 fully enriched rows,
not 200.

| Result | Columns |
|---|---|
| 2/2 | `Mfg_Part_Num`, `MANUFACTURER_PART_NUMBER`, `Part_Desc`, `Dept`, `Class`, `Product Name` |
| 0/2 | `BRAND_NAME`, `MANUFACTURER_NAME` — needs the 27k manufacturer list |
| 0/2 | `Fine`, `Classpath` — our taxonomy labels differ from theirs |
| 0/2 | all five descriptions — structure matches, attribute *selection* differs |

⚠️ **Two rows is a worked example, not a sample.** It can show a field is built the right
*way*; it cannot show how often that is true. `Accuracy.is_indicative` is False below ten
rows and the report prints `over 2 labelled row(s)` beside every percentage, with a caveat
line. A metric that hides its own sample size is worse than no metric.

---

## 4. Verification

Six verifiers. Each may pass, fail, doubt, or **abstain** — and abstention is a distinct
feature in the scorer, never folded into mild approval.

| Verifier | Checks |
|---|---|
| dimensional | unit algebra via pint |
| constraint | cross-attribute physical relationships |
| vocabulary | membership of the category's controlled vocabulary |
| identity | part number against the description's redundant copy |
| coherence | value against the catalog's own distribution |
| ensemble | agreement across resampled extractions |

### The catch that makes the argument

Live from the review queue, `BRG-00027`, proposed `seal_type = C4`:

```
ensemble     1.00   identical across 3 samples
coherence    1.00   'C4' appears in 10% of this category
dimensional  abstained
constraint   abstained
identity     abstained
vocabulary   0.00   'C4' is not a term seal_type accepts
```

C4 is a bearing *clearance* code, not a seal type. **The model agreed with itself three
times out of three and the statistical profile said it looked normal. Both were confidently
wrong.** Only the check that knows what the category actually sells caught it — which is
the whole argument for external tools over model self-critique, demonstrated rather than
asserted.

---

## 5. Certification on the real catalog

```bash
uv run python -c "from crucible.api.source import CatalogSource; ..."
```

150 real products, six verifiers, labels from fault injection over a pseudo-reference:

| | |
|---|---|
| Scorable values | 708 |
| Scorer AUROC | **0.662** |
| Unverified error | 12.3% |
| α = 15% | feasible — 26.7% automated, realised error **3.17%** |
| α = 25% | feasible — 76.3% automated, realised error 9.44% |
| α ≤ 10% | **refuses to certify** |

Realised error on published values falls from **12.3% → 3.2%**, a 4× improvement.

⚠️ Three caveats, all load-bearing:

1. **The certified *bound* is loose** at this sample size — 12.5% at α=15%, barely under the
   12.3% baseline. That is a binomial-sample-size limit, not a verifier limit. More labelled
   data tightens it; a fifth verifier does not.
2. **Labels come from injected faults over the system's own clean extraction**, because the
   200-row answer key was not published. The noise is one-directional: it *deflates* AUROC
   and *inflates* realised error. It cannot manufacture a guarantee that does not hold.
3. Every artifact from this path is labelled **SIMULATED**.

---

## 6. Ablations and negative results

### Icecat is useless for this catalog — 0/999

Scanned all 28,547 entries of `daily.index.xml.gz` against 999 distinct part numbers.
**Zero real matches.** The two apparent hits (`52655`, `25762`) are a Hunter ceiling fan and
a mason line colliding with numeric ids in what is largely a printer-supplies index. Open
Icecat is brand-sponsored and consumer-electronics-weighted; it does not cover US
building-materials distribution. `HANDOFF.md` had this as priority 1.

### Verifier coverage, not verifier quality, was the binding constraint

First real-catalog run: **AUROC 0.532** — a coin flip. Applicability measured over 772
values:

| verifier | applied to |
|---|---|
| dimensional | 37.8% |
| constraint | 37.7% |
| identity | 5.7% |

**~62% of values received no verifier opinion at all**, so their feature vector was all
zeros and the scorer had nothing to separate them with. The synthetic corpus was
quantity-heavy *by construction*; a real building-products catalog is dominated by nominal
attributes, and both physical verifiers correctly abstain on every one.

Adding the vocabulary verifier: coverage **38% → 88%**, AUROC **0.532 → 0.599**.
Necessary, and not sufficient — on this domain the signals are genuinely weaker, not merely
absent.

### A vocabulary the data never uses is a filter that rejects everything

`wheel_type` held ISO type codes (`type 1 flat`, `type 27 depressed center`) and matched
**0 of 38** real values, because this catalog writes "Cut Off", "Grinding", "Dual Metal".
Rewriting it from measurement, plus trade-abbreviation synonyms (`Alm`, `Rnd`, `SS`, `Wh`)
and whole-word containment matching (`Metal Cut-Off` → `cut-off`), moved compliance
**43% → 79%**. The verifier had been working perfectly against a wrong
list — invisible without a compliance metric.

### Concurrency: 1.09× ceiling, and the cause is VRAM

| workers | default server | `OLLAMA_NUM_PARALLEL=2` |
|---|---|---|
| 1 | 49.9s | 50.4s |
| 2 | 45.6s (1.09×) | 46.2s (1.09×) |
| 4 | 45.7s (1.09×) | 46.2s (1.09×) |
| 8 | 45.6s (1.09×) | — |

Identical with and without the server setting. The plateau at two workers is the signature
of a single inference slot: a 6.2 GB model on an 8.15 GB card leaves ~1.9 GB, and each extra
slot needs its own KV cache. Ollama accepts the setting and quietly declines to grant a slot
it cannot fit.

The 9% present is our own CPU work overlapping inference — **no inference runs in parallel
at all.** The implementation is correct and deterministic (output byte-identical at 1 and 8
workers) and would give real parallelism on a larger card. **The architecture is
concurrency-ready; this laptop's VRAM is the constraint.** We do not quote a projected
figure we have not measured.

---

## 7. Throughput

| | |
|---|---|
| Warm extraction | **1.14–1.39 s/product** |
| GPU placement | 13% / 87% CPU/GPU |
| 1,000 products | **≈ 19 minutes** |
| Empty or unparseable responses | 0 across every run |

⚠️ Placement is checked before every run (`preflight.py`). If something else is holding
VRAM the model silently falls back to CPU and runs 3.5× slower with no error — measured the
hard way, when a game left 4.6 GB resident and `ollama ps` read `98%/2% CPU/GPU`.

---

## Reproducing

```bash
uv sync --extra models --extra api --extra dev
ollama pull qwen3-vl:8b
uv run pytest -q                     # 759 tests
uv run crucible enrich --input "Unihack_ Sample Dataset - Input.csv" --out runs/demo --limit 120
uv run crucible evaluate --input "Unihack_ Sample Dataset - Input.csv" --limit 120
uv run crucible-app                  # http://127.0.0.1:8000
```
