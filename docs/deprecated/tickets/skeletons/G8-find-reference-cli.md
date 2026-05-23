# TICKET G8: `rfmesh-find-reference` site-selection CLI

## Goal (one sentence)
Build a small CLI tool that takes a `(lat, lon, radius_km)` triple and proposes candidate reference emitters (cellular masts) ranked by `(signal_strength × spatial_isolation × distance_in_operating_envelope)`, turning Maciej's btsearch.pl + map-eyeball protocol into one repeatable command.

## Context (links only, not content)

- Phase C lesson (`docs/phase-c-report/findings.md` §5): site selection is non-trivial and part of the system, not luck. Three masts → two failures → one success. Mast A was too close (650 m, multipath dominance), Mast B was contaminated by co-channel interferer 5 km off-axis. Mast C succeeded because Maciej drove to a clear-line-of-sight spot 3 km from a known-strong tower identified on btsearch.pl's heatmap.
- This CLI is the Tier G8 idea from the project audit synthesis on 2026-05-18 (commit `c4eeebb` SPRINT_LOG).
- Reference: `docs/hardware/phase-c-bench-checklist.md` §2.A.2.bis + §2.A.2.ter (the protocol this CLI automates).
- btsearch.pl is a public Polish cellular-network database (no auth needed for public data); polite scraping per `robots.txt` is the assumption.

## Acceptance criteria

(Executor fills these. Suggested shape:)

1. New CLI entry point: `rfmesh-find-reference --lat <deg> --lon <deg> --radius-km <km> [--band {gsm900,gsm1800,lte800,lte2100}] [--top-n <N>]` lands in a new `tools/find-reference/` package (NOT a `packages/rfmesh-*/` directory — this is operator tooling, not part of the core star architecture). Or alternative: `scripts/find_reference.py` (lighter weight; pick one and document why).
2. The CLI prints a table:
   ```
   rank | freq_mhz | tower_lat | tower_lon | range_m | bearing_deg | est_signal | est_isolation | score
   ```
3. Scoring: distance is **scored low below 2 km** (multipath dominance) and **scored low above 5 km** (out of operating envelope). Signal is scored high. Isolation is scored high if the nearest co-channel transmitter is ≥ 6 dB weaker than the target.
4. Tests with mocked btsearch HTTP response (responses recorded in `tests/fixtures/btsearch_*.html`):
   - `test_rank_prefers_3km_over_650m`
   - `test_rank_penalises_co_channel_interferer_within_5km`
   - `test_rank_returns_empty_when_no_masts_in_radius`
   - `test_cli_emits_json_with_--json-flag` (machine-parseable output)
5. `uv run mypy tools/find-reference` (or `scripts/find_reference.py`) strict clean.
6. `uv run ruff check` clean.
7. `uv run pytest tools/find-reference/tests` (or wherever the tests land) passes.
8. README under the tool dir explaining: input format, output format, scoring weights, btsearch ToS note (we scrape public data politely, with rate-limiting).

## Out of scope (explicit non-goals)

- Do NOT integrate this into the rfmesh-node runtime. This is operator tooling for site recon, not part of the contract path.
- Do NOT add it to `packages/rfmesh-*/`. It lives in `tools/` or `scripts/`.
- Do NOT add HTTP retries, exponential backoff, or production-grade scraping resilience. This is a hackathon-grade convenience tool.
- Do NOT cache scraped data persistently. Per-run is fine.
- Do NOT scrape any data beyond what btsearch.pl publishes publicly. No auth-required pages, no login flows.
- Do NOT modify any contract or any existing CLI.

## Files you may touch

- `tools/find-reference/` (create directory with `pyproject.toml`, `src/`, `tests/`, `README.md`) — OR —
- `scripts/find_reference.py` (single-file alternative; simpler) + `scripts/tests/test_find_reference.py`
- Workspace root `pyproject.toml` only if you add a new tool / script entrypoint (which you don't strictly need to)

## Files you may NOT touch

- `packages/rfmesh-contracts/**`
- Any other `packages/*/` (this is operator tooling, isolated)
- Anything under `firmware/`, `docs/demo/`, `docs/adr/`
- Anything under `apps/demo-replay/`

## Stop conditions

- Stop after producing the diff. Paste mypy + ruff + pytest output. Open PR.
- If btsearch.pl's HTML structure changes mid-development and the scrape breaks, STOP and write a SCRATCHPAD entry. Do not silently fall back to a worse data source.
- If the scoring heuristic ends up being non-defensible (e.g. all real-world masts score below 0.2 and the CLI returns "no candidates" for every input), STOP and surface the calibration question to Maciej. This is operator-facing tooling; it must produce useful output on representative input.
- If you find yourself wanting to add async / multiprocessing for "performance", refuse — the search radius is small enough that sequential scrape is fine.

## Council gates

- [ ] **architect** (always — confirms it does not leak into the star architecture)
- [ ] **code-reviewer** (always)
- [ ] rf-dsp-specialist (NO — no DSP)
- [ ] demo-integrity (OPTIONAL — only if the CLI's output is intended for jury slide content)

## Suggested owner

Friend (PM hat + Python systems). ~½ day. Independent of the main code path; safe to develop in parallel with anything else.

## Implementation notes (non-binding)

- Start with the btsearch.pl coverage map URL (publicly accessible). Look for the JSON API that backs the map; if it exists, use it. If not, parse HTML with BeautifulSoup.
- Distance scoring: piecewise linear. Below 1 km → 0 (no-fly zone, sub-Phase-C). 1-2 km → linear ramp 0 → 1. 2-5 km → constant 1 (operating envelope). 5-10 km → linear ramp 1 → 0.2. Above 10 km → 0.2 (theoretically reachable but unreliable).
- Signal scoring: pull from btsearch's published tower power if available. If not, proxy with `tower_class × distance^-2`. Document the proxy in the README.
- Isolation scoring: for each candidate tower at frequency f, find the nearest other tower at frequency within `f ± 1 MHz` and within the radius. If the nearest co-channel is ≥ 6 dB weaker than the target at the operator's position, isolation = 1.0. Below 6 dB → 0.
- Final score: geometric mean of the three. Multiplicative penalty matches the operational reality — any one factor below threshold makes the site unrecoverable.
- CLI design: `--json` flag for machine output (for future automation), default human-readable table.
- Polite scraping: `time.sleep(2)` between requests; `User-Agent: rfmesh-find-reference/0.1 (Maciej; logmaciej@gmail.com)`; respect `robots.txt`.

## Claude Code execution prompt skeleton

```
TICKET G8: rfmesh-find-reference CLI

Read first:
1. docs/phase-c-report/findings.md §5 (the lessons-learned that this CLI automates)
2. docs/hardware/phase-c-bench-checklist.md §2.A.2.bis + §2.A.2.ter
3. This ticket's Implementation Notes section

Goal: one CLI command that ranks candidate reference masts by physics, not by hand-eyeballing btsearch.pl.

Files to create: tools/find-reference/* OR scripts/find_reference.py (pick one).
Files NOT to touch: packages/rfmesh-*, firmware/, docs/, contracts.

Acceptance: see ticket. Test against mocked btsearch HTML.

Council gates: architect + code-reviewer. Paste mypy + ruff + pytest. Do NOT commit.
```

## Why this matters for BoTH3

The jury Q&A "How did you choose a forward observation post?" is rehearsed in `docs/demo/script.md`. The honest answer is *"we ran this CLI and drove to the top-ranked candidate"*. A CLI receipt for site selection is more credible than *"Maciej eyeballed btsearch.pl"* — same protocol, better defensibility.
