# Open source / local options for PCB routing and ML assistance

Notes gathered 2026-10-27. Nothing here is installed by this file; it is for review.

## 1. Classical open source autorouters (recommended, reliable)

- **Freerouting** — the de facto open source autorouter, Java, works with KiCad via
  Specctra DSN/SES export-import. Not machine learning, but mature and fully local.
  Install: Java runtime + `freerouting.jar` (see `LINUX_SETUP.md`).
- **KiCad 8/9 built-in interactive router** — push-and-shove, already installed here.
  Best tool for the critical nets; let an autorouter do the rest.
- **In-house scripts in this repo** — maze router and lane tools. These already work
  on this machine; they only need KiCad's Python module.

## 2. Machine learning routers (experimental)

- **DeepPCB** — ML/cloud autorouter. Free tier exists but it is a hosted service,
  so it is *not* local and the engine is not open source. Rejected if local-only.
- **Academic RL routers on GitHub** — various reinforcement-learning place-and-route
  papers with released code. Research quality: small benchmark boards, no KiCad
  integration, fragile dependency pins. Possible to experiment with, not production.
- **Open-source EDA ML stacks (OpenROAD / CircuitNet etc.)** — aimed at chip-level
  (ASIC) routing rather than PCBs. Interesting to read, wrong target for a keyboard PCB.

Conclusion: there is currently no local, open source, ML autorouter worth depending on.
Freerouting plus KiCad's interactive router remains the practical choice.

## 3. Local ML that is genuinely useful here

- **Ollama** + a coding model (e.g. a 7–14B code model) — runs locally, good for
  writing and reviewing the routing scripts, DRC rule sets and the KiCad Python API calls.
- **llama.cpp** — lighter alternative if GPU memory is tight.
- Use these as an assistant to the routing scripts, not as the router.

## 4. Commercial services with free or student tiers

These are not open source, but they cost nothing to try and several are free
outright for students with a school email address.

- **Autodesk Fusion / Eagle** — full free education licence for students and
  educators, renewable yearly. Includes the Eagle-derived PCB editor and its
  autorouter. Verify with a school email or proof of enrolment.
- **Altium Designer** — free student licence (6-month renewable) plus Altium 365
  cloud. Industry standard, includes a strong interactive and automated router.
- **Altium CircuitMaker** — free for anyone, no student status needed, for
  non-commercial and open hardware projects.
- **Cadence OrCAD X / Allegro X** — free student and university editions with
  board size limits; includes Allegro's autorouter.
- **Siemens Xpedition / PADS student editions** — available through university
  programmes, generally needs an institutional request.
- **DeepPCB (Instadeep)** — the ML autorouter mentioned above. Cloud only, has a
  free tier for small boards; upload a DSN file, get a routed SES back, which
  KiCad can import. Worth trying as a comparison against Freerouting.
- **Flux.ai** — browser EDA with an AI copilot. Free tier for public projects
  and an education discount; useful for schematic-level help, routing is manual.
- **JITX** — generative hardware design, free tier for evaluation.

Caveats: all of these are cloud or licence-gated, so the design leaves the
machine; student terms are usually non-commercial only; and licence terms change,
so check each vendor's education page before relying on it.

## 6. Suggested next step

### From the open source side

1. Finish the Freerouting + shapely setup from `LINUX_SETUP.md`.
2. Run a full board through Freerouting and judge the result.
3. Only if that is unsatisfactory, spend time on an RL router experiment.
