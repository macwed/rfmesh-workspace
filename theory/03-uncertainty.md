# Module 03 — Uncertainty: sigma, the bell curve, and honest weighting

*Builds on modules 00–02. This one is the heart of the project. The team calls sigma "the connective tissue" — once you get this, the rest of rfmesh clicks into place.*

---

## What you'll be able to do after this

- Say what **sigma (σ)** is in one sentence: the typical size of the scatter in a measurement.
- Read a bell curve and use the **68% / 95% rule** without doing any math.
- Read a bearing like **"40° ± 3°"** and know exactly what the ± means.
- Explain **inverse-variance weighting** — why a precise node counts way more than a sloppy one, and why we square.
- Explain why an **honest sigma** is the single most important number a node reports, and what goes wrong when a node lies about it.

---

## Questions this answers

- What is sigma? What is "standard deviation"? What is "variance"?
- What does "spread" mean?
- What is the bell curve / the Gaussian?
- What's the 68% / 95% rule?
- What does "40° ± 3°" actually mean?
- What is inverse-variance weighting? Why `w = 1/σ²`? Why squared?
- What is "honest sigma" and why does the whole system depend on it?
- How does sigma connect to the fuzzy wedge (module 02) and the uncertainty ellipse?

---

## Lesson

### (a) Sigma is just spread

Measure the same thing several times and you will not get the same answer twice. The results scatter. **Sigma is the typical size of that scatter.** That's it.

Tiny example. You measure a bearing three times and get:

```
8        10        12
```

The average is **10** (8 + 10 + 12 = 30, divided by 3).

Now look at how far each measurement misses the average:

- 8 misses by 2
- 10 misses by 0
- 12 misses by 2

The typical miss is about **2**. So **σ ≈ 2**.

That's the whole idea. Sigma is a single number that says "measurements usually land about this far from the center."

Picture it as dots scattered around a center, with a band one sigma wide on each side:

```
              center (avg = 10)
                  |
        .    .  . | .  .    .
      .   .  . .  |  . .  .   .
   ---[------------+------------]---
      8            10           12
      |<-- 1σ -->|<-- 1σ -->|
        (about 2)   (about 2)
```

Most dots land inside the band. A few stragglers land outside it. The band is **±σ**.

> **One quick word: "variance."** Variance is just sigma squared (σ²). Same idea, squared. If σ = 2, then variance = 4. We mention it because the weighting rule later uses it — but you never have to compute it by hand. When you see "variance," think "sigma, squared." Move on.

A **small sigma** means tight, repeatable, trustworthy measurements. A **large sigma** means sloppy, scattered, less trustworthy. Sigma is the system's word for "how sure am I?"

---

### (b) The bell curve (the Gaussian)

When random errors pile up, they don't pile up evenly. They pile up in a **bell shape**: most results land near the average, and results far from the average get rarer and rarer the further out you go.

This shape has a fancy name — the **Gaussian**, or the **normal distribution** — but you don't need the math. You need the picture and one fact.

Here's the real shape (Wikipedia's normal-distribution figure):

**Image:** https://en.wikipedia.org/wiki/Normal_distribution#/media/File:Standard_deviation_diagram_micro.svg

And a tiny ASCII version of the same bell:

```
                  ___
                 /   \
                /     \         <- most results pile up near the center
               /       \
              /         \
            _/           \_
          _/               \_   <- rare results far out in the tails
   ______/                   \______
   -3σ   -2σ   -1σ   avg   +1σ   +2σ   +3σ
```

**The one fact to memorize:**

- About **68%** of results land within **±1σ** of the average.
- About **95%** land within **±2σ**.

So if a measurement has σ = 2 and an average of 10:

- ~68% of the time the result lands between **8 and 12** (10 ± 2).
- ~95% of the time it lands between **6 and 14** (10 ± 4).

A result way out at 20 — that's five sigma away — would be extremely rare. The bell curve tells you that far-out results are not impossible, just unlikely. That's the honest way to talk about error: not "the answer is exactly 10," but "the answer is around 10, and here's how tightly it clusters."

---

### (c) Sigma on a bearing

Now connect it to rfmesh. Every bearing a node reports comes with **its own sigma, in degrees.**

A node doesn't just say "the emitter is at 40°." It says:

```
40° ± 3°
```

That **± 3°** *is the sigma.* It means: "My best guess is 40°, and my typical scatter is about 3°." Combine that with the bell-curve rule and you can read it precisely:

- ~68% chance the true bearing is between **37° and 43°** (40 ± 3).
- ~95% chance it's between **34° and 46°** (40 ± 6).

Different nodes report different sigmas, because different nodes are built differently:

| Node | Typical sigma | What it means |
|---|---|---|
| Cheap L1 node (RSSI sweep) | ±8° | coarse, wide scatter |
| Precise L2 node (MUSIC array) | ±1.5° | sharp, tight scatter |

**This is the direct link to module 02.** Remember the fuzzy wedge — the cone of "the emitter is somewhere in here"? **Sigma is exactly the half-width of that wedge.** A ±3° bearing is a wedge that fans out 3° to each side of the 40° centerline. A ±8° node draws a fat wedge; a ±1.5° node draws a thin one. The number on the bearing and the fatness of the wedge are the same fact, said two ways.

```
   ±8° node (fat wedge)          ±1.5° node (thin wedge)

        \         /                      \   /
         \       /                        \ /
          \     /                          |
           \   /                           |
            \ /                            |
         node A                         node B
```

---

### (d) Inverse-variance weighting — the key move

Here's the problem the fusion server has to solve. Several nodes each report a bearing. Some are precise, some are sloppy. How do you combine them into one answer **without letting the sloppy ones drag it around?**

The rule is simple and it's the engine of the whole project:

> **Weight each estimate by `w = 1 / σ²`** (one divided by sigma squared).

In plain words: **trust the precise ones more, and trust them a LOT more.**

Let's see it with numbers. Two bearings come in:

- A **tight** bearing, σ = 1. Its weight is `1 / 1² = 1 / 1 = 1`.
- A **sloppy** bearing, σ = 10. Its weight is `1 / 10² = 1 / 100 = 0.01`.

Compare the weights: **1 versus 0.01.** The tight bearing counts **100 times more** than the sloppy one. The sloppy bearing still gets a tiny vote — it's not thrown away — but it can't bully the answer.

**Why squared?** Because squaring punishes sloppiness hard. If we only used `1/σ` (not squared), the tight bearing would count just 10× more, not 100×. Squaring makes the gap between "kind of unsure" and "really unsure" much bigger — which is exactly what we want. A measurement that's 10× sloppier shouldn't count a little less; it should count *drastically* less. Squaring delivers that.

Picture two arrows pulling on a point, like two ropes. The thickness of each rope is its weight:

```
   tight bearing (σ=1, weight 1)
   ============================>  •   <thick rope, pulls HARD
                                  ^
                                  |  the combined point lands
                                  |  almost where the tight one wants
   - - - - - - - - - - - - - - -> •
   sloppy bearing (σ=10, weight 0.01)
   (thin thread, barely pulls)
```

The thick rope wins the tug-of-war by a mile. The combined answer sits right next to the precise node's bearing, with only a faint nudge from the sloppy one.

**This single rule is what lets cheap and expensive nodes mix correctly.** A €30 RTL-SDR node and a precise coherent-array node can be on the same mesh, both feeding the same fusion server, and each one automatically contributes *exactly as much as it deserves* — because each one is weighted by its own honest sigma. No special cases, no "ignore the cheap nodes." Just `1/σ²`. (This is the math inside the fusion server — module 07.)

---

### (e) "Honest sigma" — why it's everything

Now the punchline. The sigma a node reports gets used **three different ways**:

1. **As the fusion weight** — `1/σ²`, the tug-of-war strength we just saw.
2. **To size the uncertainty ellipse** — the oval on the operator's map that says "the emitter is somewhere in this region." Tight sigmas → small, confident oval. Loose sigmas → big, honest oval. (Sigma feeds the ellipse the same way it fed the wedge in module 02 — wedge for one bearing, ellipse for the combined fix.)
3. **As the basis of trust** on the operator's screen.

All three depend on sigma being **truthful.**

So picture a node that **lies** — it reports σ = 0.5° when its real scatter is 8°. It's claiming to be far more sure than it actually is. Look what that does to `1/σ²`:

- Honest weight (real σ = 8): `1 / 64 ≈ 0.016` — a small vote, correctly.
- Lying weight (claimed σ = 0.5): `1 / 0.25 = 4` — a *huge* vote.

That liar now **out-votes every honest node combined**, and it drags the fused position toward its own wrong bearing. One dishonest sigma **poisons the entire answer.** And because the ellipse is sized from those same sigmas, the map shows a tight, confident oval in the *wrong place* — confidently wrong, which is the worst kind of wrong.

The flip side matters too: an RF/EW expert watching the demo spots this **instantly.** A bearing claiming ±0.5° from a cheap single-antenna node is not believable, and an expert audience knows it in seconds. Dishonest sigma isn't just a math bug — it's a credibility own-goal in front of the exact people you're trying to convince.

That's why the rule across the whole codebase is: **every estimator must report a sigma that genuinely matches its real scatter.** Not a hopeful constant, not a number that makes the demo look good — the real spread. The system even has automated tests that *check* this honesty by simulation — running an estimator hundreds of times and confirming its claimed sigma matches its actual scatter (those are the **Monte-Carlo honesty tests**, covered in module 08).

Honest sigma is the connective tissue. Get it right and cheap and expensive nodes fuse beautifully. Get it wrong anywhere and everything downstream — the weight, the ellipse, the operator's trust — quietly breaks.

---

## Where this lives in the code

- **The load-bearing field.** `BearingReport.azimuth_sigma_deg` in
  `packages/rfmesh-contracts/src/rfmesh_contracts/messages.py` (around line 120). Its own description in the code says it plainly: the 1-sigma uncertainty of the azimuth, *"THE weight the fusion solver uses (inverse-variance),"* and it *"must be an honest estimate ... never a hopeful constant."*

- **The honesty tests.** `packages/rfmesh-dsp/tests/test_sigma_honesty.py` runs each estimator hundreds of times (at SNR 10, 20, and 30 dB) and asserts that the **claimed** sigma matches the **actual** scatter to within a ±20% band. If an estimator's sigma is dishonest, this test fails on purpose — and the rule is *don't widen the test, fix the estimator.*

- **The project rule.** Invariant **B2 — "Honest sigma on every `BearingReport`"** in `AGENTS.md` (§1) calls this *"the single most load-bearing technical invariant in the project."* `ARCHITECTURE.md` (§1) makes the same point: an 8°-σ L1 bearing and a 1.5°-σ L2 bearing *"differ only in their `azimuth_sigma_deg`,"* and fusion uses that sigma as the inverse weight automatically.

---

## Click test

You've got it when you can answer these without peeking:

1. You measure {9, 11, 13}. What's the average, and roughly what's σ?
   *(Average 11; misses are 2, 0, 2; so σ ≈ 2.)*

2. A node reports **50° ± 4°**. Between what two angles does the true bearing land about 95% of the time?
   *(±2σ = ±8°, so 42° to 58°.)*

3. Node A has σ = 2°, node B has σ = 4°. What's each one's fusion weight, and how many times more does A count than B?
   *(A: 1/4 = 0.25. B: 1/16 = 0.0625. A counts 4× more.)*

4. Why do we use `1/σ²` and not just `1/σ`?
   *(Squaring punishes sloppiness much harder, so a 10× sloppier node counts 100× less, not just 10× less — which is what we want.)*

5. A cheap node secretly reports σ = 0.5° when its real scatter is 8°. In one sentence, what damage does it do?
   *(Its weight balloons to 1/0.25 = 4, so it out-votes the honest nodes and drags the fused position — and the ellipse — confidently to the wrong place.)*

6. Name the three things a reported sigma is used for.
   *(The fusion weight, the size of the uncertainty ellipse, and the operator's basis for trust.)*

7. How does sigma connect to module 02's wedge?
   *(Sigma is the half-width of the fuzzy wedge — the ±degrees fanning out from the centerline.)*
