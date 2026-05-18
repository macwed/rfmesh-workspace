# ADR-012 — `schema_version` field annotation tightened to `Literal[SCHEMA_VERSION]`

**Status:** ACCEPTED
**Date:** 2026-05-18
**Author:** lead-Opus (responding to architect council finding F1, 2026-05-18 project audit)
**SCHEMA_VERSION change:** **none** — runtime behaviour unchanged.

## Context

Every wire-format message and config model in `packages/rfmesh-contracts/src/rfmesh_contracts/` carries a `schema_version` field. Both `version.py:5` and `messages.py:17-25` document that this field is pinned at type-check time:

> Each message pins `schema_version` as `Literal[SCHEMA_VERSION]`. If a workstream accidentally builds against a stale contract, mypy fails the build before a single byte crosses a wire.

The architect council audit on `826d2c2` (2026-05-18) discovered that **this claim was false on the wire**. The actual annotations at:

- `messages.py:79` (`BearingReport`)
- `messages.py:197` (`FixEvent`)
- `messages.py:317` (`NodeStatus`)
- `config.py:294` (`NodeConfig`)
- `config.py:418` (`FusionConfig`)

were all `schema_version: str = Field(default=SCHEMA_VERSION, ...)`, not `Literal[SCHEMA_VERSION]`. The mypy tripwire the governance documentation promised did not exist. A stale producer would only have been caught at runtime via Pydantic validation, and only if it set the field explicitly (defaults papered the discrepancy over silently).

This is the single most consequential drift the audit surfaced inside `rfmesh-contracts`.

## Decision

Tighten the annotation on all five field declarations to `SchemaVersionT`, a `Literal` type alias defined in `version.py`. PEP 586 forbids variables inside `Literal[...]` — only literal values are allowed — so `Literal[SCHEMA_VERSION]` (which the governance docstring promised) was rejected by mypy with `Parameter 1 of Literal[...] is invalid`. The workaround is a hardcoded literal type alias at one place:

```python
# In version.py:
SCHEMA_VERSION: Final = "1.1.0"
type SchemaVersionT = Literal["1.1.0"]   # one place to update on bump

# In every message/config:
schema_version: SchemaVersionT = Field(default=SCHEMA_VERSION, ...)
```

A SCHEMA_VERSION bump now requires updating **both** `SCHEMA_VERSION` and the literal string inside `SchemaVersionT` in `version.py`. The two-line lockstep is intentional — desynchronising them produces a workspace-wide mypy failure that the lead-Opus cannot miss.

## Consequences

- **mypy gets stricter**: any future code that constructs `BearingReport(schema_version="1.0.0", ...)` against `SCHEMA_VERSION = "1.1.0"` is a static-type error in the producing workstream. This is the tripwire the governance documentation has been promising since v1.0.0.
- **Runtime behaviour identical**: Pydantic's `Literal` validation accepts the single allowed value, the same value the previous `str + default` setup accepted by default. No correctly-constructed message changes shape.
- **No SCHEMA_VERSION bump required**. This is a **PATCH-level documentation-vs-code correction**: the docs already promised this annotation; the code now matches. No field added, no field removed, no value changed. Per `version.py` governance:
  > PATCH — documentation/semantics clarification only; no field changes.
- **Tests unchanged**. Existing contract round-trip tests already pass `schema_version=SCHEMA_VERSION` explicitly or rely on the default. The 5 field-test files (`packages/rfmesh-*/tests/test_*.py` that touch `schema_version`) require no modification.

## Why this is not a contract change requiring a bump

A contract change is a change that **producers and consumers built against the previous version cannot serialize/deserialize compatibly with the new version**. Here:

- Producers on the *old* `str + default` form, omitting `schema_version` from the kwarg list, still produce messages with `schema_version = "1.1.0"`. New consumers on the `Literal` form validate those identically.
- Producers on the *old* form that explicitly set `schema_version="1.1.0"` produce identical wire bytes.
- The only producers that *break* are those that explicitly set `schema_version` to a non-`SCHEMA_VERSION` value — which is exactly the bug class the `Literal` annotation exists to prevent.

The change is **strictly stricter type-checking of an invariant that was already documented as binding**. No semantic deviation from the v1.1.0 contract.

## Verification

Post-change:

```bash
uv run mypy packages/rfmesh-contracts  # green
uv run mypy packages/                   # green workspace-wide
uv run ruff check                       # clean
uv run pytest -m "not hardware"         # 480 tests still pass
```

## Tradeoffs considered

- **Bump to 1.2.0 instead.** Rejected: no semantic change, no new field, no removed field, no behaviour change. A bump would force every workstream's `mypy` to reconfirm, with no producer/consumer adjustment work to actually do — pure ceremony.
- **Document the docstring instead of tightening the code.** Rejected: the documentation has been right all along; the code was wrong. Aligning code to docs is always the right direction.
- **Leave it alone.** Rejected: the council surfaced this as the single highest-leverage architectural fix. Leaving a documented governance mechanism broken is a credibility hazard.

## References

- Council audit (2026-05-18), architect finding F1.
- `version.py:5-37` governance text.
- `messages.py:17-25` module docstring.
- `AGENTS.md` §1 Invariant B1 (contracts frozen — this ADR is the lead authorisation).
