# ADR-002 — Contracts as `typing.Protocol`, not `abc.ABC`

**Status:** ACCEPTED (backfilled 2026-05-18)
**Date:** 2026-05-18 — decision originally taken pre-WS-CD-001 (April 2026); backfilled per architect council finding F6 (project audit 2026-05-18).
**Author:** lead-Opus (backfill).
**SCHEMA_VERSION change:** **none** — backfilled documentation of an existing decision.

## Context

The cross-workstream behavioural contracts in `packages/rfmesh-contracts/src/rfmesh_contracts/protocols.py` define what each workstream agrees to deliver and consume — `Receiver`, `CoherentReceiver`, `BearingEstimator`, `Fuser`, `CotPublisher`, `Bearer`. Each of these has multiple implementations in different workstreams (e.g. `Receiver` is satisfied by `SoapyReceiver`, the salvaged `RTLSDRDevice` per WS-A-005, `SyntheticReceiver`).

Two ways to express "this class must look like X" in Python:

1. **`abc.ABC` with `@abstractmethod`.** Each implementer inherits: `class RTLSDRDevice(Receiver): ...`. Mypy / runtime check the abstract method set is satisfied at class-definition time. The implementer carries an explicit inheritance chain.
2. **`typing.Protocol` (structural typing, PEP 544).** Each implementer **does not inherit**; mypy checks shape at *call sites* (when a function takes `r: Receiver`, mypy verifies the passed argument's shape matches). `@runtime_checkable` enables cheap `isinstance(obj, Receiver)` at runtime.

The prior project (`github.com/macwed/rf-mesh`, see `SALVAGE_AUDIT.md` Part 4d) used ABC. The new project deliberately switched to Protocol. `WORKSTREAMS.md` §2 references this ADR by number, but the ADR itself was never authored. This file backfills it.

## Decision

**`typing.Protocol` for every cross-workstream behavioural contract.** All six Protocols in `rfmesh_contracts.protocols` are declared:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class Receiver(Protocol):
    def open(self) -> None: ...
    def configure(self, config: NodeConfig) -> None: ...
    def read(self, n: int) -> IQBlock: ...
    def capabilities(self) -> ReceiverCapabilities: ...
    def close(self) -> None: ...
```

Implementers do **not** inherit from the Protocol. A class is a `Receiver` if its shape matches; the relationship is established at call sites by mypy and at runtime by `isinstance`.

`@runtime_checkable` is applied to every Protocol that needs `isinstance` checks (tests use this; the node runtime's capability-detection code at startup uses it). The cost is a single attribute set on the class object; no performance impact.

## Consequences

**What this enables:**

1. **No inheritance coupling into `rfmesh-contracts`.** The salvaged `RTLSDRDevice` from `github.com/macwed/rf-mesh` had `class RTLSDRDevice(Device)` with `Device` imported from a shared base. Porting to the new repo required either (a) keeping a base class in `rfmesh-contracts` (which makes contracts impure — `pydantic`'s `BaseModel` is the only inheritance we accept there), or (b) switching to Protocol. We chose (b). The implementer no longer imports anything from `rfmesh-contracts` for inheritance — only for type annotations on its public methods.
2. **Mocks and test doubles do not need to inherit.** A test that fakes a `Receiver` writes a plain class with the right methods; no `class FakeReceiver(Receiver):` boilerplate. This kept test files cleaner during WS-CD-007 fuser test development.
3. **Structural typing aligns with the architecture's dependency-star intent.** `ARCHITECTURE.md` §3 binds: every workstream imports only from `rfmesh-contracts` or its own package. Protocol enforces this naturally because it cannot reach into the implementer's class hierarchy; ABC would have created a temptation to share more than just type information through the inheritance chain.
4. **`@runtime_checkable` `isinstance(obj, Receiver)` is cheap** — Python checks `obj` has every Protocol method as an attribute. We use this in `packages/rfmesh-node/src/rfmesh_node/capabilities.py` for hardware-capability detection at startup; the check runs once per node lifecycle, performance is irrelevant.
5. **Mypy still catches shape mismatches at every call site.** Pre-Protocol, an `RTLSDRDevice.configure(self, config)` accidentally renamed to `configure_with` would have been caught at class-definition time (ABC). With Protocol, it's caught at the first call site that passes the (now invalid) `RTLSDRDevice` to a function expecting `Receiver`. The diagnostic is at least as good; just from a different vantage.

**What this costs:**

1. **Less obvious that a class is a Protocol implementer.** A reader of `RTLSDRDevice` cannot immediately tell what contract it satisfies; they have to read the comment / docstring or grep for `Receiver`. Mitigation: every implementer's module docstring states which Protocol(s) it satisfies (see `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/receiver.py` for the convention).
2. **Refactor risk: renaming a Protocol method** (e.g. `read` → `read_block`) requires updating every implementer manually. ABC catches the omission at class-definition time; Protocol catches at first call site. The lag is small in practice (mypy runs on every commit).
3. **`@runtime_checkable` does not check method signatures** — only the presence of attributes with those names. A class with `def read(self): ...` (no args) passes `isinstance(obj, Receiver)` despite not matching `Receiver.read(self, n: int) -> IQBlock`. Mypy catches the signature mismatch at the call site; runtime would not catch it until invocation. Mitigation: integration tests cover the shape; unit tests mock against the Protocol, exercising signatures.

## Tradeoffs considered

**Why Protocol wins for rfmesh specifically:**

- The architecture **forbids cross-workstream inheritance** (`ARCHITECTURE.md` §3's frozen-contract star). ABC creates an implicit "shared base class" channel that, even if disciplined, tempts agents to put helper logic in the base over time. Protocol structurally cannot host such logic — the Protocol body has only `...` method stubs.
- `rfmesh-contracts` is **declarative**, not behavioural. The data part is Pydantic models with `frozen=True, extra="forbid"`. Mixing inheritance-based behavioural contracts with declarative data contracts in the same package creates a "what kind of package is this?" identity problem. Protocol keeps the package pure-declarative.
- **Multiple Protocol implementers per slot** is the norm: `Receiver` has `SoapyReceiver`, `RTLSDRDevice`, `SyntheticReceiver`; `Fuser` will eventually have `StansfieldMLEFuser` (today) and possibly future alternatives. ABC inheritance chains get awkward when implementers have unrelated parents (e.g. `SoapyReceiver(BaseSoapyClient, Receiver)` vs `SyntheticReceiver(BaseGenerator, Receiver)`). Protocol sidesteps multiple-inheritance trade-offs entirely.

**When ABC would have been right:**

- A real "is-a" relationship across implementers (e.g. all `Receiver`s share a substantial implementation, like a base `_check_calibration()` they all call). We have nothing like that. Each `Receiver` is meaningfully different in its lifecycle.
- A need to enforce method-set membership at class-definition time, before any call site exists. Our tests run every commit; call-site checks fire fast enough.
- A foreign-language audience (e.g. publishing a Java-side library that expects Python ABCs to look like Java interfaces). Not our scope.

None of these apply.

## Why this is documented and not just done

The salvage source (`github.com/macwed/rf-mesh`) is ABC-based. Any agent reading the prior repo will see `class Device(ABC)` and reasonably ask **"why did we switch to Protocol?"** The instinct to "fix" the new repo to match the salvage's idiom is real — and would silently break the dependency-star architecture by re-introducing inheritance coupling into `rfmesh-contracts`. This ADR is the canonical answer to that question.

## References

- `WORKSTREAMS.md` §2 — cites ADR-002 by number.
- `ARCHITECTURE.md` §3 — the frozen-contract star that Protocol cleanly serves.
- `packages/rfmesh-contracts/src/rfmesh_contracts/protocols.py` — the six Protocol declarations.
- `INTERFACES.md` §5 — the behavioural-contract semantic dictionary that this ADR's mechanism realises.
- `SALVAGE_AUDIT.md` Part 4d — explicit "Device ABC → Receiver Protocol" refactor note in the salvage inventory.
- `AGENTS.md` §1 Invariant 2 — cross-workstream imports must resolve to `rfmesh_contracts` or the workstream's own package. Protocol structurally upholds this.
