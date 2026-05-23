# TICKET S4: Round-trip tests for `rfmesh-contracts`

## Goal (one sentence)
Add `model_validate(model_dump())` round-trip tests + a `schema_version` mismatch regression test to `packages/rfmesh-contracts/tests/` (currently zero tests) so the contract types — which the entire workspace's correctness depends on — have at least one explicit verification beyond mypy.

## Context (links only, not content)

- Architect council finding F9 (project audit 2026-05-18): `rfmesh-contracts` has zero test files. Defensible (it's all declarative Pydantic), but a single round-trip test per message + a schema_version mismatch test would harden Invariant B1.
- Reading order: `INTERFACES.md` §3 (the three wire messages: `BearingReport`, `FixEvent`, `NodeStatus`) + `INTERFACES.md` §4 (config types: `SDRConfig`, `ArrayConfig`, `BearerConfig`, `NodeConfig`, `FusionConfig`) + `docs/adr/ADR-012-schema-version-literal-type.md` (the tripwire mechanism this ticket exercises).
- Existing test scaffolding to mirror: `packages/rfmesh-fusion/tests/conftest.py` has the `make_position` + `make_bearing` factory fixtures pattern; copy the idiom for contract tests.

## Acceptance criteria

(Executor fills these. Suggested shape:)

1. New file `packages/rfmesh-contracts/tests/test_roundtrip.py` with one parametrised test per wire-format message:
   - `test_bearing_report_roundtrip` — construct a `BearingReport` with all required fields populated, dump to dict, validate the dict back, assert equality.
   - `test_fix_event_roundtrip` — same for `FixEvent`.
   - `test_node_status_roundtrip` — same for `NodeStatus`.
2. New file `packages/rfmesh-contracts/tests/test_config_roundtrip.py` with parametrised tests for `NodeConfig`, `FusionConfig`, `SDRConfig`, `ArrayConfig`, `BearerConfig`.
3. New file `packages/rfmesh-contracts/tests/test_schema_version_tripwire.py`:
   - `test_correct_version_accepted` — `BearingReport(schema_version="1.1.0", ...)` validates.
   - `test_stale_version_rejected` — `BearingReport(schema_version="1.0.0", ...)` raises `ValidationError`.
   - `test_default_matches_module_constant` — `BearingReport(...)` without explicit `schema_version` defaults to `SCHEMA_VERSION` from `version.py`.
4. New file `packages/rfmesh-contracts/tests/test_extra_forbid.py`:
   - For each message and config, attempt to construct with a fabricated extra field, expect `ValidationError` (per `extra="forbid"`).
5. `uv run pytest packages/rfmesh-contracts -v` — at least 15 new tests pass (3 messages × roundtrip + 5 configs × roundtrip + 3 version + 8 extra-forbid ≈ 19 tests).
6. `uv run mypy packages/rfmesh-contracts/tests/` strict clean.
7. `uv run ruff check packages/rfmesh-contracts/tests/` clean.
8. Total workspace tests after this: ≥ 500 (was 481).

## Out of scope (explicit non-goals)

- Do NOT modify `packages/rfmesh-contracts/src/` (Invariant B1 — contracts frozen).
- Do NOT add `__init__.py` to `tests/` (ADR-006).
- Do NOT test wire-format serialisation (msgpack envelope) — that lives in `rfmesh-node`'s test suite. This ticket is for the in-memory model contract.
- Do NOT test cross-field validators (e.g. `BearerConfig._lora_needs_port`) — those have their own tests in the relevant package.
- Do NOT introduce `pytest-asyncio` or any new test infra dep.

## Files you may touch

- `packages/rfmesh-contracts/tests/conftest.py` (create — copy the `make_position` / `make_bearing` idiom from `rfmesh-fusion/tests/conftest.py`)
- `packages/rfmesh-contracts/tests/test_roundtrip.py` (create)
- `packages/rfmesh-contracts/tests/test_config_roundtrip.py` (create)
- `packages/rfmesh-contracts/tests/test_schema_version_tripwire.py` (create)
- `packages/rfmesh-contracts/tests/test_extra_forbid.py` (create)

## Files you may NOT touch

- `packages/rfmesh-contracts/src/**` (FROZEN — Invariant B1)
- Any other package's tests
- Workspace root `pyproject.toml`

## Stop conditions

- Stop after producing the diff. Open PR for council review.
- If you find a real bug in a contract while writing tests (e.g. a field's validator accepts a value the docstring says it shouldn't), STOP and write a SCRATCHPAD entry. Do not fix the contract — that requires an ADR.
- If `extra="forbid"` is missing from any model, that's a defect in the contract; STOP and surface it. (Should not happen; lead-Opus audited this in May 2026 and the council confirmed all 5 models carry `frozen=True, extra="forbid"`.)

## Council gates

- [ ] **architect** (always — this ticket is the audit follow-up F9)
- [ ] **code-reviewer** (always)
- [ ] rf-dsp-specialist (NO — declarative tests, no DSP)
- [ ] demo-integrity (NO — no demo surface touched)

## Suggested owner

Friend (PY hat) — ~2 hr. Excellent second-or-third ticket after WS-A-005/006 because it forces a close read of every contract type, which is the foundation knowledge friend will use everywhere.

## Implementation notes (non-binding)

- The `make_bearing` factory in `rfmesh-fusion/tests/conftest.py` is the cleanest existing example of building a fully-validated `BearingReport`. Copy the keyword-default pattern.
- For `FixEvent` round-trip you need a `confidence_ellipse_95: EllipseENU` value — see `INTERFACES.md` §2 for the field structure. Pick a small, sane example (semi_major_m=100, semi_minor_m=50, orientation_deg=45).
- For the `extra_forbid` tests, the easy way is `BearingReport(**valid_kwargs, extra_field="x")` — Pydantic will raise.
- For the version tripwire test, the failure mode is `pytest.raises(ValidationError)` checking the error message says "schema_version".
- The mypy tripwire from ADR-012 is a separate concern (it fires at type-check time, not runtime). This ticket tests the **runtime** half of the tripwire: Pydantic should also reject mismatched literals at validation time.

## Claude Code execution prompt skeleton

```
TICKET S4: rfmesh-contracts round-trip tests

Read first:
1. INTERFACES.md §3 + §4 (wire messages + configs)
2. docs/adr/ADR-012-schema-version-literal-type.md
3. packages/rfmesh-fusion/tests/conftest.py (copy the factory idiom)

Goal: add 15-20 round-trip + schema-version + extra-forbid tests.

Files to create: packages/rfmesh-contracts/tests/{conftest,test_roundtrip,
test_config_roundtrip,test_schema_version_tripwire,test_extra_forbid}.py
Files NOT to touch: packages/rfmesh-contracts/src/ (Invariant B1).

Acceptance: ≥15 new tests pass. mypy strict + ruff clean. Workspace ≥500.

Council gates: architect + code-reviewer. Paste outputs. Do NOT commit.
```
