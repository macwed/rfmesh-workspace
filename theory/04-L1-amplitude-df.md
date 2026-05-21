# Module 04 — L1: Amplitude-Comparison Direction Finding

*Builds on Module 01 (RSSI, dB, SNR) and Module 02 (azimuth / bearing).*

This is the first, cheapest way our system finds the direction to a radio
transmitter. No fancy math. Just point an antenna around, see where the
signal is loudest, and refine the answer with a parabola you already know
how to graph.

---

## What you'll be able to do after this

- Explain what a directional antenna (a Yagi) is, and why it "hears" better in one direction.
- Explain what a servo does and why we bolt one to the antenna.
- Describe the **sweep**: rotate, measure signal strength, find the loudest heading.
- Refine a coarse peak into a sharper bearing using a **parabola fit** and the vertex formula `x = -c1 / (2·c2)`.
- Explain the **prominence gate**: why the system sometimes refuses to answer, and why refusing beats guessing.
- Say what makes the answer accurate to 5° on a good day and 15° on a bad one.

---

## Questions this answers

- What is a directional antenna? What is a Yagi? What is "beamwidth"?
- What is a servo, and why do we need one here?
- What does "the sweep" mean? What is "amplitude comparison"?
- Why is the loudest heading the direction to the transmitter?
- What does "argmax" mean — the peak's *x*?
- Why fit a parabola instead of just taking the loudest point?
- What's the vertex formula and where does it come from?
- What is "prominence"? What is the "noise floor"?
- What does "return None" / "refuse" mean, and why is that the right call?
- What sets the accuracy — why 5° sometimes and 15° other times?

---

## Lesson

### Part A — A flashlight for radio: the directional antenna (Yagi)

Most simple antennas are like a bare lightbulb. They glow in every
direction equally, and they *listen* in every direction equally. Useful
if you want to hear everything. Useless if you want to know *where* a
signal came from — a bare bulb can't tell you which corner of the room a
voice came from.

A **directional antenna** is different. It listens much better in the one
direction it's pointed, and poorly everywhere else. Think of a flashlight
beam instead of a bare bulb. Or an old-fashioned ear trumpet — cup your
hand behind your ear and turn your head, and one direction suddenly gets
louder.

The directional antenna we use is a **Yagi** (full name: Yagi–Uda). It's
that fishbone-looking antenna with one driven element and a row of metal
rods. You've seen them as old rooftop TV aerials. Photo here:
<https://en.wikipedia.org/wiki/Yagi%E2%80%93Uda_antenna>

The Yagi's sensitivity, drawn as a shape (the "lobe"), looks like this —
a fat petal pointing the way the antenna faces:

```
              signal from this way is heard LOUD
                          ▲
                          |
                      .-"""""-.
                    /           \
                   |   MAIN      |
                   |    LOBE     |     <- antenna points UP the page
                    \           /
                      '-.   .-'
            quiet  <----  ANTENNA  ---->  quiet
              (off to the sides it barely hears)
```

**Beamwidth** is just *how wide that petal is*. A narrow beam is a tight
spotlight — very picky about direction. A wide beam is a floodlight —
less picky. Our antenna, the **ATK-10 Yagi**, has a beamwidth of about
**50°**. So its "petal" is roughly 50° wide. That number matters later:
a wider beam smears out the peak and makes the bearing fuzzier.

> Quick check (greenhorn): *"So the antenna doesn't tell me the direction
> by itself?"* Right. By itself it just hears louder one way. The trick is
> to **turn it** and watch when it gets loudest. That's the whole idea.

---

### Part B — Turning the antenna on command: the servo

To turn the antenna in a controlled way, we use a **servo**. A servo is a
small motor with a brain: you tell it "go to 35 degrees," and it goes to
35 degrees and holds there. Not "spin for a while" — an *exact angle*, on
command.

That's exactly what we need. The servo slowly rotates the Yagi around the
compass (in **azimuth** — the flat, horizontal turning, from Module 02),
so the antenna can "look" in many directions, one after another.

So now we have: a flashlight-ear (the Yagi) on a turntable we can aim
precisely (the servo). Time to use it.

---

### Part C — The sweep: comparing loudness across angles

Here's the core idea, and it's beautifully simple.

1. Point the antenna at heading 0°. Measure how strong the signal is.
2. Step to 5°. Measure again.
3. Step to 10°. Measure again.
4. ...keep going all the way around.

"How strong the signal is" is the **RSSI** — the received signal strength,
in dB, from Module 01. (In the code it's computed by
`compute_rssi_dbfs`.) Higher RSSI = louder.

When the antenna's beam points **straight at** the transmitter, the
transmitter lands right in the fat part of the petal, and RSSI is at its
**maximum**. When the beam points away, the transmitter falls in the quiet
sides, and RSSI drops.

So we literally lay out the loudness at every angle and pick the loudest
one. That's the whole method, and its name says exactly that:
**amplitude-comparison** direction finding. ("Amplitude" is loudness;
we're comparing it across angles.)

Plot RSSI (up) against heading (across) and you get a hump:

```
 RSSI
 (dB)
  -40 |                  *               <- the peak: loudest heading
      |               *  |  *
  -50 |            *     |     *
      |          *       |       *
  -60 |       *          |          *
      |     *            |            *
  -70 | *                |               *
      +--+----+----+----+----+----+----+----+--->  heading (degrees)
        0   10   20   30  40   50   60   70   80
                          ^
                this heading = the bearing to the transmitter
```

The heading at the top of the hump is your bearing. In math-speak, the
*x*-position of the maximum is called the **argmax** — literally "the
argument (the input, here the heading) that produces the max." Don't let
the word scare you. **Argmax = the x-value where the curve peaks.** In the
picture above, argmax ≈ 40°.

> Greenhorn: *"Why the loudest and not the quietest?"* Because the
> antenna hears **best** where it points. Loudest = the transmitter is
> sitting in the beam = the beam is aimed at it. Quietest just means the
> transmitter is off to the side.

---

### Part D — Refining the peak with a parabola (your HS algebra, used)

There's a catch. We only measured every 5°. The real peak almost never
lands exactly on one of our measured headings. Look again: we have a dot
at 40° and a dot at 45°, but the *true* loudest direction might be 41.3°,
sitting **between** our dots. Taking the single loudest dot rounds you off
to the nearest 5°. We can do better — for free.

Here's the move. The top of that RSSI hump, when you zoom in, looks just
like the top of a **parabola** — the same upside-down U you graphed in
high school. So we take the ~7 measured points right around the loudest
one and **fit a parabola through them**:

```
        y = c2·x² + c1·x + c0
```

(That's the parabola equation you know, just with the coefficients named
`c2`, `c1`, `c0` instead of `a`, `b`, `c`. Same thing.)

The fit finds the smooth upside-down U that best threads through our dots.
And here's the payoff: a parabola has an exact **vertex** — its highest
point — and you already know the formula for where the vertex sits:

```
        x_vertex = -c1 / (2·c2)
```

This is the same `x = -b / (2a)` you used to find the vertex of `y =
ax² + bx + c` in school. **You are not learning new math. You are using
the math you already have.** That `x_vertex` is our **refined bearing** —
the smooth curve's true peak, which can land between the measured dots.

```
 RSSI                  vertex of the fitted parabola
                          ▼  (the smooth top, between the dots)
  -41 |                  ___
      |              .--'   '--.
  -43 |           ,-'           '-.
      |        ,-'    o   o        '-.        o = our measured dots
  -45 |     ,-'    o                  '-.        (every 5 degrees)
      |   o'                             'o
      +---+-------+-------+-------+-------+----->  heading (deg)
         35      40      41.3    45      50
                          ^
              vertex lands at 41.3 deg, not on a 5-deg dot
```

**Tiny example.** Suppose the loudest *measured* dot is at 40°. We fit the
parabola through the 7 points around it. Its vertex comes out at 41.3°. We
report **41.3°**, not 40°. We squeezed sub-degree precision out of a
5°-coarse sweep, using nothing but a parabola.

> Greenhorn: *"Why not just take the loudest dot?"* Because the loudest
> dot is only as precise as your step size. With 5° steps, the single dot
> can be off by a couple degrees just from where the grid happened to
> fall. The parabola uses the *shape* of the whole top — the dots on both
> sides — to find the true peak between them. Same data, better answer.

**Where the ± (the uncertainty) comes from.** Every bearing we report
carries a `±` — a one-sigma uncertainty (Module 01's idea of "how sure are
we"). For L1 it comes from how *steady* that fitted vertex is. Picture the
parabola wobbling because the dots are noisy. If the dots sit cleanly on a
sharp curve, the vertex barely moves — small `±`. If the dots are scattered
and the curve is shallow and shaky, the vertex slides around a lot — big
`±`. That's the whole intuition: **a shakier fit gives a bigger ±.** (The
exact recipe the code uses to turn "fit wobble" into a number in degrees is
the one black box in this module — you don't need it to understand what's
happening. Shaky fit → big ±, clean fit → small ±.)

---

### Part E — The prominence gate: knowing when to shut up

Sometimes there's no real peak to find. The transmitter is too weak. Or
the signal is bouncing off buildings and hills (called **multipath**) and
arriving from everywhere at once, so the RSSI curve is just a noisy mush
with no clear hump. A bump that's barely there isn't a direction — it's
luck.

If we reported a bearing anyway, we'd be handing the system a confident
answer that's pure fiction. So we don't.

First we measure the **noise floor** — the background level of the curve
when the antenna is *not* looking at anything special. (Recall from Module
01: the noise floor is the baseline hum that's always there. In the code,
it's the median RSSI across the sweep, after throwing out the strongest
readings so the peak itself doesn't drag the floor up.)

Then we measure **prominence**: how far the peak sticks up above that
floor.

```
 RSSI
      |              *  <- peak
      |           *  |  *
      |         *    |    *       prominence = peak height
      |       *      |      *           minus floor height
      | - - - - - - -|- - - - - - - -   <- noise floor (the baseline)
      |              |
      +--------------+--------------->
            peak must clear the floor by at least 6 dB
```

The rule: the peak must rise **at least 6 dB above the floor**. If it
doesn't, the estimator **refuses**. It returns `None` — code-speak for
"no answer" — instead of guessing.

This is the project's **"no silent fallbacks"** rule in action: when the
system can't do something honestly, it says so out loud rather than
faking it. **Returning nothing beats returning a confident wrong
direction**, because a wrong bearing doesn't just waste effort — it
poisons the final cross-fix that combines this node with others, dragging
the whole answer off target. Silence is recoverable. A confident lie is
not.

> Real bench result (Module 09): a 2 dB bump was correctly **refused**,
> and a strong 14.9 dB peak was **accepted**. The gate works.

> Greenhorn: *"Isn't refusing just giving up?"* No — it's honesty. One
> node staying quiet is fine; the others, or the next sweep, can still
> produce a fix. One node *lying* corrupts everybody. Quiet beats wrong.

---

### Part F — How accurate is it?

L1 typically lands a bearing within about **5° to 15°** (one-sigma).
What pushes you toward the good end (5°) or the bad end (15°)?

- **SNR** (Module 01): a strong, clean signal gives steady RSSI readings,
  a sharp hump, and a rock-solid parabola vertex. Weak signal = noisy dots
  = wobbly vertex = bigger error.
- **Beamwidth**: our ATK-10's 50° beam is fairly wide, so the hump is
  broad. A broad hump is harder to pin to an exact peak than a narrow,
  pointy one. (A narrower beam would sharpen the peak.)
- **Multipath** (Module 09): bounces from terrain and structures distort
  the hump, shifting or flattening it, and widen the error.

5°–15° isn't laser-precise on its own — but it's genuinely useful. When
several nodes each report a bearing and we cross them, the lines intersect
on the transmitter, and the combined fix is far tighter than any single
node. L1 is the cheap, reliable workhorse that makes that possible.

---

### The whole thing in one breath

Bolt a directional antenna (Yagi, ~50° beam) onto a servo. Rotate it
across the compass. At each heading, measure RSSI. The loudest heading
points at the transmitter (amplitude comparison; the loudest *x* is the
argmax). Fit a parabola to the ~7 points around that peak and take its
vertex, `-c1 / (2·c2)`, for a bearing sharper than the coarse grid. If the
peak doesn't clear the noise floor by at least 6 dB, refuse and return
`None` rather than guess. Result: a 5°–15° bearing, honest about its own
uncertainty.

---

## Where this lives in the code

All paths are in `packages/rfmesh-dsp/src/rfmesh_dsp/`.

- **`l1.py`** — the estimator.
  - `class L1AmplitudeSweepEstimator` — the whole L1 method. You drive it
    with `begin_sweep()`, then `observe(heading, samples)` once per
    heading (this is where each RSSI reading is recorded), then
    `estimate()` to get the final `BearingReport` (or `None`).
  - `_fit_peak(...)` (≈ lines 313–394) — finds the loudest heading
    (`np.argmax`), fits the parabola to the points around it, and computes
    the vertex.
  - The vertex formula `-c1 / (2·c2)` is at **line 361**:
    `vertex_x = -c1 / (2.0 * c2)`.
  - The **prominence gate** default of **6.0 dB** is the
    `peak_prominence_db_min` parameter (**line 163**); the check that
    refuses with `None` when the peak is too small is at **line 331**.
  - When it refuses, it records a human-readable reason in
    `last_refusal_reason` — so an operator sees *why* it stayed quiet, not
    just that it did.
- **`rssi.py`** — `compute_rssi_dbfs(...)` turns one block of raw samples
  into the single RSSI number for that heading. This is the "how loud is
  it right now" measurement the sweep collects at every angle.

---

## Click test

Answer these out loud. If each one clicks, you've got it.

1. Why can't a plain, all-directions antenna tell you where a signal came from — and how does a Yagi fix that?
2. What does the servo contribute that the antenna alone can't?
3. In the RSSI-vs-heading hump, what does the peak's *x*-position mean physically? What's the one-word name for "the x where the curve peaks"?
4. We measured every 5° and the loudest dot is at 60°. Why might the true bearing be 61.4°, and how do we recover that without measuring more finely?
5. Write the vertex formula. Where have you seen it before?
6. The peak is only 3 dB above the noise floor. What does the estimator return, and why is that the *right* answer rather than a cop-out?
7. Name the three things that decide whether this sweep gives you 5° accuracy or 15°.
