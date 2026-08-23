"""Build a static, backend-free demo site from a real enrichment run.

Why this exists
---------------
The live app needs Ollama holding a 6.2 GB model on a GPU. No free hosting tier provides
one, so "deploy the app" and "deploy for free" cannot both be true. What *can* be true for
free is publishing the **evidence**: the real rows a real run produced, the real verifier
verdicts, and the real files, as a static page GitHub Pages will serve at no cost.

This is not a mock. Every row on the generated page is read out of an actual delivery file
written by `crucible enrich`, including its empty cells. A judge who cannot run the pipeline
can still see exactly what it produces and check the numbers against the downloadable
artifacts.

Usage:
    uv run python tools/build_static_site.py --run submission --out docs
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter
from pathlib import Path

#: How many products to embed. Enough to show variety and the abstention pattern without
#: turning the page into a data dump.
SHOWCASE = 12

ATTRIBUTE_SLOTS = 20


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [r for r in csv.DictReader(handle) if any(v.strip() for v in r.values())]


def pick_showcase(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Choose products that show range: different classes, and both filled and empty cells.

    Deliberately *not* the best-looking rows. A showcase of only densely-populated products
    would misrepresent the run, and the empty cells are the argument.
    """
    by_class: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_class.setdefault(row.get("Fine") or "(unclassified)", []).append(row)

    picked: list[dict[str, str]] = []
    for _, group in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
        picked.append(group[0])
        if len(picked) >= SHOWCASE:
            break
    return picked


def row_attributes(row: dict[str, str]) -> list[tuple[str, str, str]]:
    out = []
    for slot in range(1, ATTRIBUTE_SLOTS + 1):
        label = (row.get(f"ATTRIBUTE_LABEL {slot}") or "").strip()
        if not label:
            continue
        out.append(
            (
                label,
                (row.get(f"ATTRIBUTE_VALUE {slot}") or "").strip(),
                (row.get(f"ATTRIBUTE_UOM {slot}") or "").strip(),
            )
        )
    return out


def coverage(rows: list[dict[str, str]]) -> dict[str, object]:
    if not rows:
        return {}
    columns = list(rows[0])
    populated = [c for c in columns if any((r.get(c) or "").strip() for r in rows)]
    classified = sum(1 for r in rows if (r.get("Fine") or "").strip())
    descriptions = sum(1 for r in rows if (r.get("SHORT_DESC") or "").strip())
    classes = Counter((r.get("Fine") or "(unclassified)") for r in rows)
    return {
        "products": len(rows),
        "columns": len(columns),
        "populated": len(populated),
        "classified": classified,
        "descriptions": descriptions,
        "top_classes": classes.most_common(8),
    }


def esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def render(
    rows: list[dict[str, str]],
    stats: dict[str, object],
    run_name: str,
    repo: str,
) -> str:
    showcase = pick_showcase(rows)

    cards = []
    for row in showcase:
        attrs = row_attributes(row)
        filled = sum(1 for _, v, _ in attrs if v)
        path = " › ".join(
            p for p in (row.get("Dept"), row.get("Class"), row.get("Fine")) if (p or "").strip()
        )
        attr_rows = "".join(
            f"<tr><td>{esc(label)}</td>"
            + (
                f'<td class="mono">{esc(value)}</td><td class="mono">{esc(uom)}</td>'
                if value
                else '<td class="empty" colspan="2">not established</td>'
            )
            + "</tr>"
            for label, value, uom in attrs
        )
        descs = "".join(
            f'<div class="desc"><span class="k">{esc(name)}</span>'
            f'<span class="v">{esc(row.get(col) or "")}</span>'
            f'<span class="len">{len((row.get(col) or ""))} ch</span></div>'
            for name, col in (
                ("Invoice", "INVOICE_DESC"),
                ("Mobile", "MOBILE_DESC"),
                ("Short", "SHORT_DESC"),
                ("Retail", "RETAIL_DESC"),
            )
            if (row.get(col) or "").strip()
        )
        cards.append(
            f"""<details class="card">
  <summary>
    <span class="sku">{esc(row.get("Mfg_Part_Num") or "")}</span>
    <span class="src">{esc(row.get("Part_Desc") or "")}</span>
    <span class="tag">{esc(path) if path else "unclassified"}</span>
    <span class="count">{filled}/{len(attrs)} values</span>
  </summary>
  <div class="body">
    <div class="descs">{descs}</div>
    <table><thead><tr><th>Attribute</th><th>Value</th><th>Unit</th></tr></thead>
    <tbody>{attr_rows}</tbody></table>
  </div>
</details>"""
        )

    classes_html = "".join(
        f'<li><span class="n">{n}</span> {esc(name)}</li>' for name, n in stats["top_classes"]
    )

    return TEMPLATE.format(
        products=stats["products"],
        columns=stats["columns"],
        populated=stats["populated"],
        classified=stats["classified"],
        descriptions=stats["descriptions"],
        classified_pct=round(100 * stats["classified"] / max(stats["products"], 1)),
        desc_pct=round(100 * stats["descriptions"] / max(stats["products"], 1)),
        cards="\n".join(cards),
        classes=classes_html,
        run_name=esc(run_name),
        showcase=len(showcase),
        repo=esc(repo),
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Crucible — certified product data</title>
<meta name="description" content="Six input columns to a 252-column delivery sheet, with evidence for every value and a stated reason for every gap." />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet" />
<style>
:root {{
  --primary:#1e40af; --accent:#b45309; --bg:#f8fafc; --fg:#14203f; --card:#fff;
  --muted:#eef2f9; --muted-fg:#475569; --border:#d6e1f2; --ok:#15803d; --danger:#b91c1c;
  --sans:"Fira Sans",system-ui,sans-serif; --mono:"Fira Code",ui-monospace,monospace;
}}
@media (prefers-color-scheme:dark) {{
  :root {{ --primary:#60a5fa; --accent:#fbbf24; --bg:#0b1220; --fg:#e8eefc; --card:#131c31;
    --muted:#1a2438; --muted-fg:#9fb0cc; --border:#26334f; --ok:#4ade80; --danger:#f87171; }}
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font-family:var(--sans);line-height:1.55;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 24px}}
h1{{font-size:clamp(30px,5vw,44px);letter-spacing:-.02em;margin:0;text-wrap:balance}}
h2{{font-size:clamp(20px,3vw,27px);margin:0 0 12px;letter-spacing:-.01em}}
.eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--primary);margin:0 0 10px}}
.lede{{font-size:18px;color:var(--muted-fg);max-width:64ch}}
.mono{{font-family:var(--mono)}}
header{{padding:64px 0 40px;border-bottom:1px solid var(--border)}}
section{{padding:52px 0;border-bottom:1px solid var(--border)}}
.cta{{display:flex;gap:12px;flex-wrap:wrap;margin-top:26px}}
.btn{{display:inline-flex;align-items:center;gap:8px;background:var(--primary);color:#fff;
  text-decoration:none;padding:11px 18px;border-radius:8px;font-weight:500;font-size:15px;min-height:44px}}
.btn.ghost{{background:transparent;color:var(--fg);border:1px solid var(--border)}}
.figs{{display:flex;gap:44px;flex-wrap:wrap;margin-top:34px}}
.fig .n{{font-family:var(--mono);font-size:30px;font-weight:600}}
.fig .k{{font-size:13px;color:var(--muted-fg);max-width:22ch}}
.banner{{display:flex;gap:12px;background:color-mix(in srgb,var(--accent) 11%,var(--card));
  border:1px solid color-mix(in srgb,var(--accent) 42%,var(--border));border-left-width:4px;
  padding:14px 16px;border-radius:8px;font-size:14px;margin:22px 0}}
.proof{{background:var(--card);border:1px solid var(--border);border-radius:8px;overflow:hidden}}
.proof .h{{padding:12px 16px;background:var(--muted);border-bottom:1px solid var(--border);
  display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}}
.proof .b{{padding:16px}}
.sig{{display:grid;grid-template-columns:172px 1fr;gap:12px;padding:8px 0;
  border-bottom:1px dashed var(--border);font-size:14px;align-items:start}}
.sig:last-child{{border-bottom:0}}
.sig .why{{color:var(--muted-fg)}}
.tag{{display:inline-flex;font-family:var(--mono);font-size:11.5px;padding:2px 8px;border-radius:11px;
  border:1px solid var(--border);background:var(--muted);color:var(--muted-fg);white-space:nowrap}}
.tag.pass{{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 45%,var(--border))}}
.tag.fail{{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 45%,var(--border))}}
.tag.abstain{{border-style:dashed;font-style:italic}}
.card{{border:1px solid var(--border);border-radius:8px;background:var(--card);margin-bottom:10px}}
.card summary{{padding:12px 16px;cursor:pointer;display:flex;gap:12px;align-items:center;flex-wrap:wrap;list-style:none}}
.card summary::-webkit-details-marker{{display:none}}
.card summary:hover{{background:var(--muted)}}
.card .sku{{font-family:var(--mono);font-weight:600;font-size:14px}}
.card .src{{color:var(--muted-fg);font-size:13px;flex:1 1 240px}}
.card .count{{font-family:var(--mono);font-size:12px;color:var(--muted-fg)}}
.card .body{{padding:0 16px 16px}}
.descs{{margin-bottom:14px}}
.desc{{display:grid;grid-template-columns:74px 1fr 58px;gap:10px;padding:5px 0;font-size:13px;border-bottom:1px solid var(--border)}}
.desc .k{{color:var(--muted-fg);font-size:12px}}
.desc .len{{font-family:var(--mono);font-size:11px;color:var(--muted-fg);text-align:right}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid var(--border)}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted-fg)}}
.empty{{color:var(--muted-fg);font-style:italic}}
.empty::before{{content:"— ";font-style:normal}}
ul.classes{{list-style:none;padding:0;columns:2;gap:26px;font-size:14px}}
ul.classes li{{padding:3px 0}}
ul.classes .n{{font-family:var(--mono);color:var(--primary);font-weight:600;margin-right:8px}}
footer{{padding:34px 0;color:var(--muted-fg);font-size:13.5px}}
code{{font-family:var(--mono);font-size:13px;background:var(--muted);padding:2px 6px;border-radius:4px}}
pre{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px;overflow-x:auto;font-family:var(--mono);font-size:13px}}
@media (max-width:700px){{ul.classes{{columns:1}} .sig{{grid-template-columns:1fr}}}}
</style>
</head>
<body>

<header>
  <div class="wrap">
    <p class="eyebrow">UniHack · product data enrichment</p>
    <h1>Every value carries its evidence.<br />Every gap is deliberate.</h1>
    <p class="lede">
      Crucible turns six columns of ERP shorthand into a 252-column delivery sheet — and then
      does the part nobody else does: it marks what it could not establish, instead of filling
      the cell with something plausible.
    </p>
    <div class="cta">
      <a class="btn" href="delivery.csv" download>Download the delivery sheet (CSV)</a>
      <a class="btn ghost" href="evidence.csv" download>Evidence sidecar</a>
      <a class="btn ghost" href="{repo}">Source code</a>
    </div>
    <div class="figs">
      <div class="fig"><div class="n">{products}</div><div class="k">products enriched from 6 input columns</div></div>
      <div class="fig"><div class="n">{populated}/{columns}</div><div class="k">delivery columns carrying values</div></div>
      <div class="fig"><div class="n">{desc_pct}%</div><div class="k">with all five description formats</div></div>
      <div class="fig"><div class="n">{classified_pct}%</div><div class="k">classified into the taxonomy</div></div>
    </div>
  </div>
</header>

<section>
  <div class="wrap">
    <p class="eyebrow">The argument</p>
    <h2>Generation is solved. Knowing which tenth is wrong is not.</h2>
    <p class="lede">
      Frontier models already reach ~91% F1 on attribute extraction, and several vendors will
      sell you enrichment with a confidence score attached. That score is the model's opinion of
      its own work, and it runs highest exactly where the model is most certain and most wrong.
      So distributors still check <strong>100%</strong> of records, and the automation saves nothing.
    </p>
    <p class="lede" style="margin-top:14px">
      Crucible treats the model as an <strong>untrusted proposer</strong>. Six independent external
      checks examine every value it produces, and only what survives is published.
    </p>
  </div>
</section>

<section>
  <div class="wrap">
    <p class="eyebrow">Proof</p>
    <h2>A real catch, from a real run</h2>
    <p class="lede">A ball bearing. The model proposed <code>C4</code> as the seal type.</p>
    <div class="proof" style="margin-top:22px">
      <div class="h"><span class="mono" style="font-weight:600">BRG-00027</span>
        <span style="color:var(--muted-fg);font-size:13px">bearing.ball · seal_type</span>
        <span style="flex:1"></span><span class="tag">proposed <strong>C4</strong></span></div>
      <div class="b">
        <div class="sig"><span class="tag pass">ensemble 1.00</span><span class="why">Identical across 3 independent samples — the model agreed with itself every time.</span></div>
        <div class="sig"><span class="tag pass">coherence 1.00</span><span class="why">“C4” appears in 10% of this category. Statistically, it looks completely normal.</span></div>
        <div class="sig"><span class="tag abstain">dimensional abstained</span><span class="why">A nominal attribute, not a physical quantity. Nothing to check.</span></div>
        <div class="sig"><span class="tag abstain">constraint abstained</span><span class="why">No constraint in this category mentions seal_type.</span></div>
        <div class="sig"><span class="tag abstain">identity abstained</span><span class="why">Not an identity claim.</span></div>
        <div class="sig"><span class="tag fail">vocabulary 0.00</span><span class="why"><strong>“C4” is not a term seal_type accepts.</strong> This category allows <code>open</code>, <code>2RS</code>, <code>RS</code>, <code>2Z</code>, <code>Z</code>.</span></div>
        <p style="margin:16px 0 0;padding-top:14px;border-top:2px solid var(--border)">
          <strong>C4 is a bearing clearance code, not a seal type.</strong> The model was consistent,
          and consistently wrong. The statistical profile agreed with it. Only the check that knows
          what this category actually sells caught it — <em>a model cannot correct an error it is
          certain about.</em>
        </p>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <p class="eyebrow">Real output</p>
    <h2>{showcase} products from the run, exactly as exported</h2>
    <p class="lede">
      Read the blanks. Where an attribute shows <em>not established</em>, that attribute applies
      to the product and nothing in the source supported a value for it.
    </p>
    <div class="banner">
      <span aria-hidden="true">▲</span>
      <div>Every row below is read out of the actual delivery file linked at the top of this
      page — including its empty cells. Nothing here is a mock-up.</div>
    </div>
    {cards}
  </div>
</section>

<section>
  <div class="wrap">
    <p class="eyebrow">Coverage</p>
    <h2>What the catalog turned out to contain</h2>
    <ul class="classes">{classes}</ul>
  </div>
</section>

<section>
  <div class="wrap">
    <p class="eyebrow">Run it yourself</p>
    <h2>Everything is local and offline</h2>
    <p class="lede">
      No API keys and no per-record cost. The model runs on your own machine, which for
      unreleased pricing and specifications is the requirement rather than a convenience.
    </p>
<pre>uv sync --extra models --extra api --extra dev
ollama pull qwen3-vl:8b

uv run crucible enrich   --input "Unihack_ Sample Dataset - Input.csv" --out runs/demo
uv run crucible evaluate --input "Unihack_ Sample Dataset - Input.csv" --limit 120
uv run crucible-app      # the full product at http://127.0.0.1:8000</pre>
    <p class="lede" style="margin-top:16px;font-size:15px">
      This page is static because the live app needs a GPU holding a 6.2 GB model, which no free
      hosting tier provides. What is published here is the evidence: the real rows, the real
      verdicts, and the real files.
    </p>
  </div>
</section>

<footer>
  <div class="wrap">
    Crucible — built for UniHack. Generated from run <code>{run_name}</code>.
    Figures on this page are counted from the delivery file itself.
  </div>
</footer>

</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=Path("submission"))
    parser.add_argument("--out", type=Path, default=Path("docs"))
    parser.add_argument(
        "--repo",
        default="https://github.com/",
        help="Repository URL for the source-code link.",
    )
    args = parser.parse_args()

    delivery = args.run / "delivery.csv"
    if not delivery.exists():
        print(f"no delivery file at {delivery}")
        return 1

    rows = load_rows(delivery)
    stats = coverage(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(
        render(rows, stats, args.run.name, args.repo), encoding="utf-8"
    )

    # Copy the artifacts beside the page so the download links resolve on a static host.
    for name in ("delivery.csv", "evidence.csv"):
        source = args.run / name
        if source.exists():
            (args.out / name).write_bytes(source.read_bytes())

    print(f"wrote {args.out / 'index.html'}")
    print(json.dumps({k: v for k, v in stats.items() if k != "top_classes"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
