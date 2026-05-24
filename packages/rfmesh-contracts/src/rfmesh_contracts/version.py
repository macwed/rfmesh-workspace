"""Schema version — the single source of truth for contract compatibility.

This module exists so that every inter-package message carries a version that
can be checked *at type-check time*, not just at runtime. Each message model in
``messages.py`` pins ``schema_version`` as ``Literal[SCHEMA_VERSION]``. If a
workstream accidentally builds against a stale contract, mypy fails the build
before a single byte crosses a wire.

GOVERNANCE
----------
``SCHEMA_VERSION`` is bumped *only by the lead architect*, and only as the final
step of an accepted Architecture Decision Record (ADR) that changes a contract.
No workstream agent may edit this file. See ``AGENTS.md`` -> "The Five
Invariants" and ``docs/adr/`` for the change-control process.

SEMANTICS OF A BUMP
-------------------
The version is ``MAJOR.MINOR.PATCH``:

* PATCH  -- documentation/semantics clarification only; no field changes.
* MINOR  -- additive, backward-compatible change (a new *optional* field, a new
  enum member that existing consumers can treat as "unknown"). Producers on
  ``N.M.x`` can still be read by consumers on ``N.(M-k).x``.
* MAJOR  -- breaking change (renamed/removed field, changed field type, changed
  units, a required field added). All workstreams must re-bootstrap and adapt
  in lockstep.

When this value changes, every ``Literal[SCHEMA_VERSION]`` annotation in
``messages.py`` and ``config.py`` updates with it, which makes every message
class a different type than before -- exactly the intended tripwire.
"""

from __future__ import annotations

from typing import Final, Literal

#: The frozen contract version. Bumped only by the lead, only via an accepted ADR.
SCHEMA_VERSION: Final = "1.4.0"

#: Type alias for ``Literal[SCHEMA_VERSION]``. Used in every message/config
#: model's ``schema_version`` field so mypy can enforce the tripwire.
#:
#: WHY THIS EXISTS (and not just ``Literal[SCHEMA_VERSION]`` inline):
#: PEP 586 forbids variables inside ``Literal[...]`` — only literal values
#: are accepted. So ``Literal[SCHEMA_VERSION]`` raises mypy
#: ``Parameter 1 of Literal[...] is invalid``. The fix per ADR-012 is to
#: hardcode the literal at one place (here) and reference it as a type
#: alias everywhere else. Bumping the version requires updating *both*
#: ``SCHEMA_VERSION`` and ``SchemaVersionT`` in lockstep — caught by
#: workspace mypy if the two ever desynchronise.
type SchemaVersionT = Literal["1.4.0"]
