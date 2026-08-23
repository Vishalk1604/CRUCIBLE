# Handoff

Everything needed to pick this up on another machine. Read `CONTEXT.md` first for the
project's thesis and its non-negotiables; this document covers setup, current state, and
what to do next.

## 1. Install

Verified on Windows 11 with an 8 GB laptop GPU. Linux and macOS should work; the CUDA
wheel index below is Windows-specific.

**Prerequisites**

| | |
|---|---|
| Python | 3.11 |
| [uv](https://docs.astral.sh/uv/) | dependency manager — the venv has no `pip` |
| [Ollama](https://ollama.com/download) | local inference (~1.5 GB installer) |
| NVIDIA GPU | 8 GB VRAM minimum, recent driver |
| Disk | ~15 GB (model 6.1 GB, torch ~3 GB, caches) |

**Steps**

```bash
git clone https://github.com/Vishalk1604/CRUCIBLE.git
```

```bash
uv sync --extra models --extra api --extra dev
```

```bash
ollama pull qwen3-vl:8b
```

Confirm CUDA is live — if this prints `False`, torch installed as the CPU build:

```bash
uv run python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

Expect `2.11.0+cu128 True`. If it says `+cpu`, reinstall:

```bash
uv pip install "torch" --index-url https://download.pytorch.org/whl/cu128 --force-reinstall
```

The CUDA 12.8 index matters: RTX 50-series is Blackwell (sm_120) and needs 12.8 or newer.
Windows CUDA wheels lag PyPI, so `uv add torch` alone gets you a CPU build.

**Credentials** — create `.env` in the repo root (gitignored):

```
ICECAT_USERNAME=vishal1605
ICECAT_API_TOKEN=<from MyIcecat -> Access Tokens>
ICECAT_CONTENT_TOKEN=<from MyIcecat -> Access Tokens>
```

Nothing else needs a key. The system runs entirely locally; Icecat is only for real
product data.

## 2. Run it

```bash
uv run crucible-app
```

Open <http://127.0.0.1:8000>.

**Startup is instant if the caches came across in the handoff zip** (`data/generated/harvest/`).
Without them the first launch runs ~20 minutes of inference for the main harvest, and the
ensemble verifier silently abstains until the two resampling passes exist. To generate
those (~18 min each):

```bash
uv run python -c "from crucible.corpus.harvest import harvest_sample; [harvest_sample(i, n_per_category=200) for i in (1,2)]"
```

Tests:

```bash
uv run pytest -q
```

326 should pass. `tests/test_pipeline.py` is slow (it runs the fault-injection pipeline);
`--ignore=tests/test_pipeline.py` for a fast loop.

## 3. Where the project stands

**Built and working.** The full spine: schema → extraction (rules + local LLM) →
normalisation → four verifiers → learned fusion → conformal certification → local web app
with the risk dial and an explainable review queue. 27 commits, 326 tests.

**Headline numbers** (full detail and caveats in `docs/RESULTS.md`):

| | |
|---|---|
| Corpus | 600 generated products, 2627 scorable values |
| Extraction | 2.3 s/product, 0 empty, 0 unparseable across 600 calls |
| Unverified error | 30.3% |
| Scorer AUROC | 0.928 |
| α=7% | 18.7% automation, 3.19% bound, 1.22% realised — holds |
| α=10% | 65.5% automation, 8.33% bound, 6.28% realised — holds |

Every promise holds. Below 3% the system refuses rather than issuing one it cannot keep.

**The single most important caveat:** the corpus is generated. The *error distribution* is
real (the local model's own mistakes, not injected faults), but the products are not. This
is the biggest weakness in the submission and closing it is priority one.

## 4. What to do next, in order

### Priority 1 — Real data from Icecat

Why first: 2% is unreachable by **exactly one error**. Certifying 2% needs ≥149 accepted
values; the cleanest 149 in the calibration split contain one error, giving a 3.1% bound.
No verifier fixes that — with 876 calibration values the binomial bound cannot tighten.
Roughly 3× the data at the same clean proportion lands near 1.3%. **More data beats a
fifth verifier.**

The client is built and verified (`corpus/icecat.py` — fetches by brand + product code,
caches permanently, returns attributes with units already split from magnitudes). What is
missing is a *list* of products to fetch, which needs Icecat's index:

```
https://data.icecat.biz/export/freexml/EN/daily.index.xml.gz
```

That endpoint needs HTTP Basic auth with the account password, **or** IP whitelisting via
MyIcecat → Allowed IP addresses. Whitelisting is cleaner: no credential ends up on disk.
Save the file to `data/raw/`.

*(A previous download attempt did not land in the repo — treat the file as not yet
obtained.)*

Then: parse the index, filter to Industrial & Lab Equipment / Building & Construction /
Lighting, fetch datasheets, and map them onto `CategorySchema`. Note Open Icecat is
brand-sponsored, so vertical coverage is uneven — Lighting may be much deeper than
Industrial. Lighting is still a good target: ETIM's native domain, dense numeric
attributes with real units.

### Priority 2 — Search before/after

The commerce-impact proof, and the part business judges feel immediately. Index the
certified catalog, then show a query like `1/2 inch 316 stainless ball valve 600 WOG`
returning nothing before enrichment and the right SKUs with working facets after.

### Priority 3 — Entailment verifier

A fifth signal, deprioritised deliberately: the strict-end constraint is sample size, not
separation. Also worth knowing before starting — our evidence is currently terse ERP
shorthand, and NLI models are unreliable on that. Entailment earns its place once richer
evidence (spec sheets, product pages) exists, which is the unbuilt RESOLVE stage.

### Not started, currently slideware

Escalation to a stronger model, human queue ranked by revenue impact, PIM/ETIM/JSON-LD
export, MCP endpoint, nightly LoRA refresh.

## 5. Demo notes

The dial is the moment. Drag α and watch automation move — it re-thresholds in
milliseconds because everything expensive is computed once at startup and only the final
threshold depends on α.

The review queue is the second moment. Real caught errors with plain-language reasons:

```
BV-00176  bore = "400"  (expected 0.75")
  dimensional  '400' carries no unit; assuming millimeter
  constraint   violates bore <= nominal_size [bore=400mm, nominal_size=0.75mm]
  coherence    400 is 430.6 robust sigma from the category median of 1
```

Three independent verifiers converging, each explaining itself. Another good one: the
model put a port code (`FP`) in the bore field — a genuine attribute swap, caught.

Say plainly that the corpus is synthetic. The banner on the page already does. Judges
respect a team that knows its own limitations; getting caught overclaiming is far worse
than the caveat.
