# Module 09 — Propagation, Multipath, and What Really Happened on the Bench

> Builds on **Module 01** (dB, SNR, RSSI) and **Module 04** (the L1 6 dB
> prominence gate, the front/back idea). If "−6 dB means a quarter of the
> power" and "a bearing only counts if the peak clearly beats the
> background" don't ring a bell yet, skim those two first.

---

## What you'll be able to do after this

By the end of this module you will be able to:

- Explain, the way you'd explain a dimming light bulb, **why a radio signal
  gets weaker with distance** — and put a number on it (double the distance,
  about a quarter the power, ≈ −6 dB).
- Say what **multipath** is, and why bouncing radio — not bad math — is the
  thing that wrecks real direction-finding.
- Name the three bounce models the simulator uses (**two-ray ground**,
  **FIR / tapped-delay**, **log-normal shadowing**) and say in one sentence
  what each one does, without touching the math.
- Connect the **front/back (F/B) ratio** to Module 04's 6 dB gate: big F/B =
  trustable peak, small F/B = mush.
- Tell the **Phase C bench story** — three real cell masts, two honest
  failures, one clean pass — and explain *why* each turned out the way it did.
- List the **five pre-written failure modes** so that when the gear says
  FAIL, you diagnose instead of panic.

---

## Questions this answers

- Why does a signal get weaker the farther away you are? What's "free-space
  loss"? What's "Friis"?
- Why specifically *1/distance²*? Why "−6 dB per doubling"?
- What's multipath? What's an echo, and how can an echo *cancel* a signal?
- What are two-ray ground, "dead zones," FIR / tapped-delay, log-normal
  shadowing, and fading?
- What's the front/back (F/B) ratio and how does it tie to the 6 dB gate?
- What's co-channel interference, and what's a "confident wrong bearing"?
- What is Phase C? Why did Mast A and Mast B fail but Mast C pass?
- What's the reference-emitter isolation check?
- What are polarization mismatch, antenna-pattern deformation, servo
  backlash/cogging, and an RF-chain problem?

---

## Lesson

### 1. Free-space loss: a radio is a light bulb

Picture a bare light bulb in a dark field. Stand 1 metre away — bright. Walk
to 2 metres — noticeably dimmer. Not because the bulb got weaker, but because
its light is now spread over a **bigger sphere**. The same fixed amount of
light has more surface to cover, so any one patch gets less.

A radio transmitter does exactly this. It pours out a fixed amount of power.
That power spreads outward over an ever-growing sphere. Your antenna is a
small fixed patch on that sphere. The farther out you are, the bigger the
sphere, the thinner your slice.

The surface of a sphere grows with the **square** of its radius. So the power
landing on your antenna drops like **1 / distance²**. That's the whole idea.
The famous **Friis equation** is just this sentence written out in symbols —
nothing more mysterious than "spread over a bigger ball."

```
   FREE-SPACE SPREADING — power thins out as the sphere grows

                      . - - - - - - - .
                  .                       .
               .        . - - - - .          .
             .       .               .          .
            .      .     ( ((  T  )) )    .       .     T = transmitter
             .       .               .          .       rings = wavefronts
               .        ' - - - - '          .          spreading outward
                  .                       .
                      ' - - - - - - - '

   close in:  rings packed tight  -> lots of power per patch  (LOUD)
   far out:   rings spread thin   -> little power per patch    (faint)

   power  ~  1 / distance^2
```

**Put a number on it (using Module 01's dB).** "Drops like 1/distance²" turns
into a clean dB rule. Doubling the distance quarters the power, and a quarter
of the power is **−6 dB** (remember from Module 01: halving power is −3 dB, so
quartering is −3 + −3 = −6 dB).

> **Tiny example.** A signal you hear at 1 km. Walk out to 2 km — you doubled
> the distance, so it's about **−6 dB** weaker. Out to 4 km — doubled again,
> another −6 dB, so −12 dB total versus the 1 km reading. Same arithmetic as
> the dimming bulb.

This is the *teachable* part of propagation. It's clean, it's intuitive, and
it's the well-behaved baseline. The rest of this module is about everything
that messes it up.

> **Use it, don't derive it.** You never need to write out Friis. You need to
> *know*: open air, signal fades smoothly with distance, −6 dB per doubling.

---

### 2. Multipath: the villain

Free-space loss assumes the signal travels straight to you and that's it. Out
in the real world, that's a fantasy. Radio bounces — off the ground, off
walls, off trees, off cars, off the side of a building.

So your antenna doesn't hear *one* signal. It hears the **direct wave** plus a
bunch of **delayed echoes** — the same signal arriving a moment later, having
taken a longer, bounced path. This is **multipath**: many paths, one
transmitter.

Here's the nasty bit. The echoes don't just add noise. Depending on the exact
timing, a bounced copy can **line up** with the direct wave and make it
stronger — or it can arrive **out of step** and partly *cancel* it. Two waves
meeting crest-to-trough subtract. So the strength you measure becomes
**unreliable**: move a metre and it can swing wildly.

For *direction-finding* this is worse than unreliable — it's dangerous. If a
bounce off a wall is strong enough, your directional antenna can peak toward
**the wall**, not the transmitter. The gear confidently points the wrong way.
**This is the number-one reason real bearings go bad.**

```
   MULTIPATH — direct wave plus a ground bounce arrive together

        T (transmitter)
         \
          \   direct path  (shorter, arrives first)
           \
            \_______________________________  A (antenna)
            /                                 /
           /                                 /
          /   bounced path                  /
         /    (longer, arrives late)       /
   _____/_________________________________/______  ground
            \                            /
             \  reflection off ground   /
              \________________________/

   At A:   direct + echo.
           in step  -> they ADD     (signal boosted)
           out of step -> they FIGHT (signal partly CANCELLED)
```

The simulator (`channel.py`) models multipath with **three** named recipes.
You don't need their math — just what each one *is*:

- **Two-ray ground.** The simplest realistic bounce: the direct ray **plus one
  ground reflection.** Because the ground bounce often arrives flipped, it can
  partly cancel the direct ray — and at certain exact distances the two nearly
  null each other out, creating **"dead zones"** where the signal mysteriously
  craters even though you got *closer*. (In code: `TwoRayGroundChannel`.)

- **FIR / tapped-delay.** A general-purpose echo recipe: take the original
  signal, then add several **scaled, delayed copies** of it (each "tap" is one
  echo — how strong, how late). Sum them up and you've built an arbitrary
  multipath environment. It's the catch-all when one ground bounce isn't
  enough to describe a cluttered site. (In code: `MultipathFIRChannel`.)

- **Log-normal shadowing.** Big slow obstacles — a hill, a building, a
  tree-line — block the path and the signal **fades**. The simulator models
  this as a slow random **wobble in dB**: sometimes the path is a few dB down,
  sometimes a few dB up, drawn at random per scenario. "Slow fading" because it
  changes as you move between big obstacles, not sample-to-sample. (In code:
  `LogNormalShadowing`.)

> **Name-drop only.** Two-ray, FIR, and log-normal shadowing are tools the
> simulator *uses*. You only need the one-line intuition for each. Nobody is
> deriving impulse responses here.

---

### 3. Front/back (F/B) ratio: is the peak trustable?

Recall Module 04: a directional antenna (a Yagi) is loud toward where it
points and quiet behind it. A good bearing needs the **wanted peak to clearly
beat everything else**. If the front and the back are nearly the same loudness,
you've got mush — you can't tell which way the signal really came from.

The **front/back (F/B) ratio**, in dB, measures exactly that: how much louder
the **front** (pointed at the emitter) is than the **back/off-axis**. Big F/B
= a clean, sharp, trustable peak. Small F/B = flat, ambiguous, don't trust it.

```
   ANTENNA PATTERN — big front lobe, small back lobe

                      FRONT (toward emitter)
                          ___
                       .-'   '-.
                      /         \
                     |    BIG    |      <- main lobe: where the antenna hears best
                     |   LOBE    |
            .         \         /         .
           ( o )-------+- ANT -+-----------   boresight axis
            '          /       \          '
                      | small  |          back lobe: small leak behind
                       '-.___.-'
                       back lobe
                          |
                      BACK (away)

   F/B ratio (dB) = front lobe level  -  back lobe level
                  = "how much louder is the right way than the wrong way"

   F/B big  (e.g. 15 dB) -> sharp, trustable peak
   F/B small(e.g.  2 dB) -> mush, refuse to report
```

This ties **straight** to Module 04's gate. The L1 estimator
(`packages/rfmesh-dsp/src/rfmesh_dsp/l1.py`) has a **6 dB prominence gate**
(`peak_prominence_db_min = 6.0`): it compares the peak heading's RSSI against
the median of the off-peak headings. If the peak doesn't beat the background by
at least 6 dB, the estimator returns **`None`** — it refuses to report a
bearing rather than fabricate one. Front/back ratio is essentially this gate's
question asked out loud: *is the front clearly winning?*

Hold that 6 dB number. The whole next section is three real masts measured
against it.

---

### 4. Phase C — the real bench test (the payoff)

Here's the story the whole course has been building toward.

Every layer of this system rests on one physics assumption: *point our gear at
a known transmitter, and we get a clean peak in the right direction.* That's
the bet. **Phase C** is the experiment that finally tested the bet against real
radio.

And here's the kicker: in the **prior project, Phase C had never been run.**
Not once. The field test kept slipping on hardware bring-up. The system's most
load-bearing assumption sat *untested* the entire time.

On the evening of 2026-05-17, it finally ran — one operator, an ATK-10 Yagi, an
RTL-SDR V4, a laptop, a compass, a map, and three real GSM cell masts,
hand-rotated through eight headings. Three results came back. All three honest.

#### Mast A (~650 m) — FAIL: multipath dominance

Too close, too cluttered. At 650 m the antenna is sitting deep in the
multipath soup of a rural mast — ground reflections, building scatter,
tree-line forward-scatter all smearing the response flat. The **front/back
ratio came out at just 1.96 dB** — well under the 6 dB gate. The system
**correctly refused to report a bearing** (returned `None`).

That's not a bug. That's the documented short-range failure mode working as
designed: sub-1 km is the no-fly zone for this kind of DF. And there was a
bonus — it proved the simulator's bounce models were *realistic*, because the
simulator had been producing exactly this kind of flat, refused response at
short range all along.

> **Lesson:** too close + cluttered = honest refusal. *We don't lie when
> physics says we can't see.*

#### Mast B (~2.2 km) — FAIL: co-channel interference (the scary one)

This one is the most important failure in the whole project.

The front/back ratio was a **healthy 8.5 dB** — that would *pass* the 6 dB
gate. The peak was sharp. Everything looked great. But the peak pointed the
**wrong way**. A *stronger* transmitter about **5 km away, on the exact same
frequency**, dominated the reading and dragged the peak toward itself.

That shared-frequency clash is **co-channel interference** — "co-channel"
means another emitter sitting on the very same channel. GSM bands get reused
across a region by multiple operators, so a closer, louder transmitter on your
target's frequency simply wins.

The result is the **scariest mode a DF system has: a confident wrong answer.**
The gate was happy. The peak was clean. And it was pointing at the wrong tower.
A single node, trusting only signal strength, would have reported a crisp,
high-confidence, completely wrong bearing.

> **Lesson:** a sharp peak is *necessary* but not *sufficient*. You also have
> to make sure you locked onto the right emitter.

#### Mast C (~3 km) — PASS

Different site, deliberately chosen: a clear line of sight, ~3 km out, on an
**isolated** carrier at the quiet edge of the band (958.7 MHz, far from the
crowded 935–940 MHz GSM core). This is squarely inside the system's **2–5 km
design range**.

Result: **front/back ratio of 14.9 dB** — way over the gate — and a peak just
**~9° off** the true bearing on the coarse 45° sweep. A later finer sweep
tightened that to about **~1.4°**. Clean, unimodal, exactly the textbook
parabolic peak the system was designed to find.

Mast C became the project's **trusted real-world reference point**, captured as
`scenarios/mast_c_reference.yaml` so the simulator can be checked against
measured reality forever after.

```
   THE THREE MASTS, against the 6 dB gate

   F/B (dB)
    15 |                                        ##  Mast C  ~3 km
       |                                        ##  14.9 dB  PASS
       |                                        ##  peak right (~9deg, ->1.4deg)
       |                                        ##
    10 |                                        ##
       |              ##  Mast B  ~2.2 km       ##
   - - | - - - - - - -##- - - - - - - - - - - - ## - - -  6 dB GATE
     6 |              ##  8.5 dB  "passes" gate  ##
       |              ##  BUT peak points WRONG  ##   <- confident wrong bearing
       |   ##         ##                         ##      (co-channel interferer)
       |   ## 1.96 dB ##                         ##
       |   ## Mast A   ##                        ##
       |   ## ~650 m   ##                        ##
     0 +---##----------##-------------------------##------
          FAIL          FAIL                       PASS
        (refused)    (mis-bear)                 (trusted)
```

Read those three together and you've got the whole point of the system's
honesty design:

- **A** = refused, correctly. Too close.
- **B** = passed the gate but lied about direction. The gate alone isn't enough.
- **C** = passed honestly. In the design envelope, isolated frequency.

---

### 5. The fixes the bench taught

Two new disciplines came directly out of that night.

**(1) Reference-emitter isolation check.** Mast B taught this one. Before you
trust *any* frequency, confirm the wanted direction is at least about **6 dB
above the off-axis directions** at that frequency. In practice: point the
antenna *away* from the expected bearing and re-read the same frequency. If
the off-axis reading is within a few dB of the on-axis one, the channel is
contaminated by a co-channel interferer — pick a different frequency or tower.
This is now `§2.bis` in the bench checklist
(`docs/hardware/phase-c-bench-checklist.md`). It exists so you never lock onto
an interferer like Mast B.

**(2) Five pre-listed failure modes.** So that a FAIL means *"diagnose,"* not
*"panic."* When the sweep comes back bad, you walk this list instead of
guessing (`INHERITED_CONTEXT.md` §3.1.1, bench checklist §5):

1. **Multipath dominance** — bounces flatten or pull the peak off-axis.
   *(Too-close / cluttered site — this was Mast A.)*
2. **Polarization mismatch** — antenna oriented the wrong way relative to the
   signal's polarization (e.g. horizontal when it should be vertical); the
   antenna goes nearly "deaf."
3. **Antenna-pattern deformation from the mounting** — metal too close to the
   antenna (like the SDR's case next to the boom) warps the radiation pattern,
   making the peak lopsided.
4. **Servo backlash / cogging** — mechanical slop in the rotator: the angle the
   firmware *reports* isn't quite the angle the antenna is *actually* pointing.
5. **RF-chain / gain problem** — a cable, connector, or gain setting issue means
   the receiver just isn't seeing the emitter at usable SNR.

> The first three change *what the system can promise* (they're about the
> physics and the antenna). The last two have known mechanical/electrical
> fixes. Either way: a named list turns a scary FAIL into a checklist.

---

### 6. The takeaway

The hard part of this system was never the math. Free-space loss is a dimming
light bulb. The bearing peak-fit is a parabola. The real enemies are
**multipath** (bouncing radio that lies about strength and direction) and
**interference** (a louder stranger on your frequency).

That's *why* the system is built the way it is — and every honest design choice
traces back to what these masts showed:

- **Refuse when unclear** (the 6 dB gate) — because of Mast A.
- **Check isolation before trusting a frequency** — because of Mast B.
- **Label confidence honestly and cross-check multiple nodes** — because one
  node can be confidently wrong (Mast B again); two disagreeing nodes catch it.
- **L2 spatial separation** (precision DF that can pull apart co-channel
  sources) — because amplitude alone can't separate two emitters on one
  frequency.

A system that says *"I can't see this one"* beats a system that says *"it's
that way!"* and is wrong. Mast B is the whole argument in one data point.

---

## Where this lives in the code

- **Propagation & multipath models** —
  `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/channel.py`
  - `FreeSpaceChannel` — the Friis / inverse-square fade (the light bulb).
  - `TwoRayGroundChannel` — direct ray + one ground bounce ("dead zones").
  - `MultipathFIRChannel` — the general tapped-delay echo recipe.
  - `LogNormalShadowing` — the random slow-fade dB wobble.
  - `CompositeChannel` — chains them together for a realistic site.
- **The 6 dB prominence gate (F/B in action)** —
  `packages/rfmesh-dsp/src/rfmesh_dsp/l1.py` (`peak_prominence_db_min = 6.0`;
  the gate returns `None` and sets a refusal reason when the peak doesn't beat
  the floor by 6 dB).
- **The Phase C bench results (Mast A/B/C story)** —
  `docs/phase-c-report/findings.md` and `docs/phase-c-report/phase-c-report.md`.
- **The isolation check (§2.bis) and the five failure modes (§5)** —
  `docs/hardware/phase-c-bench-checklist.md`.
- **The trusted real-world reference scenario** —
  `scenarios/mast_c_reference.yaml`.
- **The pre-enumerated failure modes (origin)** — `INHERITED_CONTEXT.md` §3.1.1.

---

## Click test

Check yourself. Answers below — no peeking first.

1. A signal reads −40 dB(relative) at 1 km. Roughly what does it read at 4 km
   in free space, and why?
2. Explain in one sentence how an echo can make a signal *weaker*, not just
   noisier.
3. Which of the three simulator models creates "dead zones," and what causes
   them?
4. Mast B had a **healthy 8.5 dB** front/back ratio — better than the gate.
   Why was it still a FAIL?
5. Why did Mast A's 1.96 dB result count as the system *working correctly*,
   not failing?
6. You're about to trust a frequency for a bearing. What one check do you run
   first, and what are you looking for?
7. The gear reports FAIL. Name two of the five failure modes you'd check, and
   one diagnostic for each.

<details>
<summary>Answers</summary>

1. About **−52 dB**. From 1 km to 4 km is two doublings (1→2→4). Each doubling
   of distance is about −6 dB (quarter power), so −6 + −6 = **−12 dB** on top
   of the original −40 dB.
2. The echo arrives **out of step** with the direct wave — crest meeting
   trough — so the two partly cancel instead of adding.
3. **Two-ray ground.** The single ground-bounce ray often arrives flipped, and
   at certain exact distances it nearly cancels the direct ray, craters the
   signal, and creates a dead zone.
4. The peak was sharp but pointed at the **wrong tower** — a stronger
   transmitter ~5 km away on the **same frequency (co-channel interference)**
   dominated. A clean peak doesn't guarantee the *right* emitter. That's the
   "confident wrong bearing."
5. At 650 m the antenna is inside heavy near-field multipath; physics says you
   *can't* get a clean bearing there. The system **refused** (F/B below the
   6 dB gate) instead of fabricating one — exactly the honest behavior it's
   designed for.
6. The **reference-emitter isolation check**: point off-axis and re-read the
   same frequency. You want the off-axis reading at least ~6 dB **below** the
   on-axis reading. If it's within a few dB, a co-channel interferer is
   contaminating the channel — pick another frequency/tower.
7. Any two of: **multipath dominance** (move the rig 5–10 m; true peak tracks,
   bounce doesn't) · **polarization mismatch** (rotate antenna 90°; if it gets
   *louder*, polarization was wrong) · **antenna-pattern deformation** (move the
   SDR off the boom with a longer cable; asymmetry should shrink) · **servo
   backlash/cogging** (sweep both directions, compare reported vs actual peak)
   · **RF-chain/gain** (swap in a known-good antenna; if RSSI rises, the
   antenna/connection was the problem).

</details>
