# TICKET WS-A-003: Channel and receiver impairments — make the simulator physically honest

## Goal (one sentence)

Extend the simulator with the four impairment families a real
forward-deployed node will face (multipath, two-ray ground, log-normal
shadowing, and per-channel IQ imbalance / DC offset / ADC
quantization), each isolated behind a Protocol-typed plug-in so the
existing free-space + AWGN baseline from WS-A-001/002 stays the
backward-compatible default — so WS-B can layer richer σ-honesty
tests against scenarios that resemble cluttered terrain rather than
the laboratory.

## Side-cleanup (per lead instruction)

Same diff:

- **Remove** `packages/rfmesh-sdr/tests/__init__.py`.
- Commit message (verbatim, per lead):
  ```
  Remove tests/__init__.py per workspace convention (ADR-006). Tests
  discovered by pytest rootdir, not via namespace import.
  ```
- This is an ADR-006 / AGENTS.md §3.5 compliance fix; no separate
  ticket per lead's direction. Verify after removal: `uv run pytest
  packages/rfmesh-sdr -v` still green, no "Duplicate module named
  'tests'" warnings under workspace-wide mypy.

## Context (links only)

- Architecture: `ARCHITECTURE.md` §4 (simulator first-class — and
  explicitly intended to evolve), `INHERITED_CONTEXT.md` §3.1 (Phase
  C may update simulator models — design for replaceability).
- Contracts touched (read-only): `rfmesh_contracts.protocols`,
  `rfmesh_contracts.config.ArrayConfig`, `rfmesh_contracts.enums`.
- Prior tickets: WS-A-001 (free-space + AWGN baseline), WS-A-002
  (`ChannelImpairments` minimal — per-channel complex offsets).
  Both must be merged.
- Handoffs: this ticket completes what `WS-A-to-WS-B.md` §5 ("Known
  scope limits") promised would land in WS-A-003. With this ticket,
  WS-B's σ-honesty test can layer additional cases with multipath /
  shadowing inputs.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-sdr -v` passes. **All WS-A-001 and
   WS-A-002 tests continue to pass** (regression-free). New tests:

   a. `tests/test_channel_multipath.py::test_two_ray_constructive_destructive`
      — TwoRayGroundChannel with `h_tx=2.0 m`, `h_rx=1.5 m`, `f=915 MHz`.
      Compute the breakpoint distance `d_bp = 4·h_tx·h_rx / λ`. At
      `d < d_bp` the channel approximates Friis (within 3 dB). At
      `d > 2·d_bp` it falls off as `1/d⁴` (within 3 dB over 1
      decade). The interference null at `d ≈ d_bp` is at least 10 dB
      below Friis.

   b. `tests/test_channel_multipath.py::test_fir_multipath_produces_iq_ripple`
      — `MultipathFIRChannel` with two taps: `[1.0+0j, 0.7·exp(j·π/3)]`
      at delays `[0, 50]` samples. The output IQ at a CW emitter
      exhibits the closed-form spectral ripple
      `|H(f)| = |1 + 0.7·exp(j·(π/3 - 2π·f·50/fs))|`; measured PSD
      at three test frequencies matches within 0.5 dB.

   c. `tests/test_channel_shadowing.py::test_lognormal_shadowing_empirical_sigma`
      — `LogNormalShadowing(sigma_db=4.0)` over 500 fresh scenarios
      (`reseed` per trial). Measured `np.std(np.log10(|amp|)·20)` is
      within ±10% of 4.0 dB. The honesty of σ extends to channel
      models, not just bearings.

   d. `tests/test_channel_composition.py::test_composite_channel_layers_orthogonally`
      — `CompositeChannel(free_space, multipath_fir, log_normal)`.
      Verify that (i) running each impairment alone, then composing
      them in code, produces the same output as running through
      `CompositeChannel` (within numerical precision); (ii) order of
      multipath / shadowing does not change the result beyond
      numerical noise (they commute under multiplication).

   e. `tests/test_receiver_impairments.py::test_iq_imbalance_produces_image`
      — `IQImbalance(amplitude_db=0.5, phase_deg=3.0)` applied to a
      single CW emitter at frequency offset `+f0`. Output PSD shows
      an image tone at `-f0` whose level matches the closed-form
      image-rejection ratio `IRR = -20·log10(0.5·|ε|)` where
      `ε = (1 - g·exp(j·φ))`, within 0.5 dB. (Standard textbook
      formula for IQ imbalance image leakage.)

   f. `tests/test_receiver_impairments.py::test_dc_offset_appears_at_dc`
      — `DCOffset(i_volts=0.02, q_volts=-0.01)`. PSD at DC bin
      matches expected magnitude within 0.5 dB; bins away from DC
      are unaffected (within noise).

   g. `tests/test_receiver_impairments.py::test_adc_quantization_floor`
      — `ADCQuantization(bits=8)`. SNR of the recovered tone (signal
      bin vs noise floor) is clamped at the theoretical
      `6.02·N + 1.76 = 49.9 dB` ±1 dB for an emitter that would
      otherwise produce 80 dB SNR without quantization.

   h. `tests/test_backward_compat.py::test_default_scenario_unchanged`
      — A scenario constructed without any of the new impairments
      (only `emitters`, `antenna`, `sample_rate_hz`, `center_freq_hz`,
      optional `noise_floor_dbfs`, optional `array`) produces
      byte-identical IQ to the WS-A-001/002 baseline for the same
      seed and call sequence. This is the regression anchor:
      **no impairment defaults to "free-space-only Friis + AWGN."**

   i. `tests/test_protocol_conformance.py::test_channel_model_protocol_conformance`
      — All four channel implementations (`FreeSpaceChannel`,
      `TwoRayGroundChannel`, `MultipathFIRChannel`,
      `LogNormalShadowing`) and `CompositeChannel` structurally
      satisfy the `ChannelModel` Protocol (`@runtime_checkable`).
      Same for `ReceiverImpairments` Protocol (new):
      `IdentityReceiverImpairments`, `IQImbalance`, `DCOffset`,
      `ADCQuantization`, `CompositeReceiverImpairments`.

2. `uv run mypy packages/rfmesh-sdr` clean (strict).
3. `uv run ruff check packages/rfmesh-sdr` clean.
4. `uv run ruff format --check packages/rfmesh-sdr` clean.
5. No imports from other workstream packages. No new runtime deps.
   NumPy only. (`scipy.signal` is *tempting* for FFT-based
   convolution in multipath, but `np.convolve` covers this
   ticket's needs — if you find yourself reaching for scipy, STOP
   and surface, do not add it.)

## Out of scope (explicit non-goals)

- Time-varying channels (Doppler, fading rates). Static realisation
  per scenario only.
- Spatially-correlated shadowing across multiple receivers. Each
  scenario is one node; multi-node shadowing correlation is a
  WS-CD demo-scenario concern, not a simulator-internal concern.
- Mutual coupling between array elements. The four steering
  models from WS-A-002 assume isolated elements.
- Phase noise / oscillator drift. Separate ticket later if WS-B
  demonstrates a need.
- Hardware drivers. WS-A-005 territory.
- Modulations beyond CW. (LoRa-chirp emitters when L3 classifier
  needs them.)
- Mutating `packages/rfmesh-contracts/**` (Invariant 1).

## Files you may touch

- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/channel.py`     (modify — keep `FreeSpaceChannel`, `ChannelModel` Protocol; add `TwoRayGroundChannel`, `MultipathFIRChannel`, `LogNormalShadowing`, `CompositeChannel`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/impairments.py` (modify — keep `ChannelImpairments` Per-channel offsets from WS-A-002; add `ReceiverImpairments` Protocol, `IdentityReceiverImpairments`, `IQImbalance`, `DCOffset`, `ADCQuantization`, `CompositeReceiverImpairments`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/scenario.py`    (modify — add `channel: ChannelModel | None = None` and `receiver_impairments: ReceiverImpairments | None = None`; both default to identity behaviour preserving WS-A-001/002 baseline)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/synthetic_receiver.py` (modify — wire `channel` into render path before AWGN, wire `receiver_impairments` after AWGN, before quantization)
- `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/__init__.py`    (modify — export new public types)
- `packages/rfmesh-sdr/tests/conftest.py`                       (modify — add `multipath_scenario`, `shadowing_scenario`, `iq_imbalance_scenario` fixtures)
- `packages/rfmesh-sdr/tests/test_channel_multipath.py`         (create)
- `packages/rfmesh-sdr/tests/test_channel_shadowing.py`         (create)
- `packages/rfmesh-sdr/tests/test_channel_composition.py`       (create)
- `packages/rfmesh-sdr/tests/test_receiver_impairments.py`      (create)
- `packages/rfmesh-sdr/tests/test_backward_compat.py`           (create — the regression anchor)
- `packages/rfmesh-sdr/tests/test_protocol_conformance.py`      (modify — extend with the channel/receiver-impairments Protocol conformance tests)
- `packages/rfmesh-sdr/tests/__init__.py`                       (**REMOVE — per lead's side-cleanup instruction**)

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (Invariant 1).
- Other workstream packages (Invariant 2).
- Existing WS-A-001 / WS-A-002 test files. If any of them break
  because of a change you made, STOP and surface — that means the
  backward-compat regression anchor (test 1.h) is doing its job.

## Design hints (non-binding)

### Render-path order

After `channel` and `receiver_impairments` are wired:

```
for each emitter e:
    s = base_signal_for_emitter(e, heading, t)
    s = channel.apply(s, e.range_m, e.frequency_hz, rng)   # propagation
    accumulator += s · per_channel_steering_phase_i        # steering (A-002)
    accumulator += per_channel_complex_offset_i            # impairments (A-002)
accumulator += complex_awgn(...)                           # thermal noise (A-001)
output = receiver_impairments.apply(accumulator, rng)      # NEW (A-003)
# receiver_impairments includes: IQ imbalance, DC offset, ADC quantize
```

Order matters: channel impairments (multipath, shadowing) are
*before* steering (they affect the wavefront reaching the array);
receiver impairments (IQ imbalance, DC, quantization) are *after*
everything else (they live in the analog and ADC stages).

### `ChannelModel` Protocol surface

Already exists as defined in WS-A-001:
```python
def apply(self, samples: np.ndarray, distance_m: float,
          frequency_hz: float, rng: np.random.Generator) -> np.ndarray: ...
```

`TwoRayGroundChannel.apply` reads its `height_tx_m`/`height_rx_m`
from `self`, computes the path-length difference between direct and
ground-reflected rays at the given `distance_m`, sums them
coherently (ground reflection coefficient `≈ -1` for low grazing
angles is a fine approximation).

`MultipathFIRChannel.apply`: `np.convolve(samples, self.taps,
mode='same')` after the free-space scalar has been applied (the
multipath is a *post*-propagation channel filter). `MultipathFIRChannel`
takes taps + delays; the FIR impulse response is constructed in
`__post_init__`.

`LogNormalShadowing.apply`: draw a single log-normal sample per
`apply` call (one realisation per scenario block; no per-sample
fading at this stage). Multiply by `10**(fade_db/20)`. The `rng`
parameter is what makes the σ-honesty test (1c) work: 500 fresh
scenarios with `reseed` give 500 independent fade draws.

`CompositeChannel.apply`: chain `self.channels` in order. If
deterministic and stochastic channels are mixed, the order may
matter for reproducibility — document the convention (free-space
first, then deterministic FIR, then stochastic shadowing).

### `ReceiverImpairments` Protocol surface (new — define in `impairments.py`)

```python
@runtime_checkable
class ReceiverImpairments(Protocol):
    def apply(self, samples: np.ndarray,
              rng: np.random.Generator) -> np.ndarray:
        """Apply analog/ADC-stage impairments to baseband IQ.

        `samples` shape is either (n,) for single-channel or
        (n_channels, n) for coherent. Implementations broadcast
        appropriately.
        """
        ...
```

`IQImbalance(amplitude_db: float, phase_deg: float)`: rotates the
I and Q components by a complementary amount; the standard form is
`y[n] = α·Re(x[n]) + j·β·exp(j·φ)·Im(x[n])` with appropriate
normalisation. Reference: any digital comm textbook chapter on
"image rejection in direct-conversion receivers".

`DCOffset(i_volts: float, q_volts: float)`: adds the complex
constant `i_volts + j·q_volts` to every sample.

`ADCQuantization(bits: int)`: scales to `[-1, +1]` (handle clipping
by saturating, not wrapping — a real ADC does *not* wrap), quantises
to `2**(bits-1) - 1` levels per axis, scales back. Theoretical SQNR
`6.02·bits + 1.76 dB` falls out for free; the test in 1.g confirms it.

`IdentityReceiverImpairments()`: returns `samples` unchanged. Used
as the default when `scenario.receiver_impairments is None`.

`CompositeReceiverImpairments(impairments: tuple[ReceiverImpairments, ...])`:
chains them. Convention: order matters (DC offset before IQ imbalance
before quantization is the physically correct stack).

### Backward compatibility (test 1.h)

The most important test in this ticket. The render path must produce
**byte-identical** output for a WS-A-001/002 scenario (no `channel`,
no `receiver_impairments` set) after this ticket lands as before.

Implementation: `scenario.channel` defaults to `FreeSpaceChannel()`
(WS-A-001's behaviour); `scenario.receiver_impairments` defaults to
`IdentityReceiverImpairments()` (no-op). When both defaults are in
effect, the render path is mathematically identical to the
pre-ticket code path — the new abstractions are invisible.

If test 1.h fails, the regression anchor is doing its job. Surface
and stop; do not paper over.

## Stop conditions

- Stop after producing the diff. Paste pytest + mypy + ruff output
  when reporting back. Include the cleanup verification: confirm
  `packages/rfmesh-sdr/tests/__init__.py` is gone and tests still
  collected and passing.
- If a closed-form physics check fails for what looks like a physics
  reason (e.g. two-ray null does not land near `d_bp`, IQ-imbalance
  image-rejection is off by 6 dB), STOP and surface via
  `.claude/scratchpad/ws-a-<date>.md`. This is the simulator-honesty
  failure mode the architecture exists to detect.
- If you find yourself reaching for `scipy.signal`, `scipy.fft`,
  or any non-stdlib non-numpy dep, STOP and surface. NumPy's
  `np.convolve`, `np.fft.rfft/irfft`, and `np.random.Generator`
  cover everything here.
- If the backward-compat regression anchor (test 1.h) fails, STOP.
  That is the test designed to catch the case where your new
  default behaviour drifted from old.
