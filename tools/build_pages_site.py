"""Publish the real interface to GitHub Pages, not a separate site that looks like it.

There were two front ends: the one `crucible-app` serves and a hand-written static page
that only resembled it. Two surfaces drift, and the published one is the only one most
people will ever see, so it drifted into being the worse of the two. This replaces the
static page with the actual application files.

Three things have to change on the way out
------------------------------------------
**Paths.** The app serves from the domain root, so its links are absolute - `/certify`,
`/static/crucible.css`. Pages serves this repo from `/CRUCIBLE/`, where an absolute path
resolves to the user's root and 404s. Every internal link is rewritten relative.

**Routes become files.** `/certify` is a FastAPI route; on Pages it has to be
`certify.html`. The rewrite maps route to filename.

**Live data becomes fixtures.** The certify page reads four endpoints. All four are
read-only projections of a session that is fixed once built, so their responses are
captured to JSON and the page is pointed at those files instead. The numbers are the ones
the running app produced - snapshotted, not invented.

What cannot come across
-----------------------
`/app` is the upload-and-enrich workspace. It posts a file and polls a job that runs a
6.2 GB model, and no amount of rewriting makes that work on a static host. It is published
with its controls disabled and a banner saying where it does run, which is more useful
than omitting it and more honest than shipping a button that silently fails.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "crucible" / "api" / "static"
OUT = ROOT / "docs"
API = "http://127.0.0.1:8000"

#: Dial stops the certify page offers; each needs its own captured response.
DIAL_STOPS = [0.005, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10, 0.15]

#: App route -> published filename.
ROUTES = {
    "/certify": "certify.html",
    "/signin": "signin.html",
    "/app": "app.html",
    "/": "index.html",
}


def rewrite_links(html: str) -> str:
    """Turn app-absolute URLs into Pages-relative ones."""
    html = html.replace('href="/static/', 'href="').replace('src="/static/', 'src="')
    for route, filename in ROUTES.items():
        if route == "/":
            continue
        html = html.replace(f'href="{route}"', f'href="{filename}"')
        html = html.replace(f'"{route}"', f'"{filename}"')
    # Bare root links last, so they cannot swallow the longer routes above.
    html = re.sub(r'href="/"', 'href="index.html"', html)
    return html


def capture(client: httpx.Client) -> dict[str, object]:
    """Snapshot every response the certify page reads."""
    data: dict[str, object] = {
        "stats": client.get(f"{API}/api/stats").json(),
        "sweep": client.get(f"{API}/api/sweep").json(),
        "certify": {},
        "review": {},
    }
    for alpha in DIAL_STOPS:
        key = f"{alpha}"
        data["certify"][key] = client.get(f"{API}/api/certify", params={"alpha": alpha}).json()
        data["review"][key] = client.get(
            f"{API}/api/review", params={"alpha": alpha, "limit": 25}
        ).json()
    return data


#: Shim injected into the certify page. It intercepts fetch and answers from the captured
#: fixtures, so the page's own logic runs unmodified - the alternative is editing its
#: JavaScript, which would make the published page diverge from the app again.
SHIM = """
<script>
// Static build: the four read-only endpoints are served from a captured snapshot.
// The page's own code is untouched, so this file stays honest to the running app.
(function () {
  const ready = fetch("api-snapshot.json").then(r => r.json());
  const nearest = (want, keys) =>
    keys.reduce((a, b) => Math.abs(b - want) < Math.abs(a - want) ? b : a);

  // NB: the outer wrapper must not be async - an async IIFE returns a Promise, and
  // assigning that to window.fetch replaces the function with a thenable, so every
  // call throws and the page reports it cannot reach the API.
  window.fetch = ((original) => async (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if (!url || !url.includes("/api/")) return original(input, init);

    const snap = await ready;
    const u = new URL(url, location.href);
    const alpha = parseFloat(u.searchParams.get("alpha"));
    const reply = (body) =>
      new Response(JSON.stringify(body), {
        status: 200, headers: { "Content-Type": "application/json" },
      });

    if (u.pathname.endsWith("/api/stats")) return reply(snap.stats);
    if (u.pathname.endsWith("/api/sweep")) return reply(snap.sweep);
    if (u.pathname.endsWith("/api/certify")) {
      const k = nearest(alpha, Object.keys(snap.certify).map(Number));
      return reply(snap.certify[String(k)]);
    }
    if (u.pathname.endsWith("/api/review")) {
      const k = nearest(alpha, Object.keys(snap.review).map(Number));
      return reply(snap.review[String(k)]);
    }
    return reply({});
  })(window.fetch);
})();
</script>
"""

#: Banner for the workspace page, whose upload cannot work without a GPU behind it.
WORKSPACE_BANNER = """
<div style="margin:0;padding:14px 20px;background:#1a1d24;color:#e8eaed;
            font:14px/1.5 Inter,system-ui,sans-serif;border-bottom:1px solid #2a2f3a">
  <strong style="color:#6ea8fe">This workspace runs locally.</strong>
  Uploading a catalog runs a 6.2&nbsp;GB model on a GPU, which no static host provides.
  The controls below are disabled here. Run <code
  style="font-family:JetBrains Mono,monospace">uv run crucible-app</code> to use it, or
  see the <a href="certify.html" style="color:#6ea8fe">certification dashboard</a> and the
  <a href="delivery.csv" style="color:#6ea8fe">real output</a>, both live on this site.
</div>
<script>
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("input,button,select,textarea")
          .forEach(el => { el.disabled = true; el.style.opacity = 0.55; });
});
</script>
"""


def inject(html: str, snippet: str) -> str:
    """Put a snippet where it runs before the page's own scripts.

    Order matters and getting it wrong is silent. These pages have no <body> tag, so an
    earlier version appended at the end of the document - after the boot script had
    already run, failed to reach an API that does not exist on a static host, and written
    "cannot reach the API" into the header. The shim has to be installed before any
    script that might call fetch.
    """
    for pattern in (r"<head[^>]*>", r"<body[^>]*>"):
        match = re.search(pattern, html, re.I)
        if match:
            at = match.end()
            return html[:at] + snippet + html[at:]

    # No head or body: land before the first script instead of after the last one.
    match = re.search(r"<script", html, re.I)
    if match:
        at = match.start()
        return html[:at] + snippet + html[at:]
    return snippet + html


def build(skip_capture: bool = False) -> int:
    OUT.mkdir(exist_ok=True)

    if not skip_capture:
        try:
            with httpx.Client(timeout=60.0) as client:
                client.get(f"{API}/api/stats")
                snapshot = capture(client)
        except httpx.HTTPError as exc:
            print(f"cannot reach {API}: {exc}\nStart it with: uv run crucible-app")
            return 1
        (OUT / "api-snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
        stats = snapshot["stats"]
        print(f"captured snapshot: {stats.get('nValues')} values, "
              f"AUROC {stats.get('auroc'):.3f}")

    shutil.copy2(STATIC / "crucible.css", OUT / "crucible.css")

    pages = [("landing.html", "index.html", None),
             ("index.html", "certify.html", SHIM),
             ("signin.html", "signin.html", None),
             ("app.html", "app.html", WORKSPACE_BANNER)]

    for source, target, snippet in pages:
        html = (STATIC / source).read_text(encoding="utf-8")
        html = rewrite_links(html)
        if snippet:
            html = inject(html, snippet)
        (OUT / target).write_text(html, encoding="utf-8")
        print(f"  {source:14s} -> docs/{target}")

    # A 404 so a stale absolute link lands somewhere useful rather than on GitHub's page.
    (OUT / "404.html").write_text(
        rewrite_links((STATIC / "landing.html").read_text(encoding="utf-8")), encoding="utf-8"
    )

    missing = [f for f in ("delivery.csv", "evidence.csv") if not (OUT / f).exists()]
    if missing:
        print(f"  warning: {', '.join(missing)} absent — download links will 404")

    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(build(skip_capture="--skip-capture" in sys.argv))
