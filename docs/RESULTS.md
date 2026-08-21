# Results

All figures below come from a **generated corpus**, not a real catalog. What is real is
the error distribution: a local Qwen3-VL-8B extracted every value and these are its own
mistakes, not injected faults. Read every number as evidence that the method works, not
as a claim about performance on a distributor's data.

Reproduce with `crucible-app` (see the README) or:

```bash
uv run python -c "from crucible.api.session import CertificationSession; s=CertificationSession(n_per_category=200); print(s.stats())"
```

## Setup

| | |
|---|---|
| Corpus | 600 generated products across 3 categories (ball valves, hex cap screws, ball bearings) |
| Extractor | Qwen3-VL-8B, local, grammar-constrained JSON, thinking disabled |
| Extraction cost | 1370.8 s for 600 products — 2.3 s/product on an 8 GB RTX 5060 |
| Extraction health | 3053 of 3422 proposed values grounded; **0 empty, 0 unparseable** |
| Scorable values | 2627, split 876 fit / 876 calibrate / 875 test |
| Verifiers | dimensional, constraint, coherence |
| Confidence | 95% (δ = 0.05) |

## The guarantee holds

Unverified, the extraction is **30.3% wrong**. Scorer AUROC on held-out data is **0.910**.

| Requested α | Automation | Certified bound | Realised error | Verdict |
|---|---|---|---|---|
| 0.5% | refused | — | — | cannot certify |
| 1.0% | refused | — | — | cannot certify |
| 2.0% | refused | — | — | cannot certify |
| 3.0% | 16.8% | 2.26% | 0.00% | holds |
| 5.0% | 16.8% | 2.26% | 0.00% | holds |
| 7.0% | 16.8% | 2.26% | 0.00% | holds |
| 10.0% | 16.8% | 2.26% | 0.00% | holds |
| 15.0% | 67.3% | 9.36% | 7.81% | holds |

No promise is broken at any level. Where the evidence cannot support a promise the system
refuses rather than issuing one it cannot keep, which is the behaviour that makes the
rest of the table worth anything.

## The limitation, and exactly what causes it

The automation rate is flat from 3% to 10% and then jumps. That is not the bound being
conservative — it is the resolution of the evidence.

Measured on the calibration split:

- **12 distinct nonconformity scores** across 876 values
- **13 distinct verifier signal patterns**

Three verifiers, each emitting a handful of discrete trust levels, can only distinguish
thirteen kinds of value. A threshold can therefore only be placed in about twelve places,
so the risk-coverage frontier is a coarse staircase and most of the dial's range selects
the same threshold. One score alone accounts for 43.5% of all values.

This is the concrete, measured case for the two verifiers not yet built. Entailment
(NLI probability) and ensemble disagreement (fraction of samples agreeing) both emit
*continuous* scores, which would shatter these ties and give the threshold somewhere
finer to land. The argument for them is not that more signals sound better; it is that
the frontier currently has twelve rungs and needs hundreds.

It also explains why nothing certifies below 3%. Reaching a 2% guarantee requires a
threshold that isolates a very clean subset, and no such threshold exists among twelve
candidates.

## Verifier ablation

| Suite | AUROC | Distinct scores | Automation @ 10% |
|---|---|---|---|
| dimensional + constraint | 0.883 | — | — |
| + coherence | 0.910 | 12 | 16.8% |
| + ensemble | **0.928** | **37** | **65.5%** |

**Coherence** looked like padding at +0.002 AUROC on a 45-product pilot. At 600 products it
is worth +0.027, because most attributes finally clear the sample floor below which it
correctly abstains rather than guessing. It is high-precision and low-recall: applicable
on 99.7% of values but returning full trust on 97.6% of them, so it earns its keep
entirely from the 2.4% it objects to.

**Ensemble** was built to fix resolution rather than accuracy, and it did: distinct
nonconformity scores went from 12 to 37 and signal patterns from 13 to 38. The staircase
broke, and α of 5%, 7% and 10% now select genuinely different thresholds instead of
collapsing onto one. Automation at 10% rose from 16.8% to 65.5%.

It also cost something, which is worth stating plainly. α=3% previously certified 16.8%
automation and now refuses. Adding the signal reshuffled the ordering and shrank the very
clean subset at the strict end. The trade was clearly worth it — a large gain across the
usable range against a loss at one setting — but it was a trade, not a free improvement.

## Why 2% is out of reach, precisely

The strict end is now bounded by **calibration sample size, not by the verifiers**.

Sorting the calibration split cleanest-first:

| Accepted | Errors | Clopper-Pearson upper bound |
|---|---|---|
| 50 | 0 | 5.82% |
| 100 | 1 | 4.66% |
| 150 | 1 | 3.12% |
| 200 | 7 | 6.47% |

Certifying 2% with zero observed errors requires at least **149 accepted values**
(`log 0.05 / log 0.98`). The cleanest 149 in this split contain **exactly one error**, which
lifts the bound to about 3.1%.

So 2% is missed by a single value. Nothing about the verifiers fixes this — with 876
calibration values the binomial bound cannot tighten further at any threshold. Roughly
three times the calibration data at the same clean proportion would put the bound near
1.3%.

That redirects the next step. More calibration data is worth more than a fifth verifier,
which is what makes the Icecat ingestion the highest-value remaining work rather than
entailment.

## Notes against over-reading these numbers

**The corpus is synthetic.** Descriptions are generated from code tables. Real distributor
data is messier, more inconsistent, and contains vendor-specific shorthand no table
covers.

**Rules are disabled for these runs.** With the rule extractor enabled first, as the
production cascade intends, the error rate on this corpus is 0% — rules win every
contested attribute and are perfect here by construction. That is the circularity
described in `extract/rules.py`, not a result. Calibration reads the model-only path so
the labels are real.

**Realised error of 0.00% is a small-sample artifact.** At α=3% only 147 test values are
auto-published; observing zero errors among them is consistent with a true rate anywhere
below roughly 2%, which is what the certified bound of 2.26% is reporting.
