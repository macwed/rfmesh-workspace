# rfmesh-sdr

Workstream A. Houses every `Receiver` / `CoherentReceiver` Protocol
implementation: the synthetic simulator (the L1 deliverable here) and,
in later tickets, hardware-backed receivers (RTL-SDR, HackRF, bladeRF,
Pluto).

## What lives here (v0.1.0)

- `simulator/synthetic_receiver.py` -- `SyntheticReceiver`, a single-channel
  `Receiver` Protocol implementation. Generates baseband IQ from a
  configurable emitter + antenna + free-space-channel + AWGN scenario,
  fully deterministic from one integer seed.
- `simulator/scenario.py` -- the frozen `SimulationScenario` and its
  validation.
- `simulator/{emitter, antenna, channel, noise, rng}.py` -- the inner data
  leaves and the `ChannelModel` Protocol (with `FreeSpaceChannel` as the
  v0.1 implementation).
- `exceptions.py` -- `ReceiverNotOpenError`, `InvalidReadSizeError`.

## What does not live here yet

- Coherent (L2) mode of `SyntheticReceiver` -- WS-A-002.
- Multipath, two-ray-ground, log-normal shadowing, IQ imbalance, ADC
  quantization, per-channel phase/gain offsets -- WS-A-003.
- Hardware-backed `Receiver`s (`SoapyReceiver`, etc.) -- later tickets.

See `docs/tickets/WS-A-001.md` for the deliverable that scaffolded this
package and `ARCHITECTURE.md` Section 4 for the simulator's first-class
role.
