# TICKET WS-CD-001b: mypy `namespace_packages` config + tests/__init__.py + DRY type-alias refactor

## Goal (one sentence)
Resolve the mypy duplicate-`tests`-module collision at the monorepo level
(rather than per-package by-avoidance), so test modules can use
`from .conftest import MakePosition` cleanly, and migrate the existing
`packages/rfmesh-fusion/tests/test_projection.py` to use that import
(also fixing one factual docstring nit that surfaced during QC).

## Context (links only, not content)
- Workstream coordination decision: this conversation's last
  `ask_user_input_v0` chose Option 1
  (`explicit_package_bases = true` + `namespace_packages = true`).
- Files produced by WS-CD-001 (now in the worktree): `projection.py`,
  `__init__.py`, `conftest.py`, `test_projection.py`. All four pass
  pytest/mypy/ruff under the *current* config, which works around the
  collision by NOT having `tests/__init__.py`.
- Architecture references: `AGENTS.md` §6 (CI gate: full-monorepo
  `mypy`, `ruff`, `pytest` must remain clean).
- Module plan: `packages/rfmesh-fusion/docs/MODULE_PLAN.md` §5 — sprint
  1 will add ~8 test modules; the type-alias duplication scales with
  that count if not solved now.

## Acceptance criteria

1. `uv run mypy` (run from monorepo root, on the full workspace) is
   clean. The same command was previously clean because no test
   package had `tests/__init__.py`; after this ticket it must remain
   clean *with* a `tests/__init__.py` present in
   `packages/rfmesh-fusion/tests/`.
2. `uv run pytest` (full monorepo) is clean. No test regressions.
3. `uv run ruff check` is clean.
4. The mypy configuration file (root `pyproject.toml` `[tool.mypy]` if
   it exists there, else `mypy.ini` at root, else the per-package
   config the project already uses — agent picks whichever is the
   *active* mypy config) contains both:
   ```toml
   explicit_package_bases = true
   namespace_packages = true
   ```
   If the active config already sets one or both, the diff sets the
   missing flag and leaves the other untouched. Do not invent a new
   config file if one already exists.
5. `packages/rfmesh-fusion/tests/__init__.py` exists. It is empty (or
   contains only a docstring explaining that it marks the directory as
   a package for mypy module resolution; one-line is fine).
6. `packages/rfmesh-fusion/tests/conftest.py` no longer carries the
   stale workaround-explanation paragraph (the "two `__init__.py`
   files at the same module path fail mypy's default duplicate-module
   check" paragraph). The fixtures themselves stay byte-identical.
7. `packages/rfmesh-fusion/tests/test_projection.py` imports
   `MakePosition` from `.conftest` (`from .conftest import MakePosition`)
   *instead of* redeclaring it locally. The local `MakePosition =
   Callable[..., GeodeticPosition]` alias and its surrounding comment
   are removed. The `Callable` import is removed from this test module
   if it has no other use.
8. The docstring of `test_roundtrip_within_1m_at_10km` is corrected:
   the current text "The grid corners sit ~14 km from the origin
   (sqrt(10^2 + 10^2))" is wrong at lat 52° (0.09° lon ≈ 6.15 km, not
   10 km, because of the `cos(lat)` factor). Replace with an accurate
   statement, e.g.: "The grid spans ±0.09° in lat/lon. At the 52° N
   origin this is roughly ±10 km north–south and ±6.15 km east–west;
   the worst-case corner sits ~11.7 km from the origin — comfortably
   inside the 50 km flat-Earth valid domain."

## Out of scope (explicit non-goals)

- Do NOT add new tests or change any test assertion.
- Do NOT touch `projection.py` source — purely a tests + config diff.
- Do NOT add `tests/__init__.py` to other packages (`rfmesh-sdr`,
  `rfmesh-contracts`, etc.) in this ticket. The mypy config change is
  monorepo-wide and enables them; their individual migrations are
  separate (and other workstreams' decision).
- Do NOT touch any contract type.

## Files you may touch

- Active mypy config file (root `pyproject.toml` or `mypy.ini` —
  whichever holds `[tool.mypy]` today; modify in place)
- `packages/rfmesh-fusion/tests/__init__.py` (create)
- `packages/rfmesh-fusion/tests/conftest.py` (modify — remove stale
  workaround docstring paragraph; fixtures unchanged)
- `packages/rfmesh-fusion/tests/test_projection.py` (modify — switch to
  import-from-conftest; fix docstring; remove `Callable` import if
  unused)

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (FROZEN — Invariant 1)
- `packages/rfmesh-fusion/src/**` (the source was certified by
  WS-CD-001 QC; do not regress its assertions)
- Anything in other workstream packages (`rfmesh-sdr`,
  `rfmesh-node`, `rfmesh-cot`, `rfmesh-ops`) — Invariant 2

## Implementation notes (non-binding, for guidance)

- The two mypy flags together tell mypy:
  - `namespace_packages = true` — accept PEP 420 namespace packages
    (directories without `__init__.py` count as packages).
  - `explicit_package_bases = true` — module names are computed from
    the explicit package roots (the per-package `src/` and `tests/`
    directories), not by walking up the filesystem until a directory
    without `__init__.py`. This is what stops mypy from collapsing
    every `tests/` directory in the monorepo into one `tests` module.
- After the flags land, every package's `tests/` directory becomes its
  own module root, so two packages can both have `tests/__init__.py`
  without colliding. We only add the file for `rfmesh-fusion` in this
  ticket; other packages can add theirs when their workstreams choose
  to.
- `from .conftest import MakePosition` requires `tests/__init__.py` to
  exist (Python's import machinery treats `tests` as a regular package
  only with the marker file). Pytest's fixture discovery is unaffected
  by this — it works either way — but the type-alias import needs the
  marker.

## Stop conditions

- Stop after producing the diff. Do not auto-commit or push.
- Paste the `mypy` / `ruff` / `pytest` output (full monorepo, not just
  this package) into the conversation. If anything regresses outside
  `rfmesh-fusion`, STOP — that is a sibling-workstream effect we need
  to discuss before proceeding.
- If the active mypy config sets `namespace_packages = false`
  *explicitly* (not by default), STOP and write a SCRATCHPAD entry —
  flipping that flag may have been a deliberate decision elsewhere.
- If the `from .conftest import MakePosition` import fails despite the
  config change and the `tests/__init__.py` being in place, STOP and
  report the actual error — do not "fix" it by reverting to the
  per-file type alias.
