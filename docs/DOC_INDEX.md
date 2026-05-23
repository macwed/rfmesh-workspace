# rfmesh — Doc index (selective-read pointer)

**Status:** living. Edited whenever a binding doc gains or loses a section.
**Date:** 2026-05-23 (created with the post-pivot doc consolidation).
**Audience:** any agent (Claude Code / council / Maciej / lead-Opus)
that needs to find a specific binding fact without scanning the entire
documentation tree.

This file is a *table of contents over the binding documentation*. Each
entry below names a doc + section + the question that section answers.
Use it to jump straight to a fact instead of loading the whole tree
into context.

If you find yourself searching for something that is not in this index,
either (a) the answer is in code and not in docs (preferred — read the
Pydantic model / docstring), (b) the answer is in `docs/deprecated/`
(historical, **not binding**), or (c) the index is stale — flag it.

---

## §1 Root-level binding docs (read in cold-start order)

### `README.md`
- *What is rfmesh?* — top of file (one paragraph, comms-first per ADR-021).
- *Getting started* — `uv sync` + `just verify`.
- *Document read order* — list of root docs with one-line summaries.
- *Repo layout* — annotated tree.
- *Governance, short version* — B1/B5/B2/B3 + verify gate in 5 bullets.

### `ARCHITECTURE.md`
- **§0 What rfmesh is** — comms-first product framing (ADR-021).
- **§1 The three capability layers** — L1 amplitude DF / L2 phase-coherent
  (MUSIC + Capon + null-steering) / L3 ML classification. Single-layer
  nodes are first-class.
- **§2 Three axes of variability, each absorbed in one place** — SDR
  hardware (in `rfmesh-sdr`), per-node capability (in `rfmesh-node`),
  node count (in `rfmesh-fusion`).
- **§3 The contracts are the contract** — `rfmesh-contracts/` star
  topology + B1 governance + `SCHEMA_VERSION` tripwire.
- **§4 Simulator is a first-class citizen** — `SyntheticReceiver`
  unblocks every layer; honesty constraint on its noise / multipath /
  calibration-error models.
- **§5 Hardware validation runs in parallel + gated honestly** — Phase
  C ran (Mast C anchor per ADR-014); L2 phase-coherent only if
  Pluto+/bladeRF arrives.
- **§6 Why no GNSS / no TDOA / no magnetometer** — and what that
  buys (EW-resilience by construction).
- **§7 What the demo shows + why each piece is there** — panel list,
  honesty payload, A/B null-steering slide.
- **§8 What is binding and what is not** — explicit scope line.
- **§9 Documents in the coordination package** — same role as this
  doc, abbreviated.
- **Appendix A — Universal contract conventions** — quick reference
  for `INTERFACES.md` §0 (units, sigma, validation, versioning).
- **Appendix B — Hardware quirks + regression anchors** (folded from
  retired `INHERITED_CONTEXT.md`):
  - B.1 MG996R clone pulse range unknown
  - B.2 SDR amplitude not absolute power
  - B.3 Why AoA, not TDOA
  - B.4 Why no magnetometer
  - B.5 GNSS observed, not depended on
  - B.6 Agent push-back authority
  - B.7 Regression — 25 dB SNR-invariant
  - B.8 Regression — subscriber-registration race
  - B.9 Open hardware-validation items (current status)

### `INTERFACES.md`
- **§0 Universal conventions** — full version (units, sigma,
  validation, versioning). Appendix A in ARCHITECTURE.md is the
  abbreviation; this is canonical.
- **§1 Enumerations** — `Capability` / `EmitterClass` / `ConfidenceLevel`
  / `ArrayGeometry` / `BearerKind` semantics + producer / consumer
  map.
- **§2 Geospatial value types** — `GeodeticPosition` + `EllipseENU`
  with the 2.448-σ chi-square scaling convention.
- **§3 Wire-format messages** — `BearingReport`, `FixEvent`,
  `NodeStatus` per-field semantics (load-bearing: `azimuth_sigma_deg`
  honesty, the `None` vs `EmitterClass.UNKNOWN` distinction, the
  `raw_pseudospectrum` little-endian-float32 0.5°-step convention).
- **§4 Configuration schemas** — `SDRConfig`, `ArrayConfig`,
  `BearerConfig`, `NodeConfig`, `FusionConfig`.
- **§5 Behavioural contracts** — `Receiver`, `CoherentReceiver`,
  `BearingEstimator`, `Fuser`, `CotPublisher`, `Bearer`. The five
  Protocol classes every cross-package boundary uses.
- **§6 Change-control reminders** — what triggers a `SCHEMA_VERSION`
  bump, what does not.

### `AGENTS.md`
- **§1 The Seven Binding Invariants (B1-B7)** — the most-referenced
  section in the whole tree. Memorise.
  - B1 Contracts are frozen
  - B2 Honest sigma on every `BearingReport`
  - B3 No silent fallbacks
  - B4 Demo honesty payload
  - B5 `rfmesh-dsp` + `rfmesh-fusion` are pure
  - B6 Software agents do software (hardware is operator domain)
  - B7 The 8 architectural advantages are binding pitch content
- **Workspace Disciplines (WD-1, WD-2)** — cross-package import
  enforcement, golden-test requirement.
- **§2 Scope boundary** — what agents do not touch.
- **§3 Allowed and forbidden commands** — command allowlist + the
  force-push prohibition (binding even for the lead).
- **§3.5 Repo conventions** — `__init__.py`-free test dirs, ADR-006.
- **§4 Ticket format** — `Files-you-may-touch` + `Files-you-may-NOT-touch`
  + Stop conditions.
- **§5 Verification protocol** — `just verify` + the `/uvadd-request`
  pattern for new runtime deps.
- **§6 Escalation** — when to stop and write a scratchpad note.
- **§7 Git workflow** — lead-Opus on `main`, no force-push to `main`.
- **§8 What this file does not cover** — what's elsewhere.

### `CLAUDE.md`
- **Autonomy policy** — when to escalate to Maciej (4 items).
- **Council review protocol** — sequential gate, 4× APPROVE before
  merge to `main`.
- **Commands** — `just verify` + scoped variants.
- **Architecture orientation** — one-paragraph version of ARCHITECTURE.md §0.
- **Key documents** — read order on cold start.

---

## §2 ADRs (architectural decision record, `docs/adr/`)

All ADRs are append-only and binding once `ACCEPTED`. **Critical /
load-bearing ones** below; the rest are reachable by file name and
are read in full when their topic comes up.

| # | Title | Why it matters |
|---|---|---|
| ADR-001 | Monorepo `uv` workspace | Why we are one repo, not many. |
| ADR-002 | Contracts as `typing.Protocol` | Structural typing chosen over ABCs. |
| ADR-003 | No GNSS / no TDOA / no magnetometer | EW-resilience invariants (ARCHITECTURE.md §6 anchor). |
| ADR-004 | Array calibration file format | Per-element phase/gain table for L2. |
| ADR-005 | Fusion confidence-level policy | `HIGH/MEDIUM/LOW` thresholds on `FixEvent`. |
| ADR-006 | No `__init__.py` in tests | Why test dirs are not packages. |
| ADR-007 | Fusion algorithm choices | Stansfield seed + Gauss-Newton MLE. |
| ADR-008 | L2 Capon enum + null-steering reservation | `L2_CAPON` member + `L2_MVDR_NULL` placeholder. |
| ADR-009 | Confidence-band math correction + demo narrative | Ellipse / GDOP cross-check. |
| ADR-010 | Null-steering API geometry extension | Steering-vector input shape. |
| ADR-011 | Ops architecture ratifications | Dashboard pub-sub seams. |
| ADR-012 | `SCHEMA_VERSION` `Literal[...]` type | Tripwire for contract drift. |
| ADR-013 | `SCHEMA_VERSION` 1.2.0 honesty extensions | Paired honesty fields (deferred). |
| ADR-014 | **Mast C empirical anchor** | The simulator's calibration ground truth. |
| ADR-015 | **Firmware target ESP32-C6** | Current MCU; supersedes S2 (which superseded C3). |
| ADR-016 | RF unplausibility — concealment leakage lens | Posterior overlay math. |
| ADR-017 | Jam-shadow + unified exposure engine | Locate/jam/hide engine. |
| ADR-018 | **Operator-authored CoT messaging** | Backend → TAK message authoring. |
| ADR-019 | **Antenna rendezvous (directional link)** | GPS-prior pointing + scan-and-stare. |
| ADR-020 | Frequency dimension + band-aware fusion | Multi-band fusion split. |
| ADR-021 | **Directional comms primary (the pivot)** | Comms-first product framing. The other 20 ADRs read against this one. |

**Critical read-first for any new agent: ADR-021, ADR-019, ADR-015, ADR-014,
ADR-008.** Everything else is on-demand by topic.

---

## §3 Other binding docs in `docs/`

### `docs/ADVANTAGES.md`
- **§0 What rfmesh is competing on** — pitch framing per ADR-021.
- **§1 The eight (now nine) architectural advantages** — each one
  with caption, anchor, and dashboard panel cross-reference.
- **§1.5 Pitch order (ADR-021)** — which advantage leads which
  slide (the deck order).
- **§2 How they compound** — the panel ↔ spoken-script mapping.
- **§3 Binding rule** — B7 cross-reference; the change-control
  contract for proposing removal / extension.

### `docs/wire-protocols/servo_uart_v1.md`
Frozen wire spec for the host → ESP32 servo-controller protocol
(COBS + CRC-16/CCITT-FALSE + TLV). Read before any change to
`firmware/main/*.c` or `rfmesh-servo`. §9 has the canonical
ESP32-C6 pin map (ADR-015).

### `docs/MANUAL.md`
Operator-facing setup / dev / demo / firmware-flash. Companion to
README.md; where they disagree, package-local READMEs win.

### `docs/operator-cot-guide.md`
How an operator authors a CoT/TAK message through the backend
(ADR-018). Reference doc, not binding.

### `docs/data-pipeline.md`
End-to-end node → backend data pipeline narrative; mostly
descriptive.

### `docs/demo/README.md`
Stub — the pre-pivot deck is in `docs/deprecated/demo/`; rewrite
pending ADR-021 acceptance.

---

## §4 Per-package READMEs (close-to-code, authoritative for their package)

- `packages/rfmesh-contracts/README.md` — the contract package itself.
- `packages/rfmesh-sdr/README.md` — SDR adapters + `SyntheticReceiver`.
- `packages/rfmesh-cot/README.md` — PyTAK CoT publisher.
- `packages/rfmesh-ops/README.md` — matplotlib dev/diagnostic dashboard.
- `apps/demo-replay/README.md` — in-process orchestrator (replay path).
- `apps/operator-console/README.md` — operator console scaffold.
- `deployment/README.md` — Docker stack composition.
- `deployment/node/RUNBOOK.md` — node bring-up runbook.
- `deployment/webmap/README.md` — FreeTAKServer webmap caveats.
- `field-deploy/README.md` — Pi systemd field-deployment.
- `firmware/README.md` — ESP32-C6 firmware build + flash.
- `firmware-beacon/README.md` + `firmware-beacon/lora_beacon_spec.md`
  — LoRa reference beacon firmware.

---

## §5 Outside scope of this index

- **`theory/`** — separately-tracked study program; reachable via
  `theory/README.md`. Not product documentation.
- **`docs/deprecated/`** — retired pre-pivot docs (`WORKSTREAMS.md`,
  full `INHERITED_CONTEXT.md`, `SALVAGE_AUDIT.md`, sprint logs,
  retired tickets, Phase C tutorials, pre-pivot demo deck). Reference
  only; not binding.
- **`docs/archive/`** — historical workstream-to-lead status files
  from the pre-lead-Opus model.
- **Anything under `recordings/`** — operator-personal IQ captures
  (gitignored).
- **Anything under `.claude/`** — agent-runtime state (gitignored).

---

## §6 How to update this index

When you add a binding section to a doc, add a one-line pointer here.
When you deprecate a doc, move the pointer to a "deprecated" annotation
or remove it.

This file is itself indexed by `README.md` (its `Documents — read in
this order` section) so an agent that lands on README first will find
its way here next.
