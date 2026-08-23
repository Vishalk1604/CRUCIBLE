# Publishing this for free

## The constraint, stated plainly

Crucible runs a 6.2 GB vision-language model on a local GPU. **No free hosting tier
provides a GPU**, and the free CPU tiers do not have the RAM to hold the model. Vercel,
Netlify, Render, Railway, Fly and GitHub Pages all cost nothing and all cannot run this.
A GPU instance that could is roughly $30–50/month.

So "deploy the live app" and "deploy for free" cannot both be true, and it is better to say
that than to quietly ship something crippled.

**What can be published for free is the evidence**: the real rows a real run produced, the
real verifier verdicts, and the real downloadable files. That is also, for a judge, most of
what matters — they want to see the output and check the claims, not watch a progress bar.

---

## The recommended free stack

| Piece | Where | Cost | What it gives a judge |
|---|---|---|---|
| Source code | GitHub repo | free | Reproducibility |
| **Static demo site** | **GitHub Pages** | **free** | A public URL showing real output |
| Delivery sheet + evidence | committed to the repo | free | The downloadable artifact the brief asks for |
| Demo video | YouTube (unlisted) or Loom free | free | The live app actually running |

Together these satisfy the brief's own requirement — *"your prototype should allow the
generated output to be downloaded as an Excel (.xlsx) or CSV file"* and *"include the
output file link in your solution description so the evaluation team can reproduce and
validate your approach."*

---

## 1. The static site (5 minutes)

Already built. `tools/build_static_site.py` reads a real delivery file and generates a
self-contained page: the pitch, the C4 catch, twelve real products with their empty cells
intact, the class breakdown, and download links.

```bash
uv run python tools/build_static_site.py --run submission --out docs
```

It writes `docs/index.html` plus copies of `delivery.csv` and `evidence.csv`.

**Nothing on that page is a mock-up.** Every row is read out of the actual export, blanks
and all — which is the point, because the blanks are the argument.

### Turning it on

1. Push the repo to GitHub.
2. Repo → **Settings → Pages**.
3. Source: **Deploy from a branch**. Branch: `main`, folder: **`/docs`**. Save.
4. Wait ~60 seconds.

Your URL: `https://<username>.github.io/<repo>/`

That is the link to put in the submission.

⚠️ Make the repository **public**, or GitHub Pages will not serve it on a free account.

---

## 2. The output files

The brief asks for a downloadable XLSX or CSV. Commit the real ones:

```
submission/delivery.csv      # 252 columns, 1000 products
submission/delivery.xlsx     # same, Excel-safe fractions
submission/evidence.csv      # one row per populated cell, with its source and verdicts
```

The XLSX matters: Excel silently turns `1/2` into a date when it opens a CSV, and once it
has, the original text is gone. The XLSX writes every cell as explicit text so `50-1/4`
survives.

⚠️ Check the file size before committing. GitHub warns above 50 MB and rejects above 100 MB.
A 1000-row delivery file is well under both, but the evidence sidecar grows with cell count.

---

## 3. The demo video (worth more than it costs)

Two to three minutes, screen-recorded with Windows Game Bar (`Win+G`) or OBS, both free.
Follow the script in `SUBMISSION.md` §10 — upload the sample catalog, open a product, point
at the empty `Material` cell, download the evidence sidecar, move the risk dial until it
refuses.

This is the only artifact that shows the system *working* rather than its results. Upload
unlisted to YouTube and link it.

---

## If you decide to pay after all

For a genuinely live demo, cheapest first:

| Option | Cost | Notes |
|---|---|---|
| **Hugging Face Spaces**, CPU basic | free | 2 vCPU / 16 GB. Would need a much smaller model — `qwen2.5:1.5b` — and would run several times slower. Honest but degraded. |
| Hugging Face Spaces, T4 small | ~$0.60/hr | Real GPU. Pause it between demos and a weekend costs a few dollars. |
| RunPod / Vast.ai spot | ~$0.20/hr | Cheapest real GPU; needs setup. |
| Render / Railway GPU | $30+/mo | Simplest, most expensive. |

**If you want a live URL for judging day only**, the honest play is a T4 Space started an
hour before and paused after. Under a dollar.

But do not let this block the submission. A static site with real output, real numbers and a
video demonstrates the same thing, costs nothing, and cannot fall over while a judge is
looking at it.

---

## What to put in the submission form

```
Live demo (static, real output):  https://<username>.github.io/<repo>/
Source code:                      https://github.com/<username>/<repo>
Delivery file (CSV):              .../blob/main/submission/delivery.csv
Delivery file (XLSX):             .../blob/main/submission/delivery.xlsx
Evidence sidecar:                 .../blob/main/submission/evidence.csv
Video walkthrough:                <unlisted YouTube link>

Runs entirely offline on a local GPU — no API keys, no per-record cost. The hosted page is
static because the model needs a GPU no free tier provides; every figure and every row on it
is read from the committed output files.
```

That last sentence is worth including. Explaining *why* the demo is static, and backing it
with real artifacts, reads as engineering judgement rather than a missing feature.
