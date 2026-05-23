# ADR-024 — NodeController state machine + cancel-drained servo handoff

**Status:** PROPOSED (2026-05-24)
**Date:** 2026-05-24
**Author:** lead-Opus, synthesising the council code-reviewer BLOCK on item 2
of the post-cold-start plan (the rendezvous-vs-manual-steer race) and the
demo-integrity recommendations on the 5-state badge UX.
**SCHEMA_VERSION change:** **NONE.** All new types are node-layer
(`packages/rfmesh-node/`). The frozen contracts stay frozen (B1).
**Depends on:** ADR-019 (rendezvous), ADR-021 (manual-steer surface +
state-machine sketch), ADR-022 (single-YAML config + capability handshake,
introduces the `controller_ready: bool` field this ADR flips to `true`).
**Scope:** `packages/rfmesh-node/{controller.py NEW, node.py MOD,
l1_sweep.py MOD, rendezvous.py MOD, commands.py MOD}`,
`deployment/backend/src/both3_poc/ws.py` (new `POST /node/{id}/clear_fault`
+ `manual_hold_expires_at` in the `node_hello` re-send on mode change),
`deployment/frontend/link.{html, js, css}` (5-state badge legend +
MANUAL_HOLD countdown + sticky-PARKED vs sticky-FAULT distinction).

## Context

ADR-021 §"State machine on the node" sketched a 5-state machine
(`SWEEPING | MANUAL_HOLD | PARKED | ACQUIRED_PEER | FAULT`) living in a new
`NodeController` class above `L1SweepLoop`. The class did not ship with
ADR-021 itself; ADR-022 deferred the servo-action handler to this follow-up
because the council code-reviewer flagged a binding concurrency hazard:

> The single-writer servo invariant races. Today `_rendezvous_supervisor`
> is the sole servo-driving task (`node.py:133-140` comment binds this). A
> `NodeController` "mode switch" that preempts the supervisor while
> `RendezvousLoop._refine_once` is mid-arc will interleave `servo.move`
> calls — the manual-steer command and the in-flight refine sweep become
> two writers. Required before APPROVE: (a) explicit cancellation handoff
> (the supervisor must `await` a cancel-and-drained signal before
> NodeController issues `move_smooth`), not just a mode flag; (b)
> state-machine unit tests proving no overlap under `MANUAL_HOLD`
> preemption mid-refine; (c) angle-validation must raise loudly on
> out-of-`calibrated_geographic_range`, never clamp (B3, same shape as
> `PeerOutOfArcError`); (d) `L1AmplitudeSweepEstimator` state must be
> reset on every mode exit, otherwise a half-completed refine that gets
> preempted and then resumed could emit a `BearingReport` with stale
> accumulator.

Demo-integrity additionally flagged three UX gaps the state machine has to
honour: a full 5-state badge legend on `link.html`, a visible MANUAL_HOLD
countdown to auto-resume, and a clear ALL-STOP-vs-FAULT distinction
(ADR-021 left ambiguous).

The servo wire protocol (`docs/wire-protocols/servo_uart_v1.md`) is
single-outstanding-command master/slave; this ADR's binding job is to
preserve that invariant in software.

## Decision

### 1. NodeController owns the servo and the mode

A new class `NodeController` (in `packages/rfmesh-node/src/rfmesh_node/
controller.py`) takes ownership of the servo-driving task. `Node.run`
schedules **one** controller task that internally arbitrates between modes;
the existing `_rendezvous_supervisor` is folded into the controller as one
of its driving loops. There is therefore exactly one task that may call
`servo.move()` at any instant — the single-writer invariant becomes a
class invariant, not a comment.

`L1SweepLoop` and `RendezvousLoop` are kept as **owned helpers**:
- `L1SweepLoop.run_one_sweep(stopping, cancel)` (renamed from
  `run_once`) emits a `BearingReport | None` and respects two events:
  the existing `stopping` (process shutdown) and a new `cancel`
  (mode-handoff request, see below).
- `RendezvousLoop.acquire(stopping, cancel)` and
  `RendezvousLoop.hold(stopping, cancel, duration_s)` gain the same
  `cancel` event parameter.

Loops are NOT scheduled as their own asyncio tasks. The controller calls
them directly, sequentially, in its own task.

### 2. Cancel-drained handshake (binding)

The controller owns two `asyncio.Event` instances per mode handoff:

- `mode_cancel: asyncio.Event` — set by the controller when a new mode is
  requested.
- `mode_drained: asyncio.Event` — set by the currently-running loop on
  the next safe checkpoint after observing `mode_cancel`.

Mode-switch protocol:

```python
DRAIN_TIMEOUT_S: float = 2.0  # max(single servo-move budget * 2, ~2s)

async def switch_mode(self, new_mode: NodeState) -> None:
    self._mode_cancel.set()
    try:
        await asyncio.wait_for(self._mode_drained.wait(), timeout=DRAIN_TIMEOUT_S)
    except TimeoutError:
        # B3: drain failure is loud, not silent. A wedged loop (bug in a
        # future ``finally:``, a ``to_thread`` hung on serial I/O after a
        # USB-CDC disconnect, a ``_refine_once`` fork that forgot to wrap
        # drain) must surface as FAULT with a specific status_detail
        # rather than silently leave the controller wedged with the
        # operator's slider unresponsive.
        self._state = NodeState.FAULT
        self._status_detail = f"mode_drain_timeout: prior_mode={self._state.value}"
        return
    self._mode_cancel.clear()
    self._mode_drained.clear()
    self._estimator_for_mode_exit_reset()        # B3: stale-accumulator guard
    self._state = new_mode
```

`DRAIN_TIMEOUT_S` is sized as max(single servo-move worst-case budget × 2,
~2 s). A single calibrated MG996R move takes < 0.2 s for the small steps
the loops use (and < 0.5 s including settle); 2 s gives ~4× headroom
without making the operator wait visibly. The constant is module-level so
a future hardware change can re-tune it in one place.

The currently-running loop checks `mode_cancel` at each safe checkpoint
(defined below) and, on observing it, returns from the loop function. The
controller's outer await on `mode_drained` is what guarantees no overlap;
the controller does not issue a new `servo.move()` until the prior loop
has acknowledged drain (or the drain timeout fires, transitioning to
FAULT loudly).

Safe checkpoints (the only places `mode_cancel` is consulted):

- Between any two `await asyncio.to_thread(self._servo.move, ...)` calls
  in `L1SweepLoop._one_sweep` and `RendezvousLoop._refine_once`.
- At the top of `RendezvousLoop.acquire`'s escalation-ladder loop and at
  the top of each `_refine_once` iteration.
- At the start of `RendezvousLoop.hold`'s wait (so a manual_steer arriving
  during a 30s link-hold preempts immediately).
- Inside `L1SweepLoop.run_one_sweep`'s arc iteration.

**Binding: the safe checkpoint is BETWEEN two `servo.move` calls, never
DURING one.** An `await asyncio.to_thread(self._servo.move, ...)` that has
already begun execution in the threadpool cannot be cancelled — the
underlying USB-CDC write completes after the asyncio cancel. A future
contributor must NOT place a `mode_cancel` consultation inside a
`to_thread`-wrapped call site; the cooperative cancel mechanism only
works at the boundary between two such calls. Any PR that does so is a
review block.

The loop function must set `mode_drained` in its `finally:` block so a
raise still drains. Test `test_mode_cancel_during_refine_no_overlap` is
the binding regression: a fake servo records every call; the test
preempts mid-refine, then issues a manual-steer; the recorded sequence
must show no `servo.move` between the cancel-detect timestamp and the
controller's first move under the new mode. A complementary test
`test_drain_timeout_promotes_to_fault` simulates a loop that ignores
`mode_cancel` and asserts the controller transitions to FAULT with the
expected `status_detail` rather than wedging.

### 3. Estimator reset on mode exit (binding)

Every mode exit resets the `L1AmplitudeSweepEstimator` accumulator owned
by the exiting loop:

- `L1SweepLoop._estimator.begin_sweep(t_unix_ns)` is called both at
  sweep start (existing) AND in the mode-exit drain path (new).
- `RendezvousLoop._estimator.begin_sweep(...)` likewise.

The mechanism is a `reset()` method on each loop that wraps the
estimator's `begin_sweep` call; the controller calls it after
`mode_drained` is observed and before the next mode begins. A half-completed
sweep that gets preempted thus drops its accumulator and never produces a
`BearingReport` from stale data.

### 4. Angle validation — loud refusal, never clamp (binding)

A new exception class `ManualSteerOutOfArcError(ValueError)` mirroring
`PeerOutOfArcError` (B6 — same shape across the two preconditions).
`NodeController.dispatch_command(ManualSteerCommand)` validates
`command.target_angle_deg` against the cached `calibrated_geographic_arc_deg`
(populated from the same source as ADR-022's `node_hello`); out-of-arc
raises before any state transition, the exception is caught at the
`_handle_command` boundary, and a `command_refused` frame is acked back
to the UI with the reason (`"requested 285°, calibrated arc is 30-270°"`).

No silent clamp. No partial move. B3.

### 5. The five states + transitions

```text
                    +-----------+
   boot -----+---->|  SWEEPING  |<----+
             |     +-----------+      |
             |       |    ^           | timeout(timeout_s)
             |       |    |           |
             |       |    +----------+|
             |       |               ||
             |       v               ||
             |    +-------------+    ||
             |    | ACQUIRED_   |    ||
             |    | PEER        |---+|+---+ manual_steer
             |    +-------------+   |     |
             |       |              v     v
             |       |          +--------------+
             |       |          | MANUAL_HOLD  |
             |       |          +--------------+
             |       |              |
             |       |              v all_stop
             |       |          +---------+
             |       +--------> | PARKED  | <---+ all_stop (re-issuable)
             |                  +---------+     |
             |  bad_calibration   |             |
             |       |            | manual_steer|
             |       v            +-------------+
             |   +--------+
             +-->| FAULT  |
                 +--------+
                  needs operator clear_fault
```

Transitions:

| From          | Event                       | To            | Notes |
|---------------|-----------------------------|---------------|-------|
| any           | manual_steer (in arc)       | MANUAL_HOLD   | start countdown |
| any           | manual_steer (out of arc)   | unchanged     | refuse, command_refused |
| MANUAL_HOLD   | timeout(timeout_s) expired  | SWEEPING      | auto-resume |
| any           | all_stop                    | PARKED        | sticky; re-issuable |
| PARKED        | manual_steer                | MANUAL_HOLD   | operator-recoverable |
| SWEEPING      | rendezvous lock             | ACQUIRED_PEER | only if rendezvous enabled |
| ACQUIRED_PEER | rendezvous break (per ADR-019) | SWEEPING   | jammer-sweep break |
| any           | uncalibrated / HW refusal   | FAULT         | sticky |
| FAULT         | POST /node/{id}/clear_fault | SWEEPING      | operator ack required |

### 6. ALL-STOP vs FAULT (demo-integrity rec)

- **`PARKED`** is the ALL-STOP destination. Sticky in the sense that
  the controller stops driving the servo until a new command arrives;
  operator-recoverable by issuing any `manual_steer` (clean transition
  to MANUAL_HOLD) or by a "resume" command (transition to SWEEPING).
- **`FAULT`** is reserved for unrecoverable-by-software conditions:
  uncalibrated servo at boot, hardware refusal (`ERR_RANGE` despite
  software arc-validation, `ERR_TIMEOUT`, USB-CDC disconnect). FAULT
  blocks ALL further commands; operator must POST
  `/node/{id}/clear_fault` after fixing the underlying condition.

`link.html` renders the two distinctly: PARKED is yellow + "press any
steer to resume", FAULT is red + the `status_detail` string and a "needs
ops attention" badge.

### 7. MANUAL_HOLD countdown (demo-integrity rec)

`ManualSteerCommand.timeout_s` already exists (default 10 s, range 0.5-600s
per ADR-018). When the controller enters MANUAL_HOLD it records
`manual_hold_expires_at = time.time_ns() + int(timeout_s * 1e9)` and
includes that field on every `NodeStatus` heartbeat published over the
existing dashboard pubsub. `link.js`'s already-running 1 s `setInterval`
ticks the badge label: `"MANUAL · 7s"` → `"6s"` → ... → SWEEPING. Re-issuing
`manual_steer` resets the countdown.

### 8. Backend surface additions

- New route `POST /node/{node_id}/clear_fault` — operator acknowledges a
  FAULT. Backend forwards a `clear_fault` command frame; the node
  controller transitions FAULT → SWEEPING (or refuses if the underlying
  condition is still present, in which case the node re-emits the FAULT
  loud-refusal).
- `manual_hold_expires_at: int | null` (nanoseconds Unix epoch) is added
  to the `node_hello` re-send the controller issues on **every** mode
  transition (not just at WS reconnect), so the UI tracks the countdown
  without polling.

The `node_hello` frame from ADR-022 is upgraded to be re-sendable per
mode change; it is no longer a once-per-connect handshake but a "state
snapshot at any time" message. Backwards-compatible: the backend already
overwrites the cached snapshot on every receive.

### 9. `Node.controller_ready` flips to `true`

ADR-022 shipped `controller_ready: false` in the `node_hello` because
the stub handler refused everything. This ADR flips it to `true` when a
`NodeController` is wired (which is iff `command_endpoint.enabled=true`
in the YAML and a servo is connected). `link.js` already gates the
slider on `controller_ready`; flipping the flag activates the slider for
the first time since ADR-021.

### 10. Backwards-compatibility window

`Node.__init__` continues to accept `sweep_loop` + `rendezvous_loop`
kwargs for one release (BartekDu's commit `6ca3c43` field-deploy path
constructs Node directly). When both are passed AND a NodeController
would be built, the controller wins; the standalone loops are no longer
scheduled. A `DeprecationWarning` is logged. After one release the
kwargs become controller-internal.

## Consequences

### Positive

- Single-writer servo invariant becomes a class invariant rather than a
  comment ("//exactly one writer touches the servo at any instant" is now
  enforced by `NodeController` owning the only task that calls
  `servo.move`).
- Manual steering becomes possible without the race the code-reviewer
  flagged. The slider in `link.html` activates (controller_ready=true).
- ALL-STOP and FAULT are visually + behaviourally distinct; operators
  understand which one needs ops attention.
- The MANUAL_HOLD countdown removes the "did the operator's command
  apply?" cognitive load — they see "MANUAL · 7s" or it's not active.
- B3 honestly served: every refusal path is loud and acked.

### Negative

- ~600 LOC across 6 files (controller.py new, node.py mod, two loop mods,
  ws.py mod, link.js mod) + tests (~300 LOC).
- One release of deprecated-kwarg handling in `Node.__init__`.
- A new `clear_fault` backend route increases the command surface; tests
  must cover unauthenticated abuse (FAULT clears should be operator-
  initiated only, but v1 has no auth — same posture as `manual_steer`).
- Backend re-sends of `node_hello` on every mode transition produce more
  WS traffic; magnitude is small (a few hundred bytes per transition,
  transitions are seconds apart), but it's a new write pattern.

### Risks

- The cancel-drained handshake correctness depends on EVERY safe-checkpoint
  consultation. A future contributor adding a `servo.move()` outside a
  checkpoint silently breaks the invariant. Mitigation: add a docstring
  reminder + a lint-style test that grep-asserts every `_servo.move`
  call site sits inside a `_mode_cancel`-checked region. Council
  code-reviewer's eye on every PR touching the loops.
- The FAULT state is sticky by design — a node stuck in FAULT requires a
  human round-trip (operator opens link.html, sees FAULT badge, POSTs
  clear_fault). Mitigation: link.html surfaces the
  `status_detail` reason prominently; the "needs ops attention" badge is
  unmissable.
- Estimator reset on mode exit drops a half-completed sweep's data. This
  is correct (better than emitting stale data) but means a node thrashing
  between modes produces no bearings. Mitigation: the existing
  `last_refusal_reason` populates `"mode_change_drained"` so the operator
  sees why bearings stopped.

## Alternatives considered

- **`asyncio.Lock` around the servo** instead of cancel-drained events.
  Rejected: the mode handoff needs to *preempt* an in-flight operation,
  not wait for it to finish. A lock would queue the manual-steer behind
  the in-flight 91-angle sweep (~ 20 s wait); the soldier sees the slider
  not respond. Cancel-drained gives ~1-step latency (the time between
  two `servo.move` calls, ~200 ms).
- **`asyncio.Task.cancel()` on the mode task.** Rejected: a cancelled
  task may have an in-flight `await asyncio.to_thread(self._servo.move,
  ...)` whose underlying serial write completes after cancellation,
  violating the single-writer invariant at the wire level. Cooperative
  cancel-drained gives the loop the chance to finish its current
  transport call before yielding.
- **Centralised `ServoBroker` queue with priority levels.** Rejected:
  adds an abstraction layer for no benefit; the controller already has
  the priority information (mode is the priority).
- **Skip the state machine for v1; ship a "manual steer overrides
  sweep" toggle.** Rejected: doesn't address the demo-integrity UX gaps
  (no MANUAL_HOLD countdown, no ALL-STOP vs FAULT) and the toggle still
  has the same race.

## References

- ADR-019 (antenna rendezvous) — RendezvousLoop, servo lifecycle hoist
  to `Node`.
- ADR-021 §"State machine on the node" — original 5-state sketch.
- ADR-022 §"CommandChannel wired into Node" — the stub handler this ADR
  replaces; `controller_ready: false` flips to `true` here.
- `docs/wire-protocols/servo_uart_v1.md` — single-outstanding-command
  master/slave (the invariant the cancel-drained handshake protects in
  software).
- Council code-reviewer BLOCK 2026-05-23 on item 2 of the post-cold-start
  plan — the four binding requirements (a) cancel-drained, (b)
  state-machine no-overlap tests, (c) angle B3, (d) estimator reset.
- Demo-integrity recommendation 2026-05-23 on item 2 — 5-state badge
  legend, MANUAL_HOLD countdown, ALL-STOP vs FAULT distinction.
- `INHERITED_CONTEXT.md` §3.1.1 — servo backlash discipline (preserved
  in `L1SweepLoop._one_sweep` same-side approach; unchanged by this ADR).
