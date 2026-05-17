# Ticket skeletons — collaborator-ready stubs

Each file here is a **partially-filled ticket** in the format defined at `docs/tickets/TICKET-TEMPLATE.md`. The Goal, Context, Files-may-touch, Files-may-NOT-touch, Out-of-scope, Stop-conditions, and Council-gates sections are pre-authored. The **Acceptance Criteria** are either pre-filled (use as-is) or left as suggested shape (executor refines after reading the cited binding docs).

A skeleton is **ready to hand to Claude Code** via the prompt block at the bottom of each file.

## Available skeletons

### Hardware path (Tier F — P0)

| File | Suggested owner | Effort | Skill |
|---|---|---|---|
| `WS-A-005-rtlsdr-device-port.md` | Friend | ½-1 day | Python systems |
| `WS-A-006-servo-host-driver-port.md` | Friend | 1 day | Python systems + light EMB |
| `WS-A-007-firmware-esp32-s2-port-and-lora-beacon.md` | Lead-Opus (source) + Maciej (flash) | ½-1 day | ESP-IDF C |

### Demo polish (Tier E — P0)

| File | Suggested owner | Effort | Skill |
|---|---|---|---|
| `E2-adr-001-002-003-backfill.md` | Friend (PM hat) | 2 hr | Project mgmt + docs |

### Strategic ideas (Tier G — P1-P2)

| File | Suggested owner | Effort | Skill |
|---|---|---|---|
| `G8-find-reference-cli.md` | Friend (PY hat) | ½ day | Python + HTML scrape |

### Infrastructure (Tier S — P1-P2)

| File | Suggested owner | Effort | Skill |
|---|---|---|---|
| `S4-contracts-roundtrip-tests.md` | Friend (PY hat) | 2 hr | Python + Pydantic |

## Pickup order for Friend

1. **`WS-A-005-rtlsdr-device-port.md`** — the starter ticket. Salvage shape, well-scoped, sets the council-review pattern for everything that follows.
2. **`WS-A-006-servo-host-driver-port.md`** — same shape as WS-A-005, larger surface, exercises the `Transport` Protocol idiom. Hands rfmesh-servo from stub to real.
3. After WS-A-006 merges, pick **one** of:
   - `E2-adr-001-002-003-backfill.md` — 2 hr PM-hat warm-down; foundational documentation that's been missing.
   - `S4-contracts-roundtrip-tests.md` — 2 hr PY-hat; deepens your knowledge of every contract type.
   - `G8-find-reference-cli.md` — ½ day; independent operator tooling; useful for jury Q&A but not critical path.

## Pickup order for Lead-Opus

Lead-Opus consumes the `BACKLOG.md` directly — skeletons exist primarily for Friend. Lead-Opus may use them as ticket-shape references when authoring new ones.

## Authoring conventions

When adding a new skeleton:

1. Copy `docs/tickets/TICKET-TEMPLATE.md` to `docs/tickets/skeletons/<ID>-<short-name>.md`.
2. Fill every section except possibly Acceptance Criteria.
3. Add an entry to this README.
4. Add a Backlog row to `docs/BACKLOG.md` if it's not already there.
5. Open a docs-only PR (`docs/skeletons-<id>` branch); council review is minimal for skeleton-add (architect quick scan only).

## Closed skeletons

When a skeleton's PR merges, **leave the skeleton file in place** as historical reference + future audit material. Add a `Status: CLOSED in <commit-id>` line at the top of the file. Skeletons are append-only after merge.
