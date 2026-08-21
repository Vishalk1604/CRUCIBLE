# Results

All figures below come from a **generated corpus**, not a real catalog. What is real is
the error distribution: a local Qwen3-VL-8B extracted every value and these are its own
mistakes, not injected faults. Read every number as evidence that the method works, not
as a claim about performance on a distributor's data.

Reproduce with `crucible-app` (see the README), or:

```bash
uv run python -c "from crucible.api.session import CertificationSession; print(CertificationSession(n_per_category=200).stats())"
```

## Setup

| | |
|---|---|
| Corpus | 600 generated products across 3 categories (ball valves, hex cap screws, ball bearings) |
| Extractor | Qwen3-VL-8B, local, grammar-constrained JSON, thinking disabled |
| Extraction cost | 1370.8 s for 600 products — 2.3 s/product on an 8 GB RTX 5060 |
| Extraction health | 3053 of 3422 proposed values grounded; **0 empty, 0 unparseable** |
| Resampling | 2 further passes at temperature 0.7, ~18 min each, for the ensemble verifier |
| Scorable values | 2627, split 876 fit / 876 calibrate / 875 test |
| Verifiers | dimensional, constraint, coherence, ensemble |
| Confidence | 95% (δ = 0.05) |

## The guarantee holds

Unverified, the extraction is **30.3% wrong**. Scorer AUROC on held-out data is **0.928**.

| Requested α | Automation | Certified bound | Realised error | Verdict |
|---|---|---|---|---|
| 0.5% | refused | — | — | cannot certify |
| 1.0% | refused | — | — | cannot certify |
| 2.0% | refused | — | — | cannot certify |
| 3.0% | refused | — | — | cannot certify |
| 5.0% | 9.0% | 4.31% | 0.00% | holds |
| 7.0% | 18.7% | 3.19% | 1.22% | holds |
| 10.0% | 65.5% | 8.33% | 6.28% | holds |
| 15.0% | 65.5% | 8.33% | 6.28% | holds |

No promise is broken at any level. Where the evidence cannot support a promise the system
refuses rather than issuing one it cannot keep, which is what makes the rest of the table
worth anything.

## Verifier ablation

| Suite | AUROC | Distinct scores | Automation @ 10% |
|---|---|---|---|
| dimensional + constraint | 0.883 | — | — |
| + coherence | 0.910 | 12 | 16.8% |
| + ensemble | **0.928** | **37** | **65.5%** |

**Coherence** looked like padding at +0.002 AUROC on a 45-product pilot. At 600 products it
is worth +0.027, because most attributes finally clear the sample floor below which it
correctly abstains rather than guessing. It is high-precision and low-recall — applicable
on 99.7% of values but returning full trust on 97.6% of them — so it earns its keep
entirely from the 2.4% it objects to.

**Ensemble** was built to fix resolution rather than accuracy, and it did. Before it, three
verifiers each emitting about three discrete trust levels produced only 13 distinct signal
patterns across 2627 values, one of which covered 43.5% of them. A threshold had roughly
twelve places to sit, so the risk-coverage frontier was a coarse staircase and most of the
dial selected the same threshold. Scoring agreement as mean pairwise similarity rather
than exact match took distinct scores from 12 to 37 — the staircase broke, α of 5%, 7% and
10% now select genuinely different thresholds, and automation at 10% rose from 16.8% to
65.5%.

It was a trade, not a free improvement, and the cost belongs on the record. **α=3%
previously certified 16.8% automation and now refuses.** The new signal reshuffled the
ordering and shrank the very clean subset at the strict end. A large gain across the
usable range against a loss at one setting is worth taking, but it was paid for.

## Why 2% is out of reach, precisely

The strict end is bounded by **calibration sample size, not by the verifiers**.

Sorting the calibration split cleanest-first:

| Accepted | Errors | Clopper-Pearson upper bound |
|---|---|---|
| 50 | 0 | 5.82% |
| 100 | 1 | 4.66% |
| 150 | 1 | 3.12% |
| 200 | 7 | 6.47% |

Certifying 2% with zero observed errors requires at least **149 accepted values**
(`log 0.05 / log 0.98`). The cleanest 149 in this split contain **exactly one error**,
which lifts the bound to about 3.1%.

So 2% is missed by a single value. No verifier fixes this: with 876 calibration values the
binomial bound cannot tighten further at any threshold. Roughly three times the data at
the same clean proportion would put the bound near 1.3%.

That redirects the next step. More calibration data is worth more than a fifth verifier,
which is what makes the Icecat ingestion the highest-value remaining work rather than
entailment.

## Notes against over-reading these numbers

**The corpus is synthetic.** Descriptions are assembled from code tables. Real distributor
data is messier, less consistent, and full of vendor-specific shorthand no table covers.

**Rules are disabled for these runs.** With the rule extractor enabled first, as the
production cascade intends, the error rate on this corpus is 0% — rules win every
contested attribute and are perfect here by construction. That is the circularity
described in `extract/rules.py`, not a result. Calibration reads the model-only path so
the labels are real.

**Realised error of 0.00% at α=5% is a small-sample artifact.** Only 79 test values are
auto-published there; observing zero errors among them is consistent with a true rate
anywhere below roughly 4%, which is what the 4.31% bound reports.

**α=10% and α=15% give identical results.** Both select the same threshold — the frontier
is finer than it was but still not continuous, and there is nothing between those two
settings for it to choose.
