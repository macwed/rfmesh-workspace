# Module 06 — L2: Precision Direction Finding with an Antenna Array

> Builds on **Module 01** (phase, IQ) and **Module 05** (R, eigendecomposition, inverse).
> This is the *precise* way to find a bearing — 1 to 3 degrees — versus L1's coarse 5 to 15 degrees.

---

## What you'll be able to do after this

- Explain, in plain words, **why a radio wave arriving from an angle shows up at a slightly different phase on each antenna** — and why that tiny phase step is a fingerprint of the arrival angle.
- Say what a **steering vector** `a(θ)` is: the predicted phase pattern for a guessed direction, a "template" you match against the data.
- Recall (from Module 05) what **R** is and what **eigendecomposition** and the **inverse** do, and name where each one shows up in L2.
- Describe — at the black-box level — what **MUSIC**, **Capon/MVDR**, and **MVDR null-steering** each do, and what their pictures look like.
- Explain the project slogan **"one matrix, two products"**: the same R both locates an emitter *and* lets you aim a deaf-spot at a jammer.
- Say why L2 beats L1 on precision, and why both still hand the fusion server the same thing: a bearing plus an honest sigma.

---

## Questions this answers

- What is an "array"? What is an "element"?
- What does "phase across elements" mean, and why does the arrival angle change it?
- What does "phase-coherent" mean, and why does it need one RF chip / a shared clock?
- What is "calibration" here, and why is it needed once?
- What is a steering vector `a(θ)` — the "template"?
- What's the difference between the "signal directions" and the "noise haze" (the subspace idea, in plain words)?
- What is MUSIC? What is a "pseudospectrum"? Why does it "spike"?
- What is Capon/MVDR? What does "adaptive spotlight" mean?
- What is null-steering? What is a "null" / "deaf spot"?
- What's the difference between *anti-desense* and *jamming back*?
- What does "one matrix, two products" mean?
- Why is L2 good for 1–3 degrees when L1 is only good for 5–15?

---

## Lesson

### (a) The keystone: phase across elements

This is the one physical idea the whole module rests on. Spend your time here.

Picture a fixed row of small antennas, spaced a little apart. Each antenna is an **element**. The whole row, working together, is an **array**.

Now a radio wave rolls in.

If the wave comes from **straight ahead**, it reaches every element at the *same moment*. Module 01 taught you that a wave is a spinning thing — at any instant it's at some point in its spin cycle, and that point is its **phase**. Same moment of arrival means same point in the spin: **every element sees the same phase**.

```
  Wave coming from STRAIGHT AHEAD (broadside)
  the wavefront is a flat wall, parallel to the row:

        wavefront
   =====================>   (travelling toward the row)

      |     |     |     |
     el0   el1   el2   el3      <- all hit at the SAME instant
      ^     ^     ^     ^          => SAME phase on every element
```

Now tilt the source. The wave comes in **at an angle**. The wall of the wavefront is now slanted. It touches the **nearest** element first, then the next, then the next — each one a tiny bit later.

```
  Wave coming from an ANGLE:
  the slanted wavefront reaches the near element first.

              /
            /  wavefront
          /    (slanted)
        /
      |     |     |     |
     el0   el1   el2   el3
      ^      ^      ^      ^
      t0  <  t1  <  t2  <  t3      <- arrival "ticks": each later than the last
      |      |      |      |
   phase0  phase1 phase2 phase3    <- each element caught at a slightly
                                      DIFFERENT point in the spin cycle
```

A *later* arrival means the element catches the wave at a slightly different point in its spin — a **different phase**. And because the elements are evenly spaced, the phase change from one element to the next is a steady, repeating **step**.

That step is the whole secret. **The size of the phase step from element to element is a fingerprint of the arrival angle.**

- Wave straight ahead → step of zero (all phases equal).
- Wave at a small angle → small step.
- Wave at a big angle → big step.

**Analogy.** Think of an ocean wave hitting a row of pier poles at an angle. It splashes the near pole first, then the next, then the next, in an even rhythm. If you only knew the splash *times*, you could still work out which direction the wave came from — the delay pattern gives it away. The array does exactly this, but with phase instead of splash-times, and it does it in a single instant.

**The payoff:** if we can read the tiny phase steps across the elements, we can compute the arrival angle very precisely. That is L2 in one sentence.

**Phase-coherent + one RF chip.** Those phase steps are tiny. To compare them honestly, every element's receiver must be reading from the *same clock* — otherwise each receiver adds its own random phase wobble and the steps are buried in junk. Sharing one clock is what **"phase-coherent"** means. The cleanest way to get it is **one RF chip serving all the channels** (which is exactly why the project's L2 hardware — bladeRF, Pluto+ — uses a single radio chip feeding multiple channels: coherent by construction).

**Calibration.** Even with a shared clock, the cables and circuit traces to each element differ by a hair, adding a small *fixed* phase offset per channel. **Calibration** is a one-time measurement that learns those fixed offsets so the software can subtract them back out. After calibration, the only phase differences left are the ones the *arrival angle* caused — the fingerprint we want. (The system refuses to emit L2 bearings from an *uncalibrated* stream — garbage offsets would mean garbage angles.)

---

### (b) The steering vector `a(θ)` — the template

We know the array's geometry: how far apart the elements sit. So for **any guessed angle θ**, we can predict in advance the exact phase pattern the array *would* see if a wave really came from θ.

That predicted pattern is the **steering vector**, written `a(θ)`. Think of it as a **stencil** or **template** for direction θ: "if the source is at θ, the elements should show *these* phases."

```
  Templates a(θ) for a few guessed angles (phase per element, sketch):

   θ = 0°   (straight ahead):   el0:  ·   el1:  ·   el2:  ·   el3:  ·     (flat, no step)
   θ = 30°  (mild angle):       el0:  ·   el1:  ⌐   el2:  ¬   el3:  ⌐     (small steady step)
   θ = 60°  (steep angle):      el0:  ·   el1:  ⌐   el2:  ¬   el3:  L     (bigger steady step)
```

The whole game of L2: **find the θ whose template best matches the real measured data.** Every estimator below is a different clever way of doing that match.

> **Depth-cap.** Treat `a(θ)` as a black box: "the predicted phase pattern for a guessed angle." You don't need the formula that builds it — only that we *can* build one for any θ, and that we match it against the data.

---

### (c) Build R — one line, recall Module 05

We take many quick **snapshots** across the elements (each snapshot is one set of simultaneous readings, one per element). From all those snapshots we form **R**, the covariance matrix — the table from Module 05 that summarizes **how the elements' signals move together**.

That's it. One line. **All three methods below start from the same R.**

---

### (d) MUSIC (intuition-only)

Take R and run it through **eigendecomposition** (Module 05). This splits what the array is hearing into two buckets:

- a **few strong directions** — the real signals;
- everything else — the **noise "haze"**.

Now MUSIC scans every angle θ, one at a time, and asks a sharp question: *"Does this θ's template line up with something the noise-haze cannot explain?"*

At the true angle, the answer is yes — strongly. MUSIC's score `P(θ) = 1 / (something that drops to nearly zero at the true angle)`. Divide by a near-zero number and the score **shoots up**. So as you sweep θ from 0 to 360 degrees, the curve is mostly flat — and then **spikes** sharply right at the emitter's bearing.

**Analogy.** Shine the data through a prism. Most of what comes out is dull haze. But a couple of **bright, sharp lines** appear that the haze simply can't account for — those bright lines are your real emitters.

```
  MUSIC pseudospectrum  P(θ)  (scan over azimuth):

  P |                              *
    |                              *           <- tall, narrow spike
    |                              *              = emitter bearing
    |                             ***
    |__.--.___.--._.-._.--.___.--*****.--._.-._.__.--._____
    0°        60°       120°    155°    210°       300°   360°
                                  ^
                              emitter at ~155°
```

The tall, narrow spike is why MUSIC gives **super-resolution** — it can even split two emitters sitting close together, because two real directions make two separate spikes.

> **Depth-cap.** Black box. Know: *"scan angles, the spike marks the direction."* It runs R through eigendecomposition (Module 05's tool), keeps the strong directions as signal and the rest as noise-haze, and `P(θ)` spikes where a template can't be explained by the haze. No subspace math required — **use it, don't derive it.**

*(In code: `l2_music.py` — eigendecomposition via `numpy.linalg.eigh`, then the pseudospectrum `P(θ) = 1 / ||E_nᴴ a(θ)||²` scanned over azimuth, peak picked as the bearing.)*

---

### (e) Capon / MVDR-spectrum (intuition-only)

A different way to scan. For each angle θ, Capon builds the **smartest possible spotlight**: a listening pattern that keeps **full brightness aimed at θ** while **dimming every other direction as hard as it can**. Building that custom spotlight is what uses **R⁻¹**, the matrix inverse from Module 05.

Then it simply reads how much power still leaks through the spotlight. Its score is `P(θ) = 1 / (aᴴ R⁻¹ a)`, which comes out **big only when θ is a real source** — because if nothing's there, the spotlight successfully muted everything and almost no power leaks.

**Analogy.** An adjustable directional microphone. You aim it at θ, and it **auto-mutes everything except θ**. A loud reading means something is genuinely there in that direction; silence means there isn't.

```
  Capon pseudospectrum  P(θ)  (same kind of scan):

  P |                          ***
    |                         *****          <- peak at the source
    |                        *******            (a bit broader than MUSIC's)
    |__.--._.--._.-.__.--.__********__.--._.-._.__.--.__
    0°       60°      120°    155°   210°      300°  360°
```

Capon's peak is a little **broader** than MUSIC's spike (MUSIC has the sharper super-resolution). But Capon is often **more robust when you have only a few snapshots** or low SNR — handy in the field.

> **Depth-cap.** Black box. Know: *"adaptive spotlight, leftover power = source."* It uses R⁻¹ (Module 05's inverse) to shape the spotlight; you read off where power leaks through. No Lagrange multipliers, no derivation — **use it, don't derive it.**

*(In code: `l2_mvdr.py` — Capon `P = 1 / (aᴴ R⁻¹ a)`, with **diagonal loading** added to R before inverting so the inverse stays numerically stable. Contract enum `L2_CAPON`.)*

---

### (f) MVDR null-steering — the dual-use twist (intuition-only)

Here's the elegant part. **Same R. Same spotlight math.** But now we use it to **protect** instead of to **find**.

In Capon we aimed the spotlight *at* a direction to detect a source. In null-steering we keep our spotlight listening to our **wanted** direction, but we deliberately carve a **"deaf spot" — a null —** aimed straight at a strong **jammer**. The jammer's energy falls into the deaf spot, so it stops *deafening our own array*, while we keep hearing the direction we care about.

The weights that do this are `w = R⁻¹a / (aᴴR⁻¹a)` — again just R⁻¹ from Module 05, applied to the look-direction template `a`.

```
  Array receive pattern with null-steering:

  gain |   ___                                   ___
       |  /   \         full gain kept           /   \
       | /     \        on the TARGET           /     \
       |/       \____                  ____ ___/       \
       |             \                /
       |              \      |       /
       |               \     V      /
       |                \   notch   /        <- deep NULL (deaf spot)
       |                 \_/    \_./             carved at the JAMMER's angle
       +------------------------------------------------> azimuth
              ^target                ^jammer
```

This is **anti-desense**: we are **protecting our own receiver** from being swamped. We are **not** jamming back — **we do not transmit anything**. We just shape what we *listen* to.

And that brings us to the project slogan:

> **"One matrix, two products."**
> The *same* R gives you both the **emitter's location** (MUSIC / Capon) **and** a way to **null a jammer** (MVDR weights). One snapshot of the scene, two completely different deliverables.

> **Depth-cap.** Black box. Know: *"same math, aims a deaf-spot at a jammer."* Same R, same R⁻¹ as Capon — repurposed to suppress instead of detect. No derivation needed — **use it, don't derive it.**

*(In code: `l2_null_steering.py` — `compute_null_steering_weights`, weights `w = R⁻¹a / (aᴴR⁻¹a)`. It returns weights, **not** a bearing report — it's a protection utility, not a third locator.)*

---

### (g) Why L2 beats L1

| | **L1 — Detection** | **L2 — Precision DF** |
|---|---|---|
| How it works | Mechanically **rotates one antenna**, measures signal strength at each angle, picks the strongest | Reads tiny **phase steps across many fixed antennas** at once |
| Precision (sigma) | Coarse: **5–15°** | Fine: **1–3°** |
| Separate two close emitters? | No | **Yes** (MUSIC's spikes split them) |
| Hardware cost | Cheap, single-channel | Costlier: phase-coherent multi-channel + **calibration** |

L1 is a lighthouse turning until it finds the brightest direction — simple, cheap, blurry. L2 reads the wavefront's fingerprint across the whole array in one shot — precise, but it needs coherent hardware and a calibration step.

Crucially, **both output the same thing**: a bearing plus an honest **sigma** (Module 03's idea — an honest 1-σ uncertainty in degrees). Because the report shape is identical, the **fusion server (Module 07)** treats an 8°-sigma L1 bearing and a 1.4°-sigma L2 bearing **identically** — it just weights each by its sigma. That's what lets cheap and precise nodes work together in one mesh.

---

## Where this lives in the code

| Concept in this lesson | File | What's there |
|---|---|---|
| Steering vector / "template" `a(θ)` | `packages/rfmesh-dsp/src/rfmesh_dsp/array_manifold.py` | `steering_vector` (per-element predicted phases for a guessed angle); `steering_matrix` (a stack of templates for a whole scan grid) |
| MUSIC | `packages/rfmesh-dsp/src/rfmesh_dsp/l2_music.py` | Eigendecomposition via `numpy.linalg.eigh`, noise-subspace, pseudospectrum `P = 1/‖E_nᴴ a‖²`, spike pick |
| Capon / MVDR-spectrum | `packages/rfmesh-dsp/src/rfmesh_dsp/l2_mvdr.py` | Capon `P = 1/(aᴴ R⁻¹ a)`, diagonal loading; emits `L2_CAPON` bearings |
| MVDR null-steering | `packages/rfmesh-dsp/src/rfmesh_dsp/l2_null_steering.py` | `compute_null_steering_weights`, `w = R⁻¹a/(aᴴR⁻¹a)`; returns weights, not a bearing |
| Concept context | `ARCHITECTURE.md` §1 (L2), `INTERFACES.md` §1 (`L2_MUSIC` / `L2_CAPON` / `L2_MVDR_NULL`) | Capability layer write-up and contract enum semantics |

A real array, to anchor the idea visually — the **KrakenSDR**, a 5-channel coherent receiver in the UCA layout (`ArrayGeometry.UCA`) mentioned in `INTERFACES.md`:
<https://www.krakenrf.com/krakensdr> — and a general phased-array reference picture:
<https://en.wikipedia.org/wiki/Phased_array#/media/File:Phased_array_animation_with_arrow_10frames_371x400px_100ms.gif>

---

## Click test

Check yourself. Answers in plain words — no math.

1. A wave arrives **straight ahead** of the array. What do the elements' phases look like — same, or stepping? Now it arrives **at a steep angle**. What changes, and which element "feels" it first?
2. Fill in: the steady **phase ___ from element to element** is a fingerprint of the **arrival ___**.
3. What is a steering vector `a(θ)` in one sentence? What is the "whole game" you play with it?
4. "Phase-coherent" — what has to be shared across the receivers, and why does the hardware use one RF chip to get it? What does **calibration** clean up?
5. MUSIC scans angles and the score **spikes** at the emitter. In the prism analogy, what are the "bright lines"? Which Module 05 tool turned R into signal-vs-haze?
6. Capon's "adaptive spotlight": what does it keep bright, what does it mute, and what reading tells you a source is really there? Which Module 05 tool builds the spotlight?
7. Null-steering carves a "deaf spot." What is it aimed at, and what does it protect? Is this *jamming back*? (Trick — say why or why not.)
8. Explain **"one matrix, two products"** to a teammate in one breath.
9. L1 gives 5–15°, L2 gives 1–3°. Name the one physical thing L2 reads that L1 doesn't, and the one cost L2 pays for it.
10. An L1 node reports a bearing at ±9° and an L2 node reports ±1.5°. Does the fusion server (Module 07) need different code for each? Why not?
