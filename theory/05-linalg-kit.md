# Module 05 — Linear-Algebra Survival Kit (picture-only)

*Pure scaffolding for Module 06 (the L2 coherent array) and Module 07 (fusion).*

**Read this first.** This is the one genuinely foreign-looking math in the
whole course. We are **not** going to learn how to *do* it. We are going to
learn how to *recognize what each tool does*, so that when Modules 06 and 07
throw these words at you, you nod instead of flinch.

Repeat this to yourself the whole way through:

> **You will never compute these by hand — you call a library. You only need
> to know what goes in, what comes out, and how to read the result.**

Every hard thing below is a **black box**. We describe what it *does*, never
how it grinds the numbers. Software does the grinding. You read the picture.

---

## What you'll be able to do after this

- Picture a **vector** as an arrow, or as a short list of numbers.
- Picture a **matrix** as a *machine*: an arrow goes in, a transformed arrow
  comes out.
- Say in one sentence what **transpose** and **Hermitian** (the little `H`
  superscript) are — a tidy-up flip the formulas need.
- Say what a **matrix inverse** (`A⁻¹`) is: the "undo" button.
- Explain **eigenvectors** and **eigenvalues** in plain words: the special
  directions a machine doesn't twist, only stretches — and how much.
- Say what **eigendecomposition** hands you (the full sorted list of those
  special directions and their stretch sizes).
- Say what the **covariance matrix `R`** summarizes, and why feeding it into
  eigendecomposition or inversion is the heart of Modules 06 and 07.
- Say what **diagonal loading** is for (steady a wobbly matrix before
  inverting it).

---

## Questions this answers

- What is a vector?
- What is a matrix? What does "a matrix is a machine" mean?
- What is a transpose? What is a Hermitian, and what is that `H` superscript?
- What is a matrix inverse / `A⁻¹` / the "undo" button?
- What is an eigenvector? What is an eigenvalue?
- What are these "special directions" everyone talks about?
- Why does a "big eigenvalue" mean "strong / important"?
- What is eigendecomposition?
- What is the covariance matrix `R`?
- What does `R = (1/T)·X·Xᴴ` mean, in plain English?
- What is diagonal loading, and why do we do it?

---

## Lesson

### (a) Vector — an arrow (you already know this one)

A **vector** is just an arrow. It has a direction and a length. You can also
write the very same arrow as a short list of numbers — how far it reaches
East, and how far it reaches North.

The arrow `(3, 2)` means: go 3 to the right, 2 up.

```
  ^ North
  |
2 +        * (3, 2)
  |      /
1 +    /
  |  /
0 +--+--+--+--> East
  0  1  2  3
```

That's it. An arrow, or equivalently a list of numbers. You've pictured this
since high school. Everything else in this module is built on top of this one
idea.

> Depth-cap: a vector is an arrow / a short list of numbers. Nothing deeper
> is needed here.

---

### (b) Matrix — a MACHINE

A **matrix** is a grid of numbers. But forget the grid for a second. The way
to picture a matrix is as a **machine**: you feed an arrow in one side, and a
*different* arrow comes out the other side. The machine has rotated it,
stretched it, or both.

```
                +-------------------+
   [arrow in]   |                   |   [new arrow out]
   ----------> |   MATRIX  (machine) | ----------------->
   (3, 2)       |                   |   (rotated /
                +-------------------+    stretched)
```

Same arrow goes in. A transformed arrow comes out. *Which* transformation
(how much rotation, how much stretch) is baked into the numbers in the grid —
but you do not care about those numbers by hand. You care about the picture:
**arrow in → machine → new arrow out.**

That is the entire mental model of a matrix for this course. A machine for
arrows.

> Depth-cap: black box. A matrix takes an arrow in and gives a transformed
> arrow out. You will not multiply grids by hand.

---

### (c) Transpose and Hermitian — a tidy-up flip

Sometimes a formula needs the grid **flipped across its diagonal** — the rows
become columns and the columns become rows. That flip is called the
**transpose**.

```
   original              transpose (rows <-> columns)
   [ a  b ]                 [ a  c ]
   [ c  d ]      ----->     [ b  d ]
```

The **Hermitian** is almost the same thing, written with a little `H`
superscript, like `Xᴴ`. It does the same flip, **and** it also flips the sign
of the *phase* of each complex entry (phase — the timing-angle of a wave —
came up back in Module 01). Our antenna signals are complex numbers carrying
phase, so the Hermitian is the version we actually use.

You do not need to know why the formulas want this. Just read both as: **"a
standard tidy-up operation the math needs."** When you see `Xᴴ` or `aᴴ` in
the next two modules, read it as *"the Hermitian of X"* / *"the Hermitian of
a"* — a flipped, phase-tidied copy. That's all.

> Depth-cap: black box. Transpose = flip rows and columns. Hermitian = the
> same flip plus a phase-sign flip. Just a tidy-up the formulas require.

---

### (d) Matrix inverse — the "undo" button

If a matrix is a machine that transforms an arrow, then its **inverse** —
written `A⁻¹`, with that little `-1` — is the machine that transforms it
**back**. It's the undo button.

```
   (3,2) --> [  A  ] --> (new arrow) --> [ A⁻¹ ] --> (3,2) again
              forward                      undo
```

Feed an arrow through `A`, then feed the result through `A⁻¹`, and you get
your original arrow back. That's the whole idea: `A⁻¹` reverses what `A` did.

In practice, inverses are how you "solve" a system — given the *output* of a
machine, the inverse tells you what the *input* must have been. In Module 07
the inverse of `R` powers the "adaptive spotlight" that steers a sharp null
toward a jammer. Software computes the inverse. You never do it by hand.

> Depth-cap: black box. `A⁻¹` is the undo machine that reverses `A`. Software
> computes it; never by hand.

---

### (e) Eigenvectors and eigenvalues — the big one

This is the most important idea in the module, so go slow. Good news: it's a
picture, not a calculation.

Feed most arrows through a matrix-machine and they come out **pointing
somewhere new** — twisted to a different direction. That's normal.

But a few **special** directions are different. When you feed one of *those*
arrows in, it comes out pointing the **exact same way** as it went in — only
**longer or shorter**. The machine didn't twist it at all. It just stretched
(or shrank) it along its own line.

```
  NORMAL arrow:  goes in one way, comes out TWISTED to a new direction

      in:  ^                out:      ->          (direction changed)
           |                        /
           |                      /
           +                     +


  SPECIAL arrow (an eigenvector): comes out SAME direction, only longer

      in:  ^                out:  ^
           |                      |
           |                      |   <-- same direction,
           +                      |       just stretched
                                  |
                                  +
```

- A **special direction** the machine only stretches (never twists) is an
  **eigenvector**.
- **How much** that direction gets stretched is its **eigenvalue**. Stretched
  to twice as long? Eigenvalue 2. Shrunk to half? Eigenvalue 0.5.

The mental tag to carry forever:

> **Eigenvector = a direction the machine doesn't twist, only stretches.
> Big eigenvalue = a strong, important direction.**

A big eigenvalue means that direction got blown up a lot — the machine "cares"
about it strongly. A tiny eigenvalue means that direction barely matters.
That's the entire takeaway, and it's exactly how Module 06 separates real
signals (strong directions) from noise (weak directions).

Here's a real picture of eigenvectors at work — the blue arrow keeps its
direction through the transformation while the red one gets twisted:

- https://en.wikipedia.org/wiki/Eigenvalues_and_eigenvectors

**Eigendecomposition** is just the software handing you the *full list* of
these special directions, each paired with its stretch size, **sorted from
biggest to smallest**. You call one library function and get back: "here are
the strong directions on top, the weak ones at the bottom." You read the
list. You do not derive it.

> Depth-cap: intuition + picture only. Eigenvector = direction only
> stretched; big eigenvalue = strong direction; eigendecomposition = the
> sorted list, handed to you by software. NO characteristic polynomial, NO
> `det(A − λI)`, ever.

---

### (f) The covariance matrix `R` — what it summarizes

In Module 06 a node has several antennas, so it's listening to several signals
at once — one per antenna. The **covariance matrix `R`** is a small table that
summarizes **how those signals move together**.

```
              antenna 1   antenna 2   antenna 3
            +-----------+-----------+-----------+
 antenna 1  |  POWER 1  |  1 & 2    |  1 & 3    |
            +-----------+-----------+-----------+
 antenna 2  |  2 & 1    |  POWER 2  |  2 & 3    |
            +-----------+-----------+-----------+
 antenna 3  |  3 & 1    |  3 & 2    |  POWER 3  |
            +-----------+-----------+-----------+
```

- The **diagonal** (top-left to bottom-right) holds each signal's **own
  power** — how strong each antenna's signal is on its own.
- The **off-diagonal** entries show how each **pair** of antennas is related —
  do they rise and fall together, or independently?

How does software build `R`? The recipe is written:

```
   R = (1/T) · X · Xᴴ
```

In plain English: **"take your samples, multiply them by their own Hermitian
(the tidy-up flip from part c), and average that over `T` snapshots."** `T` is
just how many sample-blocks (snapshots) you averaged. Software does all of it
from the raw antenna data — you never assemble the table by hand.

Why does any of this matter? Two reasons, and they are *exactly* Modules 06
and 07:

1. **Feed `R` into eigendecomposition** (part e) and the strong directions
   pop to the top of the list — those are **real signals**. The weak
   directions at the bottom are **noise**. That separation is how MUSIC (Mod
   06) finds the bearing to an emitter.
2. **Invert `R`** (part d) and you get the engine behind the **adaptive
   spotlight** (Mod 07) — steering a sharp listening null onto a jammer.

One little table, two superpowers.

> Depth-cap: black box. Know that `R` summarizes how the antenna signals move
> together (power on the diagonal, pairings off-diagonal), that software
> builds it, and that it feeds eigendecomposition and inversion.

---

### (g) Diagonal loading — ballast for a tippy boat

One practical wrinkle, because you'll see it in the code. When you've only
collected a few samples, `R` can come out **wobbly** — almost impossible to
invert cleanly. Try to invert a wobbly `R` and the answer "blows up" into
garbage.

The fix is simple: add a tiny amount to the **diagonal** entries of `R`
before inverting it. Written as `R + ε·something·I` — but ignore the symbols;
the *idea* is what matters. That little nudge steadies the matrix so the
inverse behaves.

Picture a tippy canoe. Drop a little ballast in the bottom and it sits still
instead of rolling over. **Diagonal loading is that ballast** — a small,
deliberate steadying nudge added before the risky inversion step.

> Depth-cap: black box. Know the *purpose* — stabilize a wobbly `R` before
> inverting it — not the formula.

---

## Where this lives in the code

Everything above is already implemented in `rfmesh-dsp`. You will recognize
the words now:

- **Building `R`** — `packages/rfmesh-dsp/src/rfmesh_dsp/array_covariance.py`.
  The `sample_covariance` function (around line 47) is literally part (f):
  it computes `R = (1/T)·block·blockᴴ` from a block of coherent antenna
  samples. The same file's `forward_backward_smooth` is an extra tidy-up step
  that helps when signals are tangled by multipath.

- **Eigendecomposition (part e)** —
  `packages/rfmesh-dsp/src/rfmesh_dsp/l2_music.py`. The MUSIC bearing
  estimator calls `numpy.linalg.eigh` — that one library call *is* the
  eigendecomposition. It hands back the sorted special directions and stretch
  sizes, and MUSIC reads the list to tell strong signals from noise. (You
  don't compute it; `numpy` does. Exactly as promised.)

- **Inverse + diagonal loading (parts d and g)** —
  `packages/rfmesh-dsp/src/rfmesh_dsp/l2_mvdr.py`. The Capon / MVDR code adds
  the diagonal-loading "ballast" to `R`, then calls `numpy.linalg.inv` to get
  `R⁻¹` — the undo button that powers the adaptive spotlight.

Notice the pattern: every hard step is **one library call**. The library
grinds; the code just reads the result. That's the whole point of this module.

---

## Click test

You're ready for Module 06 if you can answer these out loud, in your own
words, without any formula:

1. What is a vector? (Picture it.)
2. A matrix is a *what*? What goes in, what comes out?
3. Read `Xᴴ` aloud. What does the `H` mean, roughly? (One sentence is fine —
   "a tidy-up flip.")
4. What does `A⁻¹` do? What everyday button is it like?
5. Of all the arrows you feed through a machine, what's special about an
   **eigenvector**?
6. If a direction has a **big eigenvalue**, is it important or not? Why?
7. What does **eigendecomposition** hand you, and in what order?
8. What does the covariance matrix `R` summarize? What's on its diagonal?
9. Say `R = (1/T)·X·Xᴴ` in plain English (no symbols).
10. Why do we do **diagonal loading** before inverting `R`? (The boat
    analogy counts.)

If you got all ten, the next two modules will read as *engineering*, not as
foreign math.

---

## Why you just learned this

You didn't learn linear algebra. You learned to **recognize three tools**:
the covariance matrix `R`, eigendecomposition, and the matrix inverse. Those
three tools *are* Modules 06 and 07. Module 06 builds `R` and runs
eigendecomposition on it to find a bearing. Module 07 inverts `R` (with a
little diagonal-loading ballast) to steer a null at a jammer. When those words
show up — `R`, `eigh`, `R⁻¹`, "loading" — you will see *familiar machines with
arrows going in and out*, not a wall of symbols. That recognition, and nothing
heavier, is the whole job of this module.
