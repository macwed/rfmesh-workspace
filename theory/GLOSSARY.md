# rfmesh Glossary — every term, one line

Plain-language answers to every term the greenhorn flagged. `→ NN` points to the module
that teaches it properly. Grouped by theme.

## Mission & system (→ 00)

- **RF** — radio frequency; radio waves.
- **Emitter** — anything transmitting radio (a jammer, a drone control link, a phone).
- **Geolocation** — pinning the emitter on a map.
- **Jammer / jamming** — a transmitter blasting noise to drown out comms or GPS; **counter-jamming** = locating it so it can be stopped.
- **MoD** — Ministry of Defence. **BoTH3** — the Belgian MoD counter-jamming challenge this is built for.
- **Bearing** — the compass direction (angle) from a node toward the emitter.
- **Node** — one small box: a radio receiver + antenna that measures a bearing.
- **Mesh / cooperative** — many nodes networked together, pooling their bearings.
- **Distributed** — the nodes are spread out in different places.
- **Fusion server** — the central computer that combines all bearings into one position.
- **Triangulation / cross-fix** — crossing two or more bearing lines to get a point.
- **Uncertainty ellipse** — the oval on the map showing where the emitter probably is.
- **ATAK** — the soldier's Android map app where the marker appears (→ S).
- **L1 / L2 / L3** — the three "capability layers": cheap angle-finding / precise angle-finding / ML labelling.

## Signals (→ 01)

- **Sample** — one instantaneous measurement of the radio wave (thousands per second).
- **Phasor** — a tiny spinning arrow representing the wave: length = strength, angle = phase.
- **IQ sample / I / Q** — the arrow's two shadows: I (horizontal) and Q (vertical); together they are the arrow's tip.
- **Phase** — where the wave is in its spin cycle (the arrow's angle).
- **Amplitude** — the arrow's length (signal strength).
- **|iq|** — the arrow's length. **|iq|²** — its power (length squared).
- **Power** — strength squared; what we actually measure (like brightness/loudness).
- **mean()** — average over a block of samples.
- **dB (decibel)** — a log scale for ratios; +10 dB = 10× power, +3 dB ≈ 2×, 0 dB = same.
- **10·log10(...)** — the formula that turns a power ratio into dB.
- **RSSI** — received signal strength, in dB: `10·log10(mean(|iq|²))`.
- **Noise floor** — the dB level of the background hiss when no signal is present.
- **SNR** — signal-to-noise ratio: how many dB the signal sticks up above the noise floor.
- **"Relative, not absolute" power** — these radios aren't calibrated, so we never claim watts/dBm, only relative strength.

## Geometry (→ 02)

- **Azimuth** — a compass angle: North = 0°, clockwise (East 90°, South 180°, West 270°).
- **Heading** — which way a node's antenna is aimed.
- **Line of position** — the ray a single bearing draws; one node alone gives a line, not a point.
- **Why an ellipse (not a circle)** — each bearing is a fuzzy wedge; two wedges overlap in an oval, stretched along the poorly-crossed direction.

## Uncertainty (→ 03)

- **Sigma (σ) / standard deviation** — the typical spread of measurements around their average.
- **Variance** — sigma squared (σ²); same idea, squared.
- **Gaussian / bell curve** — the usual shape of random errors; ~68% land within ±1σ, ~95% within ±2σ.
- **"40° ± 3°"** — a bearing of 40° with a sigma of 3°.
- **Inverse-variance weighting** — trust precise measurements more, by weighting each as `w = 1/σ²`.
- **Honest sigma** — a reported σ that genuinely matches real scatter; a too-small (lying) σ poisons the whole fusion.

## L1 amplitude DF (→ 04)

- **Directional antenna / Yagi** — an antenna that hears much better where it's pointed, like a flashlight beam.
- **Beamwidth** — how wide that "better-hearing" beam is (~50° for the ATK-10).
- **Servo** — a small motor commanded to a precise angle; it rotates the Yagi.
- **Sweep / amplitude comparison** — rotate the Yagi, measure RSSI at each angle, the strongest = the direction.
- **argmax** — the angle where the RSSI curve peaks.
- **Parabola fit / vertex** — fit a parabola to the points around the peak; its top (vertex `x = −c1/(2·c2)`) is the refined bearing.
- **Prominence gate** — the peak must rise ≥ 6 dB above the floor, or the estimator returns "no bearing."
- **Refuse / return None** — reporting nothing rather than a confident wrong guess.

## Linear-algebra kit (→ 05, all black-box)

- **Vector** — an arrow / a list of numbers.
- **Matrix** — a machine: feed an arrow in, get a transformed arrow out.
- **Transpose** — flip a grid across its diagonal (rows ↔ columns).
- **Hermitian (superscript H)** — transpose plus flipping each entry's phase sign; a routine tidy-up the formulas need.
- **Matrix inverse (A⁻¹)** — the "undo" button; software computes it, never by hand.
- **Eigenvector** — a special direction a matrix only stretches (doesn't twist). **Eigenvalue** — how much it stretches (big = strong/important).
- **Eigendecomposition** — software handing you all the special directions + stretch sizes, sorted big-to-small.
- **Covariance matrix R** — a small table summarizing how several signals move together; `R = (1/T)·X·Xᴴ`.
- **Diagonal loading** — adding a tiny amount to a matrix's diagonal to steady it before inverting (ballast for a tippy boat).

## L2 array (→ 06, estimators are black-box)

- **Array / element** — a fixed row of several small antennas; each one is an "element."
- **Phase across elements** — a wave from an angle hits the near element slightly before the far ones; that phase step encodes the angle. (The keystone idea.)
- **Phase-coherent / one RF chip** — the receivers share one clock so their phases are truly comparable.
- **Calibration** — a one-time step removing fixed phase/gain mismatches between channels.
- **Steering vector a(θ)** — the predicted phase pattern for a guessed angle θ; a template to match against.
- **Subspace** — splitting R's directions into the strong "signal" pile and the weak "noise" pile.
- **MUSIC** — scan all angles; the formula spikes where a template can't be explained by the noise pile = a real direction.
- **Pseudospectrum** — the spiky scan curve MUSIC/Capon produce; tall spike = an emitter.
- **Capon / MVDR-spectrum** — an "adaptive spotlight": aim at θ, crush everything else, read the leftover power.
- **Null-steering** — same math used defensively: aim a "deaf spot" (null) at a jammer to protect our own array.
- **Anti-desense (not ECM/jamming)** — we only protect our receiver; we never transmit.
- **"One matrix, two products"** — the same R gives both the emitter's bearing and a jammer null.

## Fusion (→ 07)

- **Least squares** — pick the point that minimizes the total squared miss to all bearing lines.
- **Weighted least squares** — same, but precise bearings count more (`w = 1/σ²`).
- **Stansfield** — the one-shot ("closed-form") weighted-least-squares first answer (the "seed").
- **Jacobian** — a table of "sensitivities": how fast each node's predicted angle changes as the target moves. One table, reused for the fix, the ellipse, and GDOP.
- **MLE / Gauss-Newton** — start at the seed and "roll downhill" to the position that best explains all bearings; stop when steps get tiny.
- **(JᵀWJ)·dx = JᵀWr** — compact way to write "one downhill step"; software solves it.
- **Covariance of the answer** — a small 2×2 table for how uncertain (and in which direction) the position is.
- **Confidence ellipse / chi-square / 5.991** — eigendecompose that 2×2 for the oval's axes, then scale by the fixed 95% constant 5.991 (≈ 2.45·σ).
- **GDOP** — one number for geometry quality: ~1 great, >6 bad (nodes nearly collinear, ellipse smeared).
- **CRLB** — the theoretical best any method could do for this geometry/SNR; honesty tests check we get close, not that we beat it.
- **Residual** — how far a node's bearing missed the final fix; **outlier** = residual > 3σ (probable multipath/miscalibration).
- **Fallback centroid** — when geometry is hopeless, a rough weighted center, clearly labelled LOW confidence (never a fake precise fix).

## Validation (→ 08)

- **Monte-Carlo** — run the same thing thousands of times with fresh random noise and measure the real scatter.
- **Ground truth** — the true answer, known only in the simulator, used to judge honesty.
- **Sigma-honesty test** — claimed σ must match real scatter within ±20% at SNR 10/20/30 dB, or the build fails.
- **Pessimism factor** — inflating clean simulator numbers because the real world is noisier (the bench learning).
- **No silent fallbacks** — fail loudly, never quietly fake an answer.
- **Golden-file test** — freeze a known-good output to a file; fail if future code drifts from it.

## Propagation (→ 09)

- **Free-space loss / Friis** — signal weakens like 1/distance² in open air (double the distance ≈ −6 dB), like a dimming light bulb.
- **Multipath** — the receiver hears the direct wave plus delayed echoes off ground/walls/trees; the main reason bearings go wrong.
- **Two-ray ground** — direct ray + one ground-bounce that can partly cancel it (creates "dead zones").
- **FIR / tapped-delay** — a general echo recipe: original plus several scaled, delayed copies.
- **Log-normal shadowing** — slow random fading as big obstacles block the path.
- **Front/back (F/B) ratio** — how much stronger the wanted direction is than off-axis, in dB; big = clean peak.
- **Co-channel interference** — another emitter on the exact same frequency; can cause a *confident wrong bearing* (the Mast B failure).
- **Phase C** — the bench test asking "do we actually get a clean peak in the right direction?" (Masts A/B failed, C passed.)
- **Reference-emitter isolation check** — confirm the wanted direction beats others by ~6 dB before trusting a frequency.
- **Polarization mismatch / pattern deformation / servo backlash / RF-chain** — four of the five pre-listed failure modes (mounting and mechanical/electrical faults).

## Scaffolding (→ S)

- **Simulator / SyntheticReceiver** — a fake radio with the same interface as a real one; lets the whole system be built with no hardware.
- **Pydantic / contract / frozen** — strict data forms that reject malformed input; "frozen" = fixed so all parts can rely on them.
- **Protocol** — an agreed list of what a component must DO (like a power-socket standard), not how.
- **Star dependency graph** — every part depends on the one shared contract package, not on each other.
- **Schema version** — a version stamp on every message so contract changes are caught automatically.
- **UART** — a simple serial wire between computer and servo.
- **TLV** — Type-Length-Value: a tidy way to pack a message.
- **CRC-16** — a checksum to detect if any byte got corrupted in transit.
- **COBS** — a framing trick that uses the byte 0 as an unambiguous "end of message" marker.
- **LoRa beacon** — a built transmitter sending a known signal from a known spot (868.1 MHz, SF7) — a practice target with a known answer.
- **NTP** — ordinary network clock sync, good to ~10 ms.
- **AoA vs TDOA** — Angle-of-Arrival (this project; needs only loose timing, no GPS) vs Time-Difference-of-Arrival (needs nanosecond sync + GPS, jammed first).
- **CoT (Cursor on Target)** — the message format ATAK understands. **PyTAK** — the library that sends it. **FreeTAKServer** — a server that relays CoT messages.
- **Equirectangular polygon** — approximating the ellipse as a many-sided polygon (ATAK can't draw true ellipses), via a simple flat-map conversion accurate over a few km.
- **ADR** — Architecture Decision Record: a written-down decision + why.
- **Council review** — a set of review agents (architect, code-reviewer, RF specialist, demo-integrity) that check changes before they land.
