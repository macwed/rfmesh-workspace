# rfmesh — Theory Study Program

A from-scratch course that unpacks **every concept, method, and formula** behind
rfmesh (the cooperative bearing-mesh for RF emitter geolocation). Written for a
**high-school alumnus**: if you know algebra, basic trig (sin/cos), vectors-as-arrows,
and what an average is, you can finish this. No calculus, linear algebra, complex
numbers, or DSP assumed — those are taught as pictures, not proofs.

## The one rule this course lives by

> **You need to USE it, not DERIVE it.** Hard math (eigenvectors, Gauss-Newton,
> chi-square, Fisher information) is taught as a *black box*: what goes in, what comes
> out, and how to read the picture. You will never compute these by hand — software does.
> Every black box is flagged with a `> Depth-cap` line so you know exactly how deep to go.

## How to use it

Read the modules **in number order** — each builds on the ones before and never refers
forward to something un-taught. The **scaffolding** module (`S`) is independent: read it
whenever you're curious about the software/hardware plumbing.

Each module follows the same shape:
1. **What you'll be able to do after this**
2. **Questions this answers** (the beginner questions it clears)
3. **Lesson** (analogy → tiny example → picture, bottom-up)
4. **Where this lives in the code** (real `file:line` pointers into rfmesh)
5. **Click test** (questions you should now answer in plain words)

Stuck on a word? Check **[GLOSSARY.md](GLOSSARY.md)** — every term, one line.

## Dependency map

```
            00 Mission  (what & why)
                 │
            01 Signals  (IQ, dB, SNR, RSSI)
                 │
        ┌────────┼─────────────┐
        │        │             │
   02 Bearings   │        03 Uncertainty
   (geometry)    │        (sigma, weighting)
        │        │             │
        │   04 L1 amplitude DF  │   ← first full bearing method
        │        (uses 01 + 02) │
        │                       │
   05 Linear-algebra kit  ◄─────┘   ← the "foreign math", picture-only
        │
   06 L2 array  (phase, MUSIC/Capon/null)   ← precise bearing method
        │
   07 Fusion  (Stansfield + MLE + ellipse + GDOP + CRLB)   ← uses 02,03,05
        │
   08 Validation  (Monte-Carlo, honest sigma)
        │
   09 Propagation  (multipath + the real bench results)

   S  Scaffolding  (simulator, contracts, servo, LoRa, NTP, CoT/ATAK, governance)
      └─ read any time; blocks nothing
```

## The modules

| # | Module | In one line | Unlocks |
|---|--------|-------------|---------|
| [00](00-mission.md) | **Mission & system shape** | What rfmesh is, and why 20 m at 3 km is hard | the whole picture |
| [01](01-signals.md) | **Signals foundation** | IQ samples, decibels, SNR, RSSI = 10·log10(mean\|iq\|²) | every measurement |
| [02](02-bearings.md) | **Geometry of bearings** | Azimuth, lines of position, triangulation, why an ellipse | fusion geometry |
| [03](03-uncertainty.md) | **Uncertainty** | Sigma, the bell curve, inverse-variance weighting, *honest sigma* | the system's lynchpin |
| [04](04-L1-amplitude-df.md) | **L1 amplitude DF** | Spin a Yagi, find the RSSI peak, refuse weak peaks | the cheap bearing |
| [05](05-linalg-kit.md) | **Linear-algebra kit** | Matrix=machine, inverse=undo, eigen=special directions, R | modules 06 & 07 |
| [06](06-L2-array.md) | **L2 array** | Phase across elements → MUSIC / Capon / null-steering | the precise bearing |
| [07](07-fusion.md) | **Fusion** | Bearings → position + 95% ellipse + GDOP + CRLB | the output |
| [08](08-validation.md) | **Validation & honesty** | Monte-Carlo proof that the error bars are real | trust |
| [09](09-propagation.md) | **Propagation & the bench** | Multipath, and what really happened at Masts A/B/C | the real enemy |
| [S](S-scaffolding.md) | **Scaffolding** | Simulator, contracts, servo, LoRa, NTP, CoT/ATAK, governance | the plumbing |

## The keystones (grasp these and the rest falls into place)

1. **Phasor / IQ sample** (01) — every radio value is an arrow with length + angle.
2. **Sigma** (03) — the spread; it weights fusion *and* sizes the ellipse *and* is the trust currency.
3. **Phase across elements** (06) — the single physical idea behind all precision DF.
4. **The covariance matrix R + eigendecomposition** (05) — one tool, used at both ends (MUSIC and the ellipse).
5. **The Jacobian = sensitivity** (07) — one table that powers the fix, the ellipse, GDOP, and the CRLB.
6. **Honest sigma** (03 + 08) — the whole project's bet: real error bars beat impressive-looking magic.

## How this course was built

A study group of agents built it: a **greenhorn** (knew nothing, flagged every confusing
word), an **RF/DSP engineer** (mapped the concept dependencies), and a **high-school physics
teacher** (capped every explanation at high-school depth, vetoing rabbit-holes). Each module
was drafted, ceiling-checked, and re-grilled until a true beginner would get it. The math is
real and grounded in the actual rfmesh code; the *depth* is deliberately bounded.
