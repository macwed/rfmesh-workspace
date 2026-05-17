# /uvadd-request — WS-CD CoT package — 2026-05-17

## Package
`pytak >= 6.0` (PyPI), MIT license, ~3 MB wheel.

## Classification
Runtime dependency of `packages/rfmesh-cot/` only.

## Rationale

1. Architect ratified `docs/design/ops-architecture.md` §3.2 and §2.1
   binds `rfmesh-cot` to PyTAK. ADR-011 ratifies the dependency.
2. PyTAK is the canonical CoT/ATAK Python library used by the TAK
   community (FreeTAKServer, ATAK-CIV, MIL plugin pipelines). Real
   provenance — credible to a Belgian Defence jury.
3. Async-native — fits `rfmesh-node`'s asyncio runtime.
4. Handles CoT XML, transport (TCP/UDP/TLS), and stale-time
   semantics. Re-implementing CoT framing would be busywork and
   bug-prone for v1.0.
5. Alternatives considered and rejected: `takproto` (no transport
   layer, too thin), `cot-utils` (pure-encoding only, too thin),
   rolling our own (silly under time pressure).

## Invariants checked

- B1 (contracts frozen): not touched.
- B2 (inter-workstream via contracts only): pytak is an external dep
  of `rfmesh-cot` only; rest of workspace does not pull it
  transitively (wheel install respects per-package deps).
- B3 (no silent fallbacks): `CotTransportError` raised on PyTAK
  transmit failure, never swallowed.
- B4 (golden-file tests for DSP): N/A (not DSP).
- B5 (`rfmesh-dsp` / `rfmesh-fusion` pure): N/A; `rfmesh-cot` is
  *not* required to be pure (it does network I/O by definition).

## License audit
MIT — compatible.

## Pin
`pytak >= 6, < 7` (PyTAK is on stable v6; major bump would be
breaking. Cap at < 7 to surface drift via the lockfile.)

## Action

Lead-Opus delegate (this agent, per ADR-011 relaxed model) runs:

```
uv add --package rfmesh-cot "pytak>=6,<7"
```

The block-forbidden-commands hook permits `uv add` from the lead's
context (per ADR-011 + commit 8a75026).
