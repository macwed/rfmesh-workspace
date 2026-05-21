# Module 07 — Fusion: turning many bearings into one position + an honest ellipse

*The server side. Builds on Module 02 (bearing lines / ellipse), Module 03
(sigma, the weight `w = 1/σ²`), and Module 05 (matrix, inverse, eigen).*

**Read this first.** Each node out in the field hands the server one thing: an
**angle** plus **how unsure it is** about that angle. The server's whole job in
this module is to take a fistful of those angles and turn them into a single
answer:

> *"The emitter is **here** — and here's an honest picture of how wrong I might be."*

That's it. One dot on a map, and one ellipse drawn around the dot. The dot is
the position. The ellipse is the honesty.

A lot of the machinery below is genuinely hard math. We are **not** going to
derive any of it. We will look at the *picture* each piece draws and the *one
job* it does. Software grinds the numbers. You read the result. Whenever you
see a **Depth-cap** box, that's the signal: *use it, don't derive it.*

---

## What you'll be able to do after this

- Say in one sentence what **fusion** is and why two bearing lines are not
  enough by themselves.
- Explain **least squares** as "the point that makes the total miss smallest,"
  and say why we **square** the misses and why we use **perpendicular**
  distance.
- Explain **weighted** least squares: trust tight bearings more, using
  `w = 1/σ²` from Module 03.
- Say what the **Stansfield seed** is — a fast first answer in one shot
  ("closed-form").
- Explain the **Jacobian** as a plain "sensitivity table," and name the three
  things it powers.
- Say what **MLE refinement** does ("roll downhill from the seed to a sharper
  fix") without doing any calculus.
- Explain how the answer's **covariance** becomes the drawn **95% ellipse** via
  eigen, and what the number **5.991** is.
- Read **GDOP** as a single geometry-quality score (~1 good, >6 bad) and say
  *why* nodes-in-a-line is bad.
- Name the **CRLB** as the theoretical floor, and say what the honesty tests
  check against it.
- Explain a **residual**, the **3-sigma outlier** flag, and the **fallback
  centroid** for hopeless geometry.

---

## Questions this answers

- What is *fusion*?
- What is *least squares*, and why *squared*? Why *perpendicular* distance?
- What does *weighted* add?
- What's a *seed*? What does *closed-form* mean? What is *Stansfield*?
- What is the *Jacobian* / *sensitivity*?
- What is *MLE*, and what is *rolling downhill* / *Gauss-Newton*?
- What does `(JᵀWJ)·dx = JᵀWr` mean in plain words?
- What is the *covariance of the answer*, and how do the ellipse axes come out
  of *eigen*?
- What are *chi-square* / *5.991* / *2.45-sigma*?
- What is *GDOP*, and why is *collinear* geometry bad?
- What is the *CRLB* / *theoretical floor*?
- What is a *residual*? What is the *3-sigma outlier* rule? What is the
  *fallback centroid* on *degenerate geometry*?

---

## Lesson

### 1. The setup: noisy lines that almost cross

From Module 02, each node gives a **bearing line** — a straight line starting at
the node, pointing the direction the emitter seems to be. From Module 03, each
line also carries a **sigma (σ)**: its one-standard-deviation angular wobble. A
σ of 1° is a sharp, confident line. A σ of 10° is a sloppy, "somewhere over
there" line.

In a perfect world, two lines cross at exactly one point — the emitter. Done.

The real world is not perfect. Every line is a little off. So instead of meeting
at one clean point, the lines make a small **messy crossing region** — a little
cloud of near-misses.

```
        line A          line B
          \              /
           \            /
            \   . .    /
             \ .  . . /        <- the lines don't meet at ONE point.
              \. .  ./            they make a messy little crossing
               X . ./             region. somewhere in this fuzz is
              / .  X              the emitter.
             /  . . \
            /        \
        line C  (sloppy, wide σ)
```

**Fusion** is the job of taking these N noisy lines and answering two questions:

1. **Where is the single best point?** (the position)
2. **How uncertain is it?** (the ellipse)

Two lines is the bare minimum (one line alone is just a ray — no fix). Three or
more is better: extra lines over-determine the answer, which lets the system
*check itself* (see residuals at the end).

---

### 2. Stansfield = weighted least squares (the seed)

Here's the first idea, and it's friendly.

Pick a candidate point on the map. For each bearing line, measure the
**perpendicular distance** from your point to that line — the shortest hop,
straight across. Call that the line's **"miss."**

> **Why perpendicular distance?** It's the honest "how far off this line is my
> guess" measurement. The perpendicular is the *shortest* distance from a point
> to a line — any other direction is longer and would unfairly punish a point
> that actually sits right on the line.

Now add up all the misses. The best point is the one that makes the **total miss
as small as possible.** That's **least squares**: "least" (smallest) "squares"
(of the misses).

> **Why squared?** Two reasons, both simple.
> 1. **Squaring kills the sign.** A miss of −3 and a miss of +3 are equally bad;
>    squaring makes both `9`, so left-misses and right-misses both count as bad,
>    not cancel out.
> 2. **Squaring punishes big misses harder.** A miss of 4 contributes 16; a miss
>    of 2 contributes only 4. So the answer is pulled hard away from any point
>    that misses a line badly.

Now add the trust. A 1°-σ line *deserves* to be taken seriously; a 10°-σ line is
a vague suggestion. So we **weight** each miss by `w = 1/σ²` (straight from
Module 03). Small σ → huge weight → "get this line right." Big σ → tiny weight →
"don't sweat this one."

> **Tiny example.** A tight σ=1° line has weight `1/1² = 1`. A sloppy σ=10° line
> has weight `1/10² = 0.01`. The tight line counts **100×** harder. The answer
> snaps toward the sharp line and barely notices the sloppy one.

The beautiful part: for *this* particular setup, you don't have to search for
the best point by trial and error. There's a **one-shot formula** — plug in the
lines and weights, do one little 2×2 solve, out pops the point. "One shot, no
guessing loop" is what **closed-form** means. This estimator is named
**Stansfield**, and because it's fast and reliable we use it as the **seed** —
our solid first answer that the next step polishes.

```
   node A (σ=1°, sharp)            node C (σ=10°, sloppy)
        \                                /
         \____                      ____/
              \___          _______/
                  \___  ___/
                      \●/   <- weighted near-crossing.
                      /|\      drop-lines (perpendicular "misses")
                     / | \     to each line. the point sits CLOSE to
                    /  |  \    the sharp lines, FAR from the sloppy one,
                   /   |   \   because sharp lines pull 100x harder.
              node B   |   (each | is one perpendicular "miss"
            (σ=2°)     |    that least-squares is shrinking)
```

---

### 3. The Jacobian = a table of "sensitivities"

This is one idea you learn **once**, because it quietly powers the next three
things. Don't be scared of the word.

The **Jacobian** is just a **table of sensitivities**. One question, asked for
every node:

> *"If the emitter slid a little bit east (or a little bit north), how much would
> THIS node's predicted angle swing?"*

That's all it holds. A number for "how fast does the angle change if the target
moves east," and a number for "...if it moves north," for each node.

The key intuition is about **distance**:

- A **nearby** node is very **sensitive**. The target shuffles a few meters and
  the node has to swing its pointing-line a lot to keep up. Big sensitivity.
- A **far-away** node is barely sensitive. The target shuffles the same few
  meters and the far node's line hardly moves at all. Tiny sensitivity.

```
   target moves one small step:   ·——→·

   NEARBY node:                       FAR node:
        N                                  F
        |\                                 |
        | \  old                           |\  old
        |  \____                           | \ new   (the two lines are
        |  /         the line              |  \      almost on top of each
        | / new      SWINGS a lot          |   \     other — barely moved)
        |/  (high sensitivity)             |    \  (low sensitivity)
```

That's the whole Jacobian: near = swings fast, far = barely budges. Software
fills the table in with the geometry.

**Why you only learn it once:** the *same* sensitivity table gets reused three
times —

1. to **refine** the fix (Section 4),
2. to **size the ellipse** (Section 5),
3. to **compute GDOP** (Section 6).

One idea, three payoffs.

> **Depth-cap (Jacobian).** Intuition only. It's a table of "how fast does this
> node's angle change when the target moves." Software computes the entries from
> the node positions and the current guess. You never differentiate anything by
> hand. *Use it, don't derive it.*

---

### 4. MLE refinement = rolling downhill from the seed

The Stansfield seed is good, but for a finite batch of noisy bearings it's
slightly **biased** — nudged a touch off the truly best answer. We can do
better.

The truly best answer is the **MLE** — the **maximum likelihood estimate**, a
fancy name for *"the emitter position that best explains all the noisy bearings
we actually saw."* Of all the points on the map, it's the one most consistent
with the evidence.

How does software find it? It **rolls downhill.** Picture a smooth bowl. The
bottom of the bowl is the best answer (lowest total weighted error). The seed is
where you set the marble down — already near the bottom. Then:

1. Look at the current misses (the residuals).
2. Use the **sensitivity table** (Section 3) to figure out which direction is
   downhill.
3. Take a step that way.
4. Repeat. The steps get smaller and smaller as you near the bottom.
5. Stop when the steps get tiny (in our code, smaller than 1 mm).

The marble settles to the bottom of the bowl. That settled spot is the MLE.

```
   total weighted error
        \                         /
         \      seed ●           /
          \       \             /
           \       \  step     /
            \       ●_  step  /
             \        ●_  _● /
              \         ●●  /     <- marble settles at the bottom:
               \___________/         the MLE. each step uses the
                  the "bowl"         sensitivity table to pick the
                                     downhill direction.
```

You will see this written compactly as **`(JᵀWJ)·dx = JᵀWr`**. In plain words,
that single line is just **"compute one downhill step."** `J` is the sensitivity
table, `W` is the trust weights `1/σ²`, `r` is the current misses, and `dx` is
the little step to take. Software solves it, takes the step, and loops. This
particular roll-downhill recipe is called **Gauss-Newton**.

> **Depth-cap (Gauss-Newton MLE).** Black box. Know this much: *start at the
> Stansfield seed, roll downhill toward lower total weighted error, stop when it
> stops improving.* The `(JᵀWJ)·dx = JᵀWr` line is "one downhill step," done by
> software. No calculus. *Use it, don't derive it.*

---

### 5. From the answer to the ellipse

Now the honesty half. The **same sensitivity table** from Section 3 also tells
you how **pinned-down** the answer is.

Think about it: if every node is highly sensitive and they pull from good
directions, the answer is locked in tight. If the nodes are insensitive or pull
from nearly the same direction, the answer is loose and could slide around.
Software turns the sensitivity table into a small **2×2 covariance** table for
the position. (Recall Module 05: **covariance summarizes spread** — how big the
cloud of possible answers is, and which way it leans.)

A 2×2 covariance is exactly the kind of thing Module 05's **eigendecomposition**
eats. Eigen hands back the **special directions** and the **stretch sizes** of
the spread:

- the two **axis directions** of the uncertainty (which way it's long, which way
  it's short), and
- the two **lengths** along those axes.

That's an ellipse. The directions orient it; the lengths size it.

One last twist: those raw eigen lengths describe the *1-sigma* spread. We want
the **95% confidence** ellipse — the one we can honestly tell the operator
"there's a 95% chance the emitter is inside this." To get there, scale each
semi-axis by a fixed number:

> **semi-axis = √(5.991 × eigenvalue)**

Where does **5.991** come from? It's a looked-up constant: the **chi-square 95%
value for 2 dimensions.** It's the same as drawing the contour at about
**2.45-sigma** (`√5.991 ≈ 2.448`). It never changes — it's a fact about
2D Gaussians, like π is a fact about circles. We hard-code it. Forget it, and
every ellipse the system draws comes out ~2.45× too small — a silent lie about
how good the fix is.

```
   covariance "blob"        eigen finds the axes        scaled 95% ellipse
   (raw spread)             (special dirs + sizes)      (×√5.991 ≈ 2.45)

       .·:·.                      ↖   ↗                    ___________
     .:·····:.                     \ /                    /           \
    :·····●···:        --->         ●         --->       (      ●      )
     ·:·····:·                     / \                     \___________/
       ·:·:·                      ↙   ↘                   the drawn ellipse
                            major axis = long stretch     on the dashboard
                            minor axis = short stretch
```

> **Depth-cap (covariance → ellipse).** Black box. Eigen gives the axis
> directions and lengths; **5.991** is a fixed 95% scale constant
> (≈ 2.45-sigma). You don't derive how sensitivity becomes covariance, and you
> don't derive chi-square. *Use them, don't derive them.*

---

### 6. GDOP = how good is the geometry?

Here's a sharp, important idea: **even with perfect sensors, bad geometry smears
the answer.** Where you put the nodes matters as much as how good they are.

**GDOP** — **Geometric Dilution of Precision** — is **one number** that scores
the node layout for a given target:

- **GDOP ≈ 1** → great. Nodes are well spread out, their lines cross near a
  clean 90° angle. The crossing is crisp, the ellipse is compact.
- **GDOP > 6** → bad. Nodes are nearly in a line with the target, so the lines
  *graze* each other at a shallow angle. A tiny wobble slides the crossing a
  long way. The ellipse stretches into a long cigar.

> **Why is collinear (nodes-in-a-line) bad?** When two lines cross at a steep
> angle, they nail down the point from two independent directions. When they
> cross at a shallow, grazing angle, they both agree on roughly the same
> direction but say almost nothing about *how far along* — so the answer is free
> to slide. That sliding is the long axis of the cigar ellipse.

The clever thing: GDOP is computed from the **sensitivity table only** — it does
**not** use the sigmas at all. That keeps two questions cleanly separate:

- **GDOP** answers *"is the SHAPE of the layout good?"*
- **Covariance/ellipse** (Section 5) folds in *"are the SENSORS good?"* (the σ's).

So when an operator sees a high GDOP, they know to **move a node** — the fix is
weak because of *placement*, not because of bad radios.

```
   GOOD geometry (GDOP ~1)              BAD geometry (collinear, GDOP >6)

      A                                  A───────B───────C   (nodes in a row)
       \                                  \      |      /
        \                                  \     |     /     lines all graze
         \   ____ B                         \    |    /      the target at a
          \ /    /                            \  |  /        shallow angle
        ●  X    /     <- crisp crossing,        \●/   <- crossing slides easily
          / \  /         lines meet near 90°    /|\      along the line of sight
         /   \/                                / | \
        /    /\                          ┌─────────────────────┐
       /    /  \                         │ ●●●●●●●●●●●●●●●●●●●●  │ long cigar
      C────/    (compact ellipse: ◯ )    └─────────────────────┘ ellipse
```

> **Depth-cap (GDOP).** Know the *meaning* and the *scale*: ~1 good, >6 bad,
> computed from layout geometry alone (no sigmas). The actual formula —
> `sqrt(trace((HᵀH)⁻¹) / mean range²)` — is software's. *Use it, don't derive
> it.*

---

### 7. CRLB = the theoretical floor

There is a hard limit on how good **any** method could *possibly* do, given this
exact geometry, this signal quality, and this number of nodes. That limit is the
**Cramér-Rao Lower Bound (CRLB)** — the theoretical floor on the size of the
uncertainty.

You can't beat it. No estimator can. It's not a goal you try to exceed; it's a
wall set by physics and geometry. The honesty tests in Module 08 check that our
system gets **close to** this floor — that we're squeezing out nearly all the
precision the situation allows — *not* that we somehow beat it (which is
impossible). It's the yardstick that says "our fix is as tight as anyone's could
honestly be here."

> **Depth-cap (CRLB).** Name and meaning only. *The best precision any method
> could achieve for this geometry/SNR/node-count.* We measure how close we get;
> we never claim to beat it. *Use it, don't derive it.*

---

### 8. Residuals, outliers, and the fallback

After we've solved for the position, we do a self-check. For each node, look at
its **residual**: how far its reported bearing missed the final answer. If a node
truly is good, its residual should be small — comparable to its own σ.

Now apply the **3-sigma rule** from Module 03. If a node's residual is more than
**3× its own σ**, that's deeply suspicious — far more off than its claimed
uncertainty allows. We **flag it as a probable outlier** (multipath bounce, bad
calibration, knocked antenna) and we light up **which node** on the dashboard.
The system *self-diagnoses*: the operator doesn't just see "the fix is loose,"
they see "node 3 is the troublemaker."

```
   node 1 residual: 0.8°  (σ=1.0°)  ratio 0.8   ok
   node 2 residual: 1.5°  (σ=2.0°)  ratio 0.75  ok
   node 3 residual: 9.0°  (σ=1.0°)  ratio 9.0   ⚠ OUTLIER  (>3σ) — flag node 3
```

(In sprint-1 the outlier isn't thrown out of the math — it's *shown*. Honesty
over cleverness: the operator decides.)

And the safety net. Sometimes the geometry is just **hopeless** — the lines are
nearly parallel, no real crossing exists ("degenerate geometry"). A clever
solver pushed on garbage geometry would spit out a confident-looking but fake
answer. We refuse to do that. Instead the solver **falls back** to a rough
**weighted center of the bearing crossings**, labels the result **LOW
confidence**, and never dresses it up as precise. A loose-but-honest answer beats
a tight-looking lie every time.

---

## Where this lives in the code

All under `packages/rfmesh-fusion/src/rfmesh_fusion/`:

- **`stansfield.py`** — `stansfield_seed()`: the one-shot weighted least-squares
  seed (Section 2). Raises `DegenerateGeometryError` when there's no real
  crossing.
- **`mle.py`** — `solve_mle()`: the roll-downhill Gauss-Newton refinement
  (Section 4). The sensitivity table (the **Jacobian**, Section 3) is built in
  `_predicted_azimuths_and_jacobian()`.
- **`covariance.py`** — `compute_covariance()` turns the sensitivity table into
  the 2×2 covariance `(JᵀWJ)⁻¹` (Section 5); `covariance_to_ellipse()`
  eigen-decomposes it and scales by the constant
  `CHI2_95_DF2 = 5.991464547107979`.
- **`gdop.py`** — `compute_gdop()`: the geometry-quality number (Section 6),
  computed from the layout alone.
- **`residuals.py`** — `compute_residuals()`: per-node residual + the
  `|r|/σ > 3` outlier flag (Section 8).
- **`fuser.py`** — `StansfieldMLEFuser`: the orchestrator that runs the whole
  pipeline (seed → MLE → covariance → ellipse → GDOP → residuals →
  confidence) and the `fallback_centroid` path for hopeless geometry.
- **Concept reference:** `docs/demo/crlb_analysis.py` computes the **CRLB**
  (Section 7) for the demo geometry; `ARCHITECTURE.md` §7 describes the demo
  honesty payload; `INTERFACES.md` §3 defines the `FixEvent` that carries
  position, covariance, ellipse, GDOP, residuals, and method out to the
  dashboard and ATAK.

---

## Click test

Tick each box. If one doesn't click, re-read the matching section.

- [ ] **Fusion** = turn N noisy bearing lines into one position + one honest
  ellipse. Two lines minimum; three+ lets the system check itself.
- [ ] **Least squares** picks the point with the smallest total miss; misses are
  **perpendicular** distances to the lines.
- [ ] We **square** the misses to kill the sign and punish big misses harder.
- [ ] **Weighted** = trust tight bearings more, with `w = 1/σ²`. A σ=1° line
  pulls 100× harder than a σ=10° line.
- [ ] **Stansfield seed** = the fast, one-shot (**closed-form**) first answer.
- [ ] The **Jacobian** is a **sensitivity table**: how fast each node's angle
  swings when the target moves. Near = fast, far = slow. It powers the refine,
  the ellipse, and GDOP.
- [ ] **MLE** = the position that best explains the bearings; we find it by
  **rolling downhill** from the seed (Gauss-Newton). `(JᵀWJ)·dx = JᵀWr` means
  "take one downhill step."
- [ ] The **covariance** of the answer goes through **eigen** to get ellipse
  axis directions + lengths; scale by **√5.991 (≈ 2.45-sigma)** for the **95%**
  ellipse. 5.991 is the chi-square 2D constant — accept it.
- [ ] **GDOP** scores the layout (~1 good, >6 bad), from geometry alone.
  **Collinear** is bad because grazing lines let the crossing slide.
- [ ] **CRLB** is the theoretical floor; tests check we get *close*, never that
  we *beat* it.
- [ ] A **residual** is how far a node missed the final fix; **> 3σ** flags it
  as a probable **outlier** and the dashboard names the node.
- [ ] On **degenerate geometry**, the solver **falls back** to a weighted
  centroid, labelled **LOW confidence** — never a fake precise answer.
