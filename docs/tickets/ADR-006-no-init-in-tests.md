# ADR-006 — No `__init__.py` under `tests/` directories (workspace convention)

**Status:** ACCEPTED
**Date:** 2026-05-15
**Decider:** Lead architect
**Raised by:** Opus-B during WS-B-001 review, anticipating a duplicate-module
mypy conflict with `packages/rfmesh-sdr/tests/__init__.py`.

## Context

Two workstreams (rfmesh-sdr, rfmesh-dsp) each have their own `tests/` tree
under `packages/<pkg>/tests/`. When a `tests/__init__.py` is present, the
directory becomes an importable Python package — and uvworkspace-wide tools
(mypy `--namespace-packages` off, pytest's rootdir collection) see two
packages both named `tests`, which produces "Duplicate module named 'tests'"
errors at type-check and collection-conflict warnings at test time.

Two ways to resolve:
- **Option A (chosen):** workspace-wide convention to omit `tests/__init__.py`.
  pytest discovers by filename; tests do not need to be importable as
  modules.
- **Option B (rejected):** enable `namespace_packages = true` in mypy config
  and reconfigure pytest accordingly.

## Decision

Adopt Option A. No `__init__.py` files under any `tests/` directory in
the workspace. Workstreams may use `tests/conftest.py` for shared fixtures;
that file is not a package marker and does not trigger the same conflict.

## Consequences

- Tests are discovered by pytest using its standard rootdir mechanism;
  test files import production code from the installed (editable) package,
  not via relative imports from `tests/`.
- mypy runs cleanly across all workstreams' tests without
  `namespace_packages` (which we did not want for production code anyway).
- WS-A removes the existing `packages/rfmesh-sdr/tests/__init__.py` in the
  next maintenance pass (no separate ticket; side-cleanup in next open PR
  with a commit message referencing this ADR).
- Convention added to `AGENTS.md` §3.5.
- Industry standard. The prior repo (`github.com/macwed/rf-mesh`)
  followed the same convention; this restores it. See pytest docs:
  https://docs.pytest.org/en/stable/explanation/goodpractices.html

## Notes

This is a hygiene decision, not an architectural one. It does not affect
`SCHEMA_VERSION` and does not require a contract bump. Future workstreams
inherit the convention via `AGENTS.md`.
