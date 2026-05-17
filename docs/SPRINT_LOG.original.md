# Sprint log — 2026-05-17

Single-session lead-Opus handoff + sprint execution. Started from
post-WS-CD-002 state; ended with the full fusion pipeline implemented
+ honest-ellipse Monte Carlo passing + L2 null-steering + L3
modulation classifier shipped + ops/demo design pass complete.

## Shipped (in commit order)

### Admin + ratifications

| Commit | What | Source |
|---|---|---|
| `b957fab` | docs+chore: ADR renumber, ratifications, Phase C bench checklist, py.typed marker | lead |
| `28210cc` | chore(claude): commit council subagent definitions (`.claude/agents/*.yaml`) | lead |
| `df6e30d` | chore(fusion-tests): remove tests/__init__.py — ADR-006 compliance | lead |

### Sprint A — fusion pipeline (the load-bearing chain)

| Commit | Ticket | Headline result |
|---|---|---|
| `e190b76` | WS-CD-003 ticket draft | analytic Jacobian derivation pre-worked |
| `30e1873` | trench_demo geometry + `crlb_analysis.py` | surfaced ADR-005 √5.99 math error |
| `1adc42a` | WS-CD-003 Gauss-Newton MLE | bias 17 m → 3 m on 3 km/5°/3-node geometry; **builder caught a ticket Jacobian residual-sign bug** |
| `398187b` | WS-CD-004 covariance + 95% ellipse | trench-demo Beat D 358.76 m semi-major matches `crlb_analysis.py` to 0.5 m; **10000-sample inclusion test landed at exactly 95 %** |
| `4ad630b` | WS-CD-005 GDOP (ADR-007 D4 unweighted) | Beat C 1.16, Beat D 1.02 |
| `dbd067a` | WS-CD-006 residuals + `is_outlier` flag | strict `> 3·σ`; modulo-form wrap to avoid trig round-off at threshold |
| `44d9800` | WS-CD-007 `StansfieldMLEFuser` (Protocol impl, merge of `worktree-agent-a90329705ca5efc8b`) | `isinstance(fuser, Fuser)` True; Beat D `confidence_level == MEDIUM` per ADR-009 narrative |
| `75a6f82` | WS-CD-008 honest-ellipse Monte Carlo | **sprint-1 acceptance gate met** — see headline table below |

### Sprint B — L2 null-steering + L3 classifier

| Commit | What | Headline result |
|---|---|---|
| `a484efa` | `SCHEMA_VERSION 1.1.0` — `L2_CAPON` enum split + ADR-008 + WS-B-007 ticket | additive MINOR bump; WS-B-004's Capon estimator emits `L2_CAPON` (was `L2_MVDR_NULL`) |
| `b6b6c34` | WS-B-007 `l2_null_steering.py` | canonical scenario: null depth ≥ 25 dB ideal; robustness MC p5 ≥ 15 dB, median ≥ 20 dB |
| `83f32f6` | ADR-010 — formalise ADR-008 §D6 API geometry extension | **builder discovered slide-friendly `null_depth_db` requires array manifold; documented 3 rejected geometry-free formulations + recommendation** |
| `d7833f7` | WS-B-005 L3 modulation classifier | per-class confidence ≥ 0.77 at SNR ≥ 5 dB; honest UNKNOWN at SNR ≤ -5 dB |
| `1ad9352` | WS-B-005 ticket draft | (drafter agent) |
| `f8d4d0d` | WS-B-006 ticket draft (bundled into architect's commit by pre-commit-hook scoop) | threat-library enrichment spec |

### Design + checkpoint

| Commit | What |
|---|---|
| `a115f2a` | ADR-009 — fix ADR-005 1-σ vs 95 % chi-square scale error, adopt re-narrative |
| `ca61f55` | docs(demo): demo script + jury Q&A rehearsal (`docs/demo/script.md`, 634 lines) |
| `f8d4d0d` | docs(design): ops architecture design pass (`docs/design/ops-architecture.md`, 1164 lines) |

### Post-checkpoint batch (2026-05-17 PM) — ratifications + 4 parallel builders

After the 2026-05-17 checkpoint (`90ab700`), lead-Opus ratified the four
open decisions in `docs/design/ops-architecture.md` and spawned four
general-purpose builders in parallel against the main checkout (no
worktree isolation — single uv.lock + shared verification env). Commits
landed in build-finish order, not spawn order.

| Commit | Ticket / package | Headline |
|---|---|---|
| `8a75026` | ADR-011 ratifications | D1 node composition-root carve-out, D2 ops-only-null-steering carve-out, D3 matplotlib, D4 pyyaml + types-PyYAML. 6 lint-imports contracts KEPT. |
| `c8f2778` | WS-B-006 threat library | 47 tests (24 prior + 23 new). 6 YAML profiles (4 real + POLE21/VOLNOREZ stubs). **ELRS vs Crossfire at 868 MHz = multi-match → UNKNOWN with upstream confidence (option 2, honest).** `yaml.safe_load` only; stub-first scan; `ThreatProfileError` raises with offending file name. |
| `8c76cb8` | WS-CD-ops dashboard | 31 tests, 9 panels, 3 layouts (TRENCH / DEBUG / MINIMAL). FixPanel renders %-of-range to **1 decimal**, `n/a` when origin unknown. NullSteeringPanel **caps quoted depth at 20 dB** per ADR-008 §D8. ClassificationOverlayPanel: `None` and `UNKNOWN` both render `"UNKNOWN"`; tooltip differentiates ("no L3 capability" vs "classifier ran, unsure"). ResidualsPanel **re-derives `is_outlier`** per ADR-005 §D3 (contract `FixEvent.residuals_deg` is bare `tuple[float,...]`; flag does not cross the wire). Added one `ignore_imports` line for `rfmesh_ops.panels.null_steering -> rfmesh_dsp.l2_null_steering` so the `independence` clause accepts the D2 carve-out; companion `forbidden` clause still pins every other `rfmesh_dsp` submodule. |
| `9b08096` | WS-CD-cot PyTAK publisher | 45 tests across 4 files + byte-exact `canonical_fix.xml` golden. **WGS-84 meridional/transverse curvatures** in `ellipse_to_polygon_vertices` (not naive equirectangular) — sub-metre accuracy at demo ranges, <1 m at lat 70°. Stale time = `fix.t_unix_ns + 30 s` (not `now + 30s`) — replays stay honest. Transport errors → `CotTransportError`, no auto-reconnect (B3). `range_m=0.0` placeholder in remarks; ops owns operational % display per ADR-005 D5b. Added `cryptography>=42` runtime dep because PyTAK 6.4.0 has a buggy `warnings.warn(exc)` when cryptography is absent (upstream bug; lifted locally to keep demo immune). |
| `f8b2a48` | WS-CD-node composition root | 39 tests, 14 source files. **Subscriber-registration race regression test landed** (`test_fusion_service.py::test_subscriber_registration_race`, per `INHERITED_CONTEXT.md` §5.2) — design prevents the race structurally (inbound queue independent of dashboard pubsub list). **Bearer Protocol is synchronous** (builder caught architect-doc vs contract conflict — contract wins per B1; WifiBearer uses non-blocking socket). LoraBearer ships `loopback=True` v1.0; real-serial path raises `NotImplementedError` (B3 honesty); BothBearer suppresses LoRa `NotImplementedError` so Wi-Fi keeps shipping. L2-uncalibrated → `CapabilityMismatchError` at startup (raise, not exclude). |

**Honest-engineering deviations (documented, no contract change):**

- WS-CD-ops `is_outlier` re-derivation. The contract carries
  `residuals_deg: tuple[float, ...]` (per `FixEvent`); the outlier flag
  is internal to `rfmesh_fusion.residuals.ResidualsResult` and is not
  on the wire. Re-deriving in the panel from the live
  `BearingReport.azimuth_sigma_deg` stream is the honest path. Builder
  documented this in `packages/rfmesh-ops/src/rfmesh_ops/panels/residuals.py`
  + README.
- WS-CD-ops added `pytest-asyncio>=0.23` to workspace dev deps. Needed
  for the dashboard's WebSocket round-trip + dashboard.run async tests.
- WS-CD-node caught a workspace-root `pyproject.toml` bug: the
  `per-file-ignores` glob `tests/**` was root-anchored and had no
  effect on package tests (`packages/<pkg>/tests/**`). Fixed to
  `**/tests/**`. Carries `S` + `ANN` + `PLR2004` + `PLC0415` for tests
  — workspace-wide.
- WS-CD-node delivered the `Bearer` Protocol with **sync send/recv** to
  honor the contract in `rfmesh_contracts.protocols.Bearer`. The
  architect doc had `async def` on the same methods; in the conflict the
  contract wins (Invariant B1). Documented in
  `packages/rfmesh-node/src/rfmesh_node/bearer/__init__.py` docstring.
- WS-CD-node uses **lazy `if TYPE_CHECKING:` imports** for `rfmesh-cot`
  and `rfmesh-ml` to allow parallel builds; `run_fusion.py` catches
  `ImportError` so the fusion-server CLI runs headless even when those
  packages have not landed yet.
- WS-CD-cot added `cryptography>=42` to lift a PyTAK 6.4.0 upstream
  bug (`warnings.warn(exc)` passes Exception instead of str/Warning,
  raising `TypeError` on import when cryptography is absent — official
  PyPI install includes it transitively, uv resolver does not).

### Post-batch council audit + BLOCK fix + demo-replay

After the 4-builder batch landed (`0dbaaeb`), the council protocol per
`CLAUDE.md` lines 23-49 was run (architect / code-reviewer /
rf-dsp-specialist / demo-integrity, in parallel, read-only). One
critical finding required immediate action.

| Reviewer | Batch verdict | Findings |
|---|---|---|
| Code-reviewer | APPROVE | 6 ℹ INFO. Honesty rails verified at code level. |
| RF-DSP specialist | APPROVE | 5 NOTES (CoT `<ellipse>` angle convention; v1.5+ hop-rate discriminator; equirectangular envelope wording; `L2MvdrEstimator` class-vs-label; pseudospectrum adaptive sample count). |
| Demo-integrity | APPROVE | 7 RECOMMENDATIONs. R1 (CoT `range_m=0.0` cosmetic dishonesty) + R4 (`NodeStatusPanel` missing `status_detail` column) reinforced the architect BLOCK. |
| Architect | **BLOCK on `f8b2a48`** | B3 violation: `BothBearer` silently suppressed `LoraBearer.NotImplementedError` → `Node._build_status` hard-coded `healthy=True, status_detail=""` → `NodeStatusPanel` shows green when redundancy is broken. Operator-invisible failure mode in the most jury-visible diagnostic channel. |

**Fix landed (`320423e`):** end-to-end honesty chain through 4 layers:

- `BothBearer` — `contextlib.suppress` replaced with try/except logging WARN once + flipping `_lora_available=False`; subsequent calls skip LoRa entirely. New `is_lora_available()` + `health_summary()` accessors. Returns `"LoRa bearer down, Wi-Fi only"` verbatim per INTERFACES.md §3 canonical example.
- `Node._build_status` — duck-typing on `bearer.health_summary` so Wi-Fi-only / LoRa-only bearers stay valid; populates `NodeStatus.status_detail`.
- `NodeStatusPanel` — 5th column rendering `status_detail` in orange.
- `cot/markers.py` — `_range_m` returns `"n/a"` (R1 alignment with FixPanel's `n/a`-on-unknown-origin); golden `canonical_fix.xml` updated.

4 new regression tests (2 bearer-level + 2 heartbeat-level). All gates green: 43 node + 45 cot + 31 ops; mypy strict + ruff + lint-imports (6 KEPT). Architect's re-audit predicate flips: 4× APPROVE achievable on next pass.

### apps/demo-replay (`d3baabc`)

Greenfield app per architect §2.4 + §3.3. 20 tests (5 scenario loader + 5 ReplayMetadata + 3 ReplayRecorder + 5 ReplayOrchestrator + 2 CLI/aux). In-process orchestration for v1.0 (multi-process is documented migration path; orchestrator seams `_NodeContext` + shared `FusionService`/`DashboardPubSub` already match the multi-process shape).

- `SyntheticReceiver` replay-mode extension **deferred to WS-A-005** — `.iqx + .json` format + `ReplayMetadata` ship now so Phase-C bench captures land in stable format; `NodeReplaySpec.replay_iqx_path` field in place; the actual playback path is the WS-A-005 follow-up.
- `ReplayMetadata` extends actual `IQMetadata` field names (`n_samples`, `start_time_utc`, `format` — not the brief's guessed `total_samples`/`started_at_utc`/`dtype`).
- Test-filename collision avoided per AGENTS.md §3.5 (no `__init__.py` under `tests/`; basenames must be workspace-unique) — renamed `test_metadata.py` → `test_replay_metadata.py`.
- Workspace root: `apps/*` added to `[tool.uv.workspace] members`; `rfmesh-demo-replay` added under `[tool.uv.sources]` + root deps; `apps` added to pytest `testpaths`; `apps/*/tests/` added to mypy excludes.

CLIs: `rfmesh-demo-replay --scenario scenarios/trench_demo.yaml [--pessimism 1.0|1.5|2.0] [--no-cot] [--headless]` and `rfmesh-demo-record --scenario … --output recordings/<session>/ --duration-s 60`.

### 15 non-blocking council follow-ups (`105619f`)

Closes the non-blocking follow-up list surfaced by the four-reviewer council audit; no contracts touched, `SCHEMA_VERSION` unchanged. 15 files, +265/-27 LoC across cot/dsp/ml/node/ops + pyproject + ADR-008 + 1 new parking-lot ticket.

| Bucket | Items |
|---|---|
| Doc / cosmetic | `pyproject.toml` D2 deny-list comment (transitive-chain finding; structural fix parked); `profile.py` Invariant B5 → B3 doc-ref; ADR-008 §Neutral on `L2MvdrEstimator` class-name vs Capon-label intentional. |
| RF-DSP NOTES 1-5 | `markers.py` `<ellipse>@angle` ENU-math vs ATAK-clockwise convention; `ellipse.py` ACCURACY ENVELOPE — radial (~exact) + tangential (chord-sagitta + projection scale-factor); `gdop_heatmap.py` white dashed contour at `_GDOP_WARN_THRESHOLD=6.0`; `pseudospectrum.py` adaptive sample-count WARN-once per `(node_id, sample_count)`. |
| Demo-integrity R2-R7 | `bearings.py` red-X fix marker drawn last; `classification_overlay.py` populates `_confidence_by_class` from `BearingReport` + renders "classifier ran, confidence X.XX" on `FixEvent`; `fix.py` fontsize 8 → 11; `run_node.py` + `run_fusion.py` per-class CLI error handling (FileNotFoundError / yaml.YAMLError / ValidationError / CapabilityMismatchError / NotImplementedError) → exit 2 with `_LOG.error`. |
| Test fixes | 3 unused `noqa: PLC0415` removed in dsp tests (ruff `--fix`). |
| New parking-lot | `docs/tickets/PARKING-LOT-elrs-crossfire-hoprate.md` — v1.5+ hop-rate discriminator; requires real-IQ validation. |

Workspace-wide: 474 tests still pass; mypy strict + ruff + lint-imports clean (6 KEPT).

### Integration check (all six landed)

`uv run pytest -m "not hardware"` workspace-wide at the six-commit
landing: **474 passed**. Composition: 335 pre-batch + 138 from the 4-
builder batch + 4 BLOCK-fix regression + 20 from demo-replay - 23 overlap = 474.

### Earlier integration check (4-builder batch only)

(Historical, before the architect BLOCK fix.)

`uv run pytest -m "not hardware"` workspace-wide at the four-builder
landing: **450 passed in 157.33 s** (from 335 pre-batch). +115 tests:
B-006 (23 new) + ops (31) + node (39) + cot (45) = 138 new minus 23
overlap counted in B-006's prior 24-test baseline.

Workspace state after the batch:
- 6 lint-imports contracts KEPT, 0 broken (D1 + D2 + the WS-B-005
  `rfmesh_ml.features -> rfmesh_dsp.spectrum` and WS-CD-ops
  `rfmesh_ops.panels.null_steering -> rfmesh_dsp.l2_null_steering`
  ignore_imports lines hold).
- mypy strict: every package green at its own scope; the workspace's
  `[tool.mypy.exclude]` keeps tests out as before. The post-checkpoint
  `**/tests/**` per-file-ignores fix is the only non-package edit to
  `pyproject.toml` from the batch.
- ruff check + format: every package green.

**Builder agent type lesson learned (operational, not architectural):**
The first spawn attempt used `caveman:cavecrew-builder` for all four
packages. That agent type **hard-refuses 3+ file scope** and **has no
Bash tool** (cannot run `uv add` / `uv run pytest` / verification gates).
Three agents instantly refused on scope; one (B-006) started writing in
the main checkout (its `--isolation=worktree` flag was honored in name
only — file edits used absolute paths to main) before being killed.
General-purpose agents are the right choice for greenfield package
builds; cavecrew-builder is for surgical 1-2 file edits only.

## ADRs landed (cumulative state at sprint end)

| ADR | Title | Status |
|---|---|---|
| ADR-004 | Array calibration file format | ACCEPTED 2026-05-17 (WS-B sign-off points #1, #2 folded) |
| ADR-005 | Fusion `ConfidenceLevel` policy + 5% threshold | ACCEPTED 2026-05-17; **AMENDED by ADR-009** |
| ADR-006 | No `__init__.py` under `tests/` | (prior) |
| ADR-007 | Fusion algorithm choices (Stansfield + MLE + GDOP D4) | ACCEPTED 2026-05-17 |
| ADR-008 | L2_CAPON enum split + `SCHEMA_VERSION 1.1.0` | ACCEPTED 2026-05-17; **AMENDED by ADR-010** |
| ADR-009 | ADR-005 math correction (1-σ vs 95 % chi-square) | ACCEPTED 2026-05-17 |
| ADR-010 | Null-steering API geometry extension | ACCEPTED 2026-05-17 |
| ADR-011 | Ops architecture ratifications (D1-D4) | ACCEPTED 2026-05-17 |

## Honest-ellipse Monte Carlo — briefing-book headline

From `packages/rfmesh-fusion/tests/test_honest_ellipse_monte_carlo.py`, seed = 42, n_trials = 1000 per scenario:

| Scenario | Inclusion | semi-major (m) | GDOP | Band-dominant |
|---|---|---|---|---|
| trench-demo Beat C (3 L1, σ = 5°) | **93.70 %** | 393 | 1.16 | MEDIUM (100 %) |
| trench-demo Beat D (3 L1 + 1 L2) | **94.80 %** | 358.7 | 1.02 | MEDIUM (100 %) |
| isotropic 4-node ring (σ = 3°) | **94.10 %** | — | — | MEDIUM (100 %) |
| high-SNR 5-node mesh (σ = 1°) | **95.10 %** | — | — | HIGH (100 %) |

All four scenarios inside the [0.92, 0.98] pass band. **The pipeline (L1 / L2 σ honesty → Stansfield → Gauss-Newton MLE → Fisher-info Σ → χ²-scaled ellipse → ConfidenceLevel band) is empirically honest at the 95 % inclusion level.** Frobenius ratio (empirical Σ vs claimed Σ): 1.146 / 1.020 / 1.002 / 1.030 — all dead-centre at 1.0. Mean position bias < 0.5 × semi_minor on every scenario.

## Demo narrative — binding facts

Per `docs/demo/script.md` + `docs/adr/ADR-009-*.md`:

- The trench-demo lives in **MEDIUM** band on all four beats. HIGH only at denser deployment than the 4-node demo. The honest pitch is "the percentage shrinks from 49 % → 26 % → 32 % as nodes join" — the *shrinking* is the deployment-density story.
- Null-depth claim **caps at 20 dB** in all UI text (ADR-008 §D8). Rehearsed range: "15-20 dB typical, up to 25 dB with fresh calibration."
- "One matrix, two products" — reframed as **back-to-back from one R per snapshot** (not "simultaneously"). Anti-desense framing — not ECM.
- Demo built around **recorded IQ from day 1**. Maciej announces: *"This is a 30-second capture from our bench in Poznań last week — same code path, replayed for jury-friendly timing."*

## Hardware bench-side

`docs/hardware/phase-c-bench-checklist.md` landed in `b957fab`.
Single-day procedure, no servo required. Awaiting Maciej's first
execution (planned for the morning after this sprint). PASS = peak
within ±10° of true bearing, ≥ 6 dB prominence. FAIL routes to one of
the 5 pre-enumerated diagnostic branches from `INHERITED_CONTEXT.md`
§3.1.1.

If Phase C reveals real-world σ inflated > 1.5× over simulator, the
ADR-005 5 % threshold may need re-tuning (single-line constant, ADR-005
§D6 anticipates this); the simulator's `MultipathFIRChannel` tap
configuration retunes accordingly.

## Open lead decisions for ratification

Surfaced by the architect's design doc (`docs/design/ops-architecture.md`)
+ the WS-B-006 drafter's notes. Each is small, all four can be ratified
in one short pass:

1. **Import-linter relaxation for `rfmesh-node`** (architect §4.1).
   `rfmesh-node` is the single composition root; cannot satisfy the Star
   `independence` contract by definition. Carve-out: relax the `Star`
   contract for `rfmesh_node` only.
2. **`rfmesh-ops` carve-out for `rfmesh_dsp.l2_null_steering`**
   (architect §1.4). Advantage #4 panel reads receive-pattern compute
   from `rfmesh-dsp`. Add a named exception to lint-imports.
3. **`rfmesh-ops` rendering tech** (architect §3.1). Architect
   recommends **matplotlib** over FastAPI+SPA on three grounds: faster
   to build (1-2 days vs 1-2 weeks), no web-stack risk, jury-credible
   if the data is right.
4. **`pyyaml` runtime dep** (WS-B-006 drafter notes). Needed for
   threat-profile YAML loader. ~few KB pure-Python. Approve the
   `/uvadd-request` to unblock WS-B-006 build.

Lead recommendation: **accept all four** with the documented
justifications. Council can revisit if real-data testing surfaces an
issue.

## Three `[needs validation — TBD]` flags

From the demo-integrity agent's `docs/demo/script.md`:

1. **Precise on-day null-depth number** — waits on the recorded-IQ buffer being captured against the canonical 2-emitter scenario. Likely lands at 15-20 dB per the WS-B-007 MC.
2. **Pre-rendered slide-deck fallback** for the demo's §5 step 4 — content TBD when the slide deck itself exists.
3. **Non-FTS C2 stack integration** — for the §6 honesty section. The current PyTAK adapter targets FreeTAKServer; behaviour with other CoT servers is undocumented.

## Next batch (planned, not started)

In rough priority:

| Ticket | What | Blocker |
|---|---|---|
| **WS-B-006** | Threat-library builder | `pyyaml` /uvadd-request approval (decision #4 above) + WS-B-005 had to land (done) |
| **WS-CD-cot** | `PyTAKCotPublisher` impl per architect design | none |
| **WS-CD-node** | asyncio runtime — Receiver → Estimator → Bearer → Fusion → CoT | decision #1 (lint-imports relaxation) |
| **WS-CD-ops** | matplotlib dashboard, 4 demo beats | decision #2 + #3 (carve-out + rendering tech) |
| **apps/demo-replay** | recorded-IQ replay CLI | depends on `WS-CD-node` |
| **WS-A-005** | RTLSDRDevice port (subprocess wrapper) | Phase C result optional but useful |
| **WS-A-006** | Servo driver port (`servo_uart_v1`) | Phase C must have run by then |
| **WS-A-007** | Firmware ESP32-S2 port + LoRa beacon firmware | independent of Python stack |

## Tests

`uv run pytest -m "not hardware"` at sprint end: **312 passed in 136.94 s**. Breakdown:

- `rfmesh-contracts` — contract validation tests
- `rfmesh-sdr` — simulator + receiver protocol tests (50)
- `rfmesh-dsp` — L1 / L2 MUSIC / L2 Capon / L2 null-steering / sigma-honesty tests (69 with null-steering, 54 pre-null)
- `rfmesh-fusion` — 158 (= 12 projection + 24 stansfield + 12 mle + 23 covariance + 11 gdop + 10 residuals + 30 fuser + 12 confidence + 4 slow honest-ellipse MC + others)
- `rfmesh-ml` — 24 modulation classifier tests

Slow-marker MC suite (excluded from default verify): `uv run pytest -m slow` runs the 4 honest-ellipse scenarios in ~3-4 s on commit `75a6f82`.

mypy strict — clean on every package. ruff check + format — clean. lint-imports — every existing contract KEPT.

## Context state at sprint end

`/context` snapshot before this checkpoint:
- 390 k / 1 M tokens (~39 %).
- Memory files claimed ~56 k per turn (7 docs in claudeMd block).
- Conversation messages: 308 k (the bulk).

Doc compaction via `/caveman-compress` applied to `INTERFACES.md` + `SALVAGE_AUDIT.md` as part of this checkpoint (Step 0 of `i-see-before-you-zippy-pumpkin.md` plan). Backups at `INTERFACES.original.md` + `SALVAGE_AUDIT.original.md`.

---

## 2026-05-17 evening — A3 end-to-end demo-replay exercise

Ran `rfmesh-demo-replay` orchestrator end-to-end against the simulator while Maciej executed Phase C on bench. Artifacts under `docs/demo/artifacts/`.

**Captured:**
- 4× dashboard PNG (one per beat, A→D, ~100 KB each, DEMO_LAYOUT_TRENCH)
- 3× CoT XML (one per `FixEvent`, beats B/C/D, byte-exact `fix_event_to_cot_xml` output)
- 1× JSON summary (`trench_demo_fixes.json`, per-fix scalar table)
- 1× findings note (`A3_NOTES.md`)

**Honest finding (Phase-C-relevant):** the design-intent scenario `trench_demo.yaml` produces **zero L1 bearings** at the documented antenna heights (tx 3 m / rx 2 m) over 2.2 km at 915 MHz. Two-ray destructive null suppresses on-axis RSSI to ~5 dB above the off-axis floor; the L1 prominence gate (6 dB default) correctly refuses (Invariant B3). This is the multipath-dominance failure mode pre-enumerated in `INHERITED_CONTEXT.md` §3.1.1 — simulator faithfully models it.

**Mitigations probed:**
- `tx_h=10 / rx_h=10` recovers L1 (null moves off range)
- `free_space` channel recovers L1 (idealised, used for these artifacts)
- Raising L1 prominence threshold below 6 dB — refused (would break sigma honesty)

**Follow-ups surfaced:**
- **NEXT-1** — adjust `trench_demo.yaml` heights to 10 m and re-run WS-CD-008 Monte-Carlo to re-derive `expected_fix:` numbers in `docs/demo/script.md`.
- **NEXT-2** — add `--channel-override` flag to `rfmesh-demo-replay` so artifact capture does not need a sister YAML.
- **NEXT-3** — fix `--headless` mode: orchestrator's `_relay_fixes_to_sink` requires the dashboard queue to also exist. Trace `apps/demo-replay/src/rfmesh_demo_replay/replay.py:545`.

**Sister scenario:** `scenarios/trench_demo_artifact.yaml` (free-space channel, same geometry/nodes/beats as `trench_demo.yaml`), only for artifact capture — does not replace the design-intent file.

**Capture harness:** `scripts/capture_demo_artifacts.py` — programmatic orchestrator + subscriber + per-beat dashboard render, bypasses the `--headless` bug. Reproducible: `uv run python scripts/capture_demo_artifacts.py`.

**Artifact-pass numbers** (free-space channel, not multipath-loaded):

| Beat | Nodes | semi_major_m | semi_minor_m | gdop | confidence |
|---|---|---|---|---|---|
| B | west+east | 17.29 | 10.41 | 1.53 | HIGH |
| C | +south | 9.01 | 5.12 | 1.16 | HIGH |
| D | +L2 | 10.63 | 6.75 | 1.16 | HIGH |

The ellipse axes are simulator-clean (an order of magnitude tighter than the Monte-Carlo multipath-loaded numbers). Pipeline integrity confirmed; honesty budget unchanged.
