# Crucible

**Product data enrichment that tells you which values it could not stand behind.**

> Generation is solved. Knowing which tenth is wrong is not.

Crucible takes the shorthand that actually sits in a distributor's ERP — a part number, a
35-character description, three brand columns that are usually placeholders — and produces a
252-column, commerce-ready delivery sheet. Then it does the part nobody else does: it marks
what it could not establish, and shows the evidence for everything it could.

Built for **UniHack**, run by **Unilog**. Runs entirely offline on one laptop GPU.

---

## The one-paragraph argument

Frontier models already reach ~91% F1 on product attribute extraction, and several vendors
will sell you enrichment with a confidence score attached. That score is the model's opinion
of its own work, and it runs highest exactly where the model is most certain and most wrong.
So distributors still pay people to check **100%** of records, and the automation saves
nothing. Crucible treats the model as an **untrusted proposer**: every value it produces is
examined by six independent external checks, and only what survives is published.

The client's own guide says the same thing from the other side:

> *"Noticing and reporting such gaps is a strength, not a failure; a confidence score or a
> 'needs human review' flag is a genuinely valuable feature."*

---

## What it actually does

```bash
uv run crucible enrich --input "Unihack_ Sample Dataset - Input.csv" --out runs/demo
```

Input — six columns, 35 characters of description:

```
DCB518ASTS06G | DCB518ASTS06G Diablo 1/2"x18" - Sanding Belt 6pc | -- Unbranded -- | ...
```

Output — 61 of 252 columns populated, including all five description formats at their
required lengths and casings:

```
Dept / Class / Fine   Tools & Equipment / Power Tool Accessories / Sanding Belts
SHORT_DESC            Diablo DCB518ASTS06G Sanding Belt, 1/2"x18", 6pc Quantity
INVOICE_DESC          SANDING BELT 1/2"X18" 6PC              (25 chars, ≤40, CAPS)
MOBILE_DESC           Freud Inc Diablo, Sanding Belt, DCB518ASTS06G, 1/2"x18"   (69 chars)
ATTRIBUTE_LABEL 3     Material
ATTRIBUTE_VALUE 3     ← empty, on purpose
```

**That empty cell is the product.** The attribute applies to this class and nothing in the
source supported a value for it. Every other tool fills it.

---

## Measured, not claimed

Full detail and reproduction commands in [`docs/RESULTS.md`](docs/RESULTS.md).

| | |
|---|---|
| Columns populated | **61 / 252** on 120 products |
| Descriptions produced | 120 / 120 |
| `INVOICE_DESC` ≤ 40 chars, upper case | **100%** |
| Unit spacing (`24 in`, never `24in`) | **100%** |
| Controlled-vocabulary compliance | **76%** (from 43%, after fixing our own lists) |
| Throughput | 1.14 s/product · ≈19 min per 1,000 |
| Empty or unparseable model responses | **0** |

On 150 real products with six verifiers, realised error on published values falls from
**12.3% → 3.2%**. The certified *bound* at that setting is loose (12.5%) — a sample-size
limit, stated plainly rather than rounded away.

---

## The catch that makes the argument

From the live review queue. A ball bearing; the model proposed `seal_type = C4`:

```
ensemble     1.00   identical across 3 independent samples
coherence    1.00   'C4' appears in 10% of this category
dimensional  abstained    nominal attribute, nothing to check
constraint   abstained    no constraint mentions seal_type
identity     abstained    not an identity claim
vocabulary   0.00   'C4' is not a term seal_type accepts
```

C4 is a bearing **clearance** code, not a seal type. The model agreed with itself three times
out of three; the statistical profile said it looked normal. Both were confidently wrong.
Only the check that knows what the category actually sells caught it.

*A model cannot correct an error it is certain about. Something outside the model has to.*

---

## Why the descriptions can be trusted

The five description fields are **deterministic templates over already-verified values**, not
model output. There is no path in the code from source text to output prose that does not go
through a verified value, so:

> **A composed description cannot contain a fact that was not verified.**

Not *unlikely to* — cannot. `tests/test_compose.py::test_no_clause_is_unverified` asserts it.
Against the guide's warning that *"a fluent description made of invented values scores zero"*,
that is the strongest available answer.

`MARKETING_DESCRIPTION` is deliberately left empty: it is genuine manufacturer copy and
cannot be derived from six input columns.

---

## Running it

```bash
uv sync --extra models --extra api --extra dev
ollama pull qwen3-vl:8b

uv run crucible enrich   --input "Unihack_ Sample Dataset - Input.csv" --out runs/demo
uv run crucible evaluate --input "Unihack_ Sample Dataset - Input.csv" --limit 120
uv run crucible-app                        # http://127.0.0.1:8000
uv run pytest -q                           # 759 tests
```

The web app is a product, not a dashboard: a landing page, a workspace, drag-and-drop upload,
live per-product progress, and four downloads — delivery sheet (XLSX and CSV), the evidence
sidecar, and the live guarantee.

⚠️ Extraction needs the model resident on the GPU. `preflight.py` checks placement before
every run and refuses if Ollama has fallen back to CPU, because that failure is silent and
costs 3.5× throughput with no error message.

---

## What this does not do

Stated here rather than discovered later:

- **No image or document retrieval.** `Product Image`, `Specification Sheet` and the URL
  columns stay empty. The naming convention is obvious enough to synthesise, and a filename
  is a claim that a file exists. Eleven such columns are pinned closed by a test.
- **No distributor-internal identifiers.** `PART_NUMBER` and `SKU - MY_PART_NUMBER` are not
  in the input under any name.
- **Vocabulary compliance is against our own lists**, not the client's 161,000-row LOV, which
  was not published with the sample pack. Never described as "LOV compliance".
- **Calibration labels come from injected faults** over the system's own clean extraction,
  because the 200-row answer key was not published either. Every artifact from that path is
  labelled SIMULATED, and the label noise runs in the conservative direction.

---

## Documents

| | |
|---|---|
| [`docs/RESULTS.md`](docs/RESULTS.md) | Every measured number, with its command and caveat |
| [`guide.md`](guide.md) | The client's Solution Guide, transcribed |
| [`WINNING-PLAN.md`](WINNING-PLAN.md) | What was built, in priority order, and why |
| [`DIARY.md`](DIARY.md) | Append-only log — decisions, measurements, and wrong turns |
| [`CLAUDE.md`](CLAUDE.md) | The non-negotiables |
