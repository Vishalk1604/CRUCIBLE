# Start here

You are picking up **CRUCIBLE**, our UniHack entry. This zip is a complete working copy.

## What to do first

**1. Get the repo connected.** The code here is identical to
<https://github.com/Vishalk1604/CRUCIBLE> at the time of handoff, but this copy has no
`.git` directory. Clone the repo and copy this zip's contents over it, so you keep the
history and can push:

```bash
git clone https://github.com/Vishalk1604/CRUCIBLE.git
```

Then copy everything from this zip into the cloned folder, overwriting. Set your own
identity so commits are attributed to you:

```bash
git config user.name "your-name"
```

```bash
git config user.email "your-email"
```

**2. Read these three, in order.**

| File | Why |
|---|---|
| `CLAUDE.md` | Claude Code loads this automatically. The project's thesis, six non-negotiables, and the traps that already cost hours. |
| `docs/HANDOFF.md` | Install steps, how to run it, current state, and what to do next in priority order. |
| `docs/RESULTS.md` | The measured numbers, the verifier ablation, and the caveats that go with them. |

**3. Install.** Full detail in `docs/HANDOFF.md`, but the short version is Python 3.11 +
[uv](https://docs.astral.sh/uv/) + [Ollama](https://ollama.com/download), then:

```bash
uv sync --extra models --extra api --extra dev
```

```bash
ollama pull qwen3-vl:8b
```

```bash
uv run crucible-app
```

## What's in here that git does not have

- **`data/generated/harvest/`** — the extraction caches. These are the valuable part.
  Without them the first run costs about 20 minutes of inference for the main harvest and
  another 36 for the two resampling passes. With them the app starts instantly.
- **`.env`** — the Icecat API tokens. **Do not commit this file**; it is already in
  `.gitignore`. Rotate the tokens at MyIcecat if this zip ends up anywhere public.

## One thing that is not here

`data/raw/daily.index.xml.gz` — the Icecat product index. It was downloaded but never
made it into the project folder, so treat it as still needed. It is the blocker for
Priority 1 in the handoff doc. Get it from:

```
https://data.icecat.biz/export/freexml/EN/daily.index.xml.gz
```

That endpoint needs the Icecat account password over HTTP Basic auth, or your IP
whitelisted under MyIcecat → Allowed IP addresses. Save it into `data/raw/`.

## Where things stand in one paragraph

The whole pipeline works: messy ERP-style product text goes in, a local model extracts
attributes, four independent verifiers judge each value, a learned scorer fuses their
signals, and conformal risk control converts that into a certified error rate at a chosen
automation level. It holds every promise it makes and refuses when the evidence is too
thin. The unfinished business is that the corpus is generated rather than real — the
*errors* are genuine (the model's own), but the products are synthetic. Fixing that with
Icecat data is the top priority, and it is also what unlocks a 2% guarantee, currently
missed by exactly one error out of 149.
