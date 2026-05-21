# Module 01 — Signals Foundation: IQ, decibels, SNR, RSSI

## What you'll be able to do after this

After this lesson you will be able to:

- Say what a radio "sample" is, and what one **IQ sample** stores.
- Explain the spinning-arrow (**phasor**) picture, and what **I** and **Q** are.
- Tell the difference between **amplitude** and **power**, and compute power from I and Q.
- Read a **decibel (dB)** value and know roughly how big it is.
- Read the one formula that is the heartbeat of the whole system: **RSSI = 10·log10(mean(|iq|²))**.
- Explain **noise floor** and **SNR**, and why we only ever say "relative" strength — never watts or dBm.

You won't be deriving anything. The goal is to *use* these ideas and recognize them when you see them in the code.

---

## Questions this answers

- What is a radio sample?
- What is an **IQ sample**? What are **I** and **Q**?
- What is a **phasor**?
- What does **|iq|** mean? What does **|iq|²** mean?
- What does **mean()** mean?
- What's the difference between **amplitude** and **power**?
- What is a **decibel**? What does **10·log10** do? Why use logs at all?
- What is **RSSI**?
- What is the **noise floor**? What is **SNR**?
- Why do we say "relative strength," never "dBm" or "watts"?

---

## Lesson

### (a) What a radio sample is

A radio wave is wiggling through the air all the time. The receiver can't look at "all the time" at once. So it takes a quick measurement, then another, then another — many thousands of times per second.

Each one of those measurements is called a **sample**.

Think of a movie. A movie looks like smooth motion, but it's really just still photos shown fast — 24 photos a second. A radio receiver does the same thing to a radio wave: it grabs "photos" of the wave, thousands per second. Each photo is one sample.

If our receiver samples at, say, 2 million samples per second, then in one second it has taken 2 million little snapshots of the wave. That stream of snapshots is the raw material everything else is built from.

```
the wave:    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
samples:     |   |   |   |   |   |   |   |
             ^   ^   ^   ^   ^   ^   ^   ^
           each tick = one measurement = one sample
```

### (b) The spinning arrow: phasor and the IQ sample

Here is the one idea that unlocks the rest. We will use an **analogy**, not algebra.

At any single instant, a radio wave has two things going on:

1. **How strong it is** (a big wiggle or a small wiggle) — call this the **amplitude**.
2. **Where it is in its cycle** (just starting to rise? at the top? heading down?) — call this the **phase**.

A neat way to picture *both at once* is a little **spinning arrow**. Engineers call it a **phasor**.

- The **length** of the arrow = how strong the wave is (amplitude).
- The **angle** of the arrow = where it is in its spin cycle (phase).

As the wave goes through its cycle, the arrow spins around like the hand of a clock.

Now the practical problem: a computer can't store "an arrow." It stores numbers. So we store the arrow by writing down its **two shadows** on a graph:

- The **horizontal shadow** is called **I** (short for *in-phase*).
- The **vertical shadow** is called **Q** (short for *quadrature* — just a fancy word meaning "the sideways one, a quarter-turn from I").

The pair **(I, Q)** is exactly the tip of the arrow. That pair is **one IQ sample**.

So: **one IQ sample = (I, Q) = the tip of the spinning arrow at that instant.**

Tiny examples:

- Arrow pointing straight right → **(I = 1, Q = 0)**.
- Arrow rotated a quarter turn upward → **(I = 0, Q = 1)**.
- Arrow pointing down-left, half strength → maybe **(I = -0.5, Q = -0.5)**.

```
        Q (vertical shadow)
        ^
        |        . tip of arrow = (I, Q)
        |      /:
        |    /  :
        |  /    : <- Q is the height of the tip
        |/      :
  ------+-------+----------> I (horizontal shadow)
        |       ^
                I is how far right the tip is

  The arrow spins; (I, Q) is just where its tip is right now.
```

Picture from Wikipedia showing the same idea (the arrow and its I and Q parts):
https://en.wikipedia.org/wiki/In-phase_and_quadrature_components

That's the whole concept. Every "IQ sample" you'll ever see in this project is just a recorded arrow-tip: two numbers, I and Q.

> **Greenhorn check:** "So I is one number and Q is another number, and together they're one sample?" — Yes. Two numbers, one sample. The two numbers are the arrow's shadows.

### (c) Amplitude vs power

The arrow's **length** is the **amplitude** — how strong the wave is.

But often we don't care about length directly; we care about **power** — how much "oomph" the signal carries. Power is *length squared*:

> **power = (arrow length)² = I² + Q²**

We write the arrow's length as **|iq|** (the two bars mean "length of"). So:

> **|iq|** = the arrow's length (amplitude)
> **|iq|²** = |iq| × |iq| = I² + Q² = the **power**

**Why squared?** Because real-world "strength you feel" almost always grows with the *square* of amplitude.

- Double the brightness of a bulb's filament swing and the light power goes up about 4×.
- Double the loudness swing of a speaker and the sound power goes up about 4×.

Power behaves the same way. A signal whose arrow is twice as long isn't twice as powerful — it's **four times** as powerful (2² = 4). So when we want "how much energy is really here," we use I² + Q², not just the length.

Tiny example:

- Arrow at (I = 3, Q = 4). Length |iq| = 5 (a 3-4-5 triangle). Power = |iq|² = 3² + 4² = 9 + 16 = **25**.

### (d) Averaging power over a block

One sample is a single snapshot — it bounces around. To get a steady, trustworthy number, we look at a whole **block** of samples (say, a few thousand in a row) and take the **average** of their powers.

**mean()** is just the math word for **average**: add them all up, divide by how many there are.

> **mean(|iq|²)** = average power over the block = "the typical power during this block."

Tiny example: three samples with powers 10, 20, and 30.
mean = (10 + 20 + 30) ÷ 3 = **20**. That 20 is the typical power for that block.

This averaging is what turns jittery individual snapshots into one solid "how strong is it right now" number.

### (e) Decibels (dB)

Radio powers are wildly spread out. A strong nearby signal can be **billions** of times more powerful than a faint distant one. Writing those numbers out (0.000000004 vs 7.0) is miserable and unreadable.

The fix is the **decibel (dB)**. A decibel is a **log scale for ratios**. The magic move is:

> **dB value = 10 · log10(power)**

Log scales turn **multiplying** into **adding**, which squashes those billions-to-one ranges into small, readable numbers.

You do **not** need to compute logs by hand. Just memorize **three anchors**:

| Change in power | Change in dB |
|---|---|
| same power (×1)   | **0 dB** |
| about double (×2) | **+3 dB** |
| ten times (×10)   | **+10 dB** |

And they stack by adding:

- ×100 power = ×10 then ×10 = +10 dB + 10 dB = **+20 dB**.
- ×1000 power = **+30 dB**.
- half the power (÷2) = **−3 dB**. A tenth the power (÷10) = **−10 dB**.

Tiny example: power goes from 1 up to 100. That's ×100, so **10·log10(100) = +20 dB**. Done — no calculator needed once you know the anchors.

```
  power:    1     2      10      100      1000     1,000,000
  dB:       0    +3     +10     +20      +30        +60

  Multiply on the top row  =  Add on the bottom row.
  That's the whole point of decibels.
```

So "+10 dB" always means "ten times the power," whether you started big or small. That consistency is why radio people live in dB.

### (f) RSSI — putting it together

Now we can read the one formula that drives everything downstream.

**RSSI** stands for *Received Signal Strength Indicator* — plain English: "how strong is the signal right now."

We build it from the pieces you already have:

1. Take a block of IQ samples.
2. Compute each sample's power, **|iq|²**.
3. Average them: **mean(|iq|²)** — the typical power.
4. Put it on the dB scale: **10·log10(...)**.

> **RSSI = 10 · log10( mean(|iq|²) )**

That's it. RSSI is just "average power of this block, written in decibels." It is a single, readable number for "how loud is the radio right now," and it is the **heartbeat of L1** — the detection layer that finds bearings by sweeping an antenna and watching where RSSI peaks. (That comes in a later module; for now, just know this formula is the thing it leans on.)

> **Greenhorn check:** "So RSSI is one number per block of samples?" — Exactly. Feed in a block, get one strength number out.

### (g) Noise floor and SNR

There is always background hiss. Even with no real signal arriving, the receiver's own electronics and the environment produce a faint random crackle — like the static between radio stations, or the gentle hiss of an empty room when you really listen.

The **noise floor** is the dB level of that hiss when no real signal is present. It's the "floor" your signal has to rise above to be noticed.

**SNR** stands for *Signal-to-Noise Ratio*. It answers: **how many dB does the signal stick up above the noise floor?**

> **SNR = (signal level in dB) − (noise floor in dB)**

Tiny example:

- Signal sits at **−30 dB**.
- Noise floor sits at **−50 dB**.
- SNR = (−30) − (−50) = **20 dB**.

That signal pokes 20 dB above the hiss — very easy to measure cleanly.

```
   dB
    0 |
      |
  -30 |======== signal  ----+
      |                     | SNR = 20 dB (the gap)
  -50 |~~~~~~~~ noise floor +   <- hiss level, no real signal
      |
      ~~~~~~~~~~~~~~~~~~~~~~~~ (random hiss)
```

- **Big SNR** (signal far above the hiss) → easy, confident measurement.
- **Small SNR** (signal barely peeking over the hiss) → the signal is half-drowned, and every measurement gets shaky.

This matters later: the *honesty* of a bearing depends on SNR. A bearing measured at high SNR deserves trust; one measured at low SNR must say so.

### IMPORTANT honesty note — relative, not absolute

You will notice every number above is **relative**: dB above *our own* noise floor, not "so many watts" or "so many dBm."

That's deliberate. The radios this project uses (RTL-SDR, HackRF, bladeRF, Pluto+) are **not power-calibrated** — none of them was factory-tuned to report true absolute power. So if we printed "−42 dBm" on screen, we'd be making up precision we don't have. To an expert RF audience, that's an instant credibility loss.

The rule, written into the project's contracts: **there is no `dBm` field anywhere.** We only ever claim:

- **relative** signal strength (RSSI in dBFS — "dB below full scale," a relative reference), and
- **SNR above our own noise-floor estimate.**

Honest and relative beats impressive and fake. That principle lives in **INTERFACES.md §0** ("Power and SNR... No `dBm` field anywhere").

---

## Where this lives in the code

The formulas in this lesson are real functions you can open and read.

- **`packages/rfmesh-dsp/src/rfmesh_dsp/rssi.py`**
  - **`compute_rssi_dbfs`** (around line 29) — this *is* the RSSI formula from section (f). The core line is:
    ```python
    power = float(np.mean(np.abs(iq) ** 2))      # mean(|iq|²)  -> typical power
    rssi_dbfs = 10.0 * float(np.log10(power + EPSILON))   # 10·log10(...) -> dB
    ```
    Notice `np.abs(iq)` is **|iq|** (arrow length), `** 2` makes it **|iq|²** (power), `np.mean` is **mean()**, and `10.0 * np.log10(...)` is the **dB** step. Every piece of section (f) is right there.
  - **`compute_noise_floor_dbfs`** — estimates the **noise floor** from section (g). It looks at the spectrum, throws away the strongest bins (those are real signals, not hiss), and takes the middle (median) of what's left.
  - **`compute_snr_db`** — computes **SNR** from section (g): the signal level minus the noise level, in dB.

- **The `+ EPSILON` trick.** You'll see `power + EPSILON` in the code. `EPSILON` is a tiny number (`1e-12`, defined in `packages/rfmesh-dsp/src/rfmesh_dsp/constants.py`). It's there for one reason: `log10(0)` blows up to negative infinity, and a perfectly silent block has power 0. Adding a microscopic crumb keeps the math from exploding without changing any real reading.

- **The dB-honesty rule** ("no `dBm` field anywhere," relative-only) is written in **`INTERFACES.md` §0**, under "Power and SNR."

You don't need to understand the rest of `rssi.py` yet (the spectrum-based functions use tools from a later module). Just recognize the four ideas — `|iq|`, `mean()`, `10·log10`, and noise floor / SNR — when you see them.

---

## Click test

Quick self-check. Answers are right below — try each before peeking.

1. A receiver takes 2 million measurements per second. What do we call **one** of those measurements?
2. An IQ sample is the pair (I, Q). In the spinning-arrow picture, what is (I, Q)?
3. An arrow points to (I = 0, Q = 1). Which way is it pointing, and how long is it?
4. A sample is (I = 6, Q = 8). What is **|iq|** (the length)? What is **|iq|²** (the power)?
5. Three samples have powers 4, 8, 12. What is **mean(|iq|²)**?
6. The power of a signal goes up ×10. How many dB is that? What about ×1000?
7. Write the RSSI formula in words and in symbols.
8. A signal is at −25 dB; the noise floor is at −45 dB. What is the SNR?
9. Why do we never print "dBm" or "watts" in this system?

**Answers:**

1. One **sample**.
2. The **tip of the arrow** — its horizontal shadow (I) and vertical shadow (Q).
3. Straight **up** (a quarter turn), length **1**.
4. |iq| = **10** (a 6-8-10 triangle), |iq|² = 36 + 64 = **100**.
5. (4 + 8 + 12) ÷ 3 = **8**.
6. ×10 = **+10 dB**. ×1000 = +10 +10 +10 = **+30 dB**.
7. In words: average power of a block of samples, on the dB scale. In symbols: **RSSI = 10·log10(mean(|iq|²))**.
8. SNR = (−25) − (−45) = **20 dB**.
9. Because our radios are **not power-calibrated** — we don't actually know the true absolute power, so claiming dBm would be fake precision. We report **relative** strength and **SNR above our own noise floor** instead. (See INTERFACES.md §0.)
