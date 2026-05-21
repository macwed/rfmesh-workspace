# Module 02 — Geometry of Bearings: azimuth, lines of position, triangulation, the ellipse

## What you'll be able to do after this

- Read a compass direction in degrees and say which way it points.
- Explain why one node can only draw a *line* toward an emitter, never a single dot.
- Explain how two or more nodes cross their lines to pin the emitter down.
- Say why three nodes beat two.
- Explain why the answer comes out as a fuzzy *oval* (an ellipse), not a perfect dot and not a circle.
- Explain why the *angle* at which two lines cross changes how good the answer is.

No math beyond "degrees on a compass" and "lines crossing." Promise.

## Questions this answers

- What is a **bearing**? What is an **azimuth**? What is a **heading**? Are they the same thing?
- What does **true North = 0°, clockwise** mean?
- What is a **line of position** and a **ray**?
- What is **triangulation**? What is a **cross-fix**?
- Why do **3+ nodes beat 2**?
- What is an **uncertainty wedge**?
- Why is the result an **ellipse (oval)** and not a **circle**?
- Why does the **crossing angle** matter?

## Lesson

### 1. Azimuth: a direction, written as a number

Imagine standing in a field. Someone asks, "Which way is that radio tower?"
You could point. But a pointed finger doesn't travel well over a network. So
instead we write the direction down as a single number: an **azimuth**.

The rules are simple and never change in this system:

- **North is 0°.** (True North — the actual top-of-the-map North, not magnetic.)
- Numbers grow **clockwise**.
- So **East is 90°**, **South is 180°**, **West is 270°**.
- The range is 0° up to (but not including) 360°. After 359° you're back at North = 0°.

Here is the compass rose. Read it like a clock that starts at the top:

```
                 N
                 0°
                 |
                 |
   W 270° -------+------- 90° E
                 |
                 |
                180°
                 S

        clockwise:  N(0) -> E(90) -> S(180) -> W(270) -> back to N
```

A few quick reads:

- 45° is exactly **North-East** (halfway between N and E).
- 135° is **South-East**.
- 225° is **South-West**.

That's the whole idea. An azimuth is just "how many degrees clockwise from North."

**Three words that sound the same but aren't:**

- **Azimuth** — a compass direction, as a number. The general word.
- **Bearing** — the azimuth measured *from a node toward the emitter*. "Node A says the emitter is at bearing 40°." A bearing is an azimuth with a job.
- **Heading** — which way a node's own antenna is *aimed*. This is about the node's hardware, not the target. A node can be *heading* due East (90°) while reporting a *bearing* of 40° to the emitter it found.

Keep them straight: heading = where I'm pointed, bearing = where the target is.

### 2. Line of position: one node draws a line, not a dot

A node measures a bearing. Say Node A reports: **"The emitter is at 40° from me."**

What has Node A actually learned? It knows the *direction*. It does **not**
know the *distance*. The emitter could be 1 km away at 40°, or 4 km away at
40° — both look identical to a node that only measures direction.

So all Node A really knows is: "the emitter is *somewhere along this line*."
That line, shooting out from the node in the measured direction, is called a
**line of position**. Because it starts at the node and goes outward in one
direction, it's also called a **ray** (a line with a starting point and one
direction, like a laser pointer beam).

```
            * emitter is somewhere on this ray
           /
          /
         /
        /   <- ray at azimuth 40°
       /
      A  (Node A)
```

One node, one ray. The emitter is on it — but *where* on it? Unknown. **One
node alone cannot give a point.** This is the key limitation that the rest of
the module fixes.

### 3. Triangulation: two rays cross at the emitter

Now add a second node somewhere else. Node B measures its own bearing to the
*same* emitter — say 320°. Node B draws its own ray.

Here's the magic: the emitter is on Node A's ray **and** on Node B's ray. The
only place that can be true is where the two rays **cross**. That crossing
point is the emitter.

```
                  ,*  <- they cross HERE = the emitter
                ,'|
   A ----------'  |
   (ray 40°)      |
                  |
                  | (ray 320°)
                  |
                  B
```

This is **triangulation**, also called a **cross-fix** ("fix" = a pinned-down
position). Two directions, from two different places, cross at one point.

**The spotters analogy.** Picture two people standing far apart in a park, both
watching the same balloon high in the sky. Each one points at it. Neither
person alone can tell you how far away the balloon is. But if you stand back
and look at the two pointing arms, you can extend each arm into a line and see
exactly where the two lines meet — that's where the balloon is. The two
spotters are the two nodes. Their pointing arms are the rays. The meeting point
is the cross-fix.

### 4. Three or more nodes beat two

Two rays always cross at exactly one point (unless they're parallel). That's
tidy — but it's also *too trusting*. If one of the two bearings is a little
wrong, the crossing point quietly moves, and you have no way to *know* it
moved. Two rays will always agree on *some* point, even a wrong one.

Add a third node. Now you have three rays. If all three bearings are good, the
three rays cross **near one common point** — a tight little knot.

```
   Three good bearings:          One bad bearing (node C is off):

        \   |   /                     \   |        /
         \  |  /                        \ |       /
          \ | /                          \|      /
           \|/                            X     /   <- C's ray
            X   <- tight knot              \   /        misses the knot
           /|\                              \ /
          / | \                              X  <- A and B still knot here
         /  |  \                            / \
        A   B   C                          A   B   C
```

If one ray misses the knot, you can **see** it. The system can point at that
node and say "node C disagrees — probably bad data (an echo, a miscalibration)."

So three or more nodes give you two things two nodes can't:

1. A better, averaged-out position (more votes, less noise).
2. **Self-checking** — the ability to spot and flag a bad bearing instead of
   silently swallowing it.

This is exactly why this system reports a *per-node residual*: how far each
node's ray missed the agreed point. (Residuals show up again in a later module.)

### 5. Why an ellipse, not a point or a circle

Everything so far pretended each bearing was a perfect, razor-thin line. In the
real world, no bearing is perfect. Each measurement has a little **wobble** —
maybe the true direction is 40°, but the node might be off by a degree or two
either way. (Next module gives this wobble a name: *sigma*. For now, just
"wobble.")

So a node doesn't really give you a thin ray. It gives you a thin **wedge** — a
ray with fuzzy edges, fanning out slightly:

```
   A single bearing is really a WEDGE, not a line:

                        ___...---  (could be a bit this way)
              ___...---'
       A =============================  (best guess: 40°)
              '''...___
                        '''...---  (could be a bit that way)
```

The emitter is somewhere inside that wedge — most likely near the middle, but
possibly off to a fuzzy edge.

Now cross **two** wedges. The emitter must be inside both. So it lives in the
little **patch where the two wedges overlap**:

```
   Two wedges overlapping:

        B's wedge
          \\\\
           \\\\
            \\\\        the OVERLAP patch
   A ========####======     (a small oval)
            ////\\\\
           ////  \\\\
          ////
        A's wedge
```

That overlap patch is the real answer: not a dot (we're not that precise) and
**not a circle** either. It comes out as an **oval** — an **ellipse**. In the
code this oval is a value called `EllipseENU` (the "oval" you see drawn on the
operator's map).

**Why an oval and not a circle?** Look at the directions:

- *Across* the wedges (the direction in which the two wedges cut sharply
  through each other), the overlap is **narrow**. The crossing nails the
  position tightly that way.
- *Along* the direction where the wedges merely graze and run alongside each
  other, a little wobble slides the overlap a long way. So the overlap is
  **stretched** that way.

Narrow one way, stretched the other way = an oval. A circle would mean "equally
unsure in every direction," which is almost never true for a cross-fix.

### 6. Why the crossing angle matters

The *shape* of that oval depends on the **angle** at which the two wedges cross.

**Good case — wedges cross near a right angle (about 90°):** the overlap is a
small, compact patch. Both directions are pinned down well. This is the fix you
want.

**Bad case — wedges cross at a shallow, narrow angle:** the two wedges run
almost alongside each other, so their overlap smears out into a long, skinny
oval. The position is loose — you know roughly the right band, but it's stretched.

```
   GOOD: cross near 90°            BAD: shallow crossing
   -> small, compact oval          -> long, smeared oval

         \   /                        ____
          \ /                     ___/    \___
        ===O===                  =================------____
          / \                        \___    ___/
         /   \                           \__/
        small (.)                  loooooong  ( ~~~~~~~ )
```

Same wobble in each bearing — wildly different answers, just because of the
crossing angle. Nodes spread out so their views cross squarely give crisp
fixes; nodes bunched so their views graze give stretched fixes. This is the
intuition behind a measure called **GDOP** (Geometric Dilution of Precision),
which gets its own module later. Just remember the picture for now: **square
crossing = small oval, shallow crossing = long oval.**

### 7. One note on maps and frames

Two coordinate systems show up, and you don't need any depth here — just know
they exist:

- **Where things really are:** latitude and longitude on the round Earth (the
  standard called **WGS-84**). This is what gets sent to the map / ATAK display.
- **Where the cross-fix math happens:** a flat local map measured in **meters,
  East and North** (called **ENU** — East-North-Up). Treating a few km of
  ground as a flat sheet is accurate enough at these distances and makes the
  "where do the rays cross" arithmetic simple.

The system converts between the two at the edges. The oval (`EllipseENU`) lives
in the flat East-North meters frame, centered on the fix.

## Where this lives in the code

- **`packages/rfmesh-fusion/`** — the fuser. This is the code that takes each
  node's bearing, turns it into a ray, and crosses the rays into a single fix.
  - `geometry.py` — `bearing_to_unit_vector` turns an azimuth into a direction
    (using exactly the convention from §1: North = 0°, clockwise, East =
    `sin`, North = `cos`). `ray_ray_crossing` is the literal "where do two rays
    cross" from §3.
  - `fuser.py` — the orchestrator that batches bearings and produces one fix
    with its oval and confidence.
- **`ARCHITECTURE.md` §7** — the demo description: each node's bearing line, its
  uncertainty wedge, the crossing, and the 95% confidence ellipse drawn on the map.
- **`INTERFACES.md` §2** — `EllipseENU`, the oval value type (semi-major axis =
  the long radius, semi-minor axis = the short radius, orientation = which way
  the oval is tilted). §0 of the same file defines the WGS-84 and ENU frames
  from §7.

## Click test

Answer these in your head; the answers are right below.

1. A node reports a **bearing of 270°**. Which compass direction is the emitter, and how far away is it?
2. What's the difference between a node's **heading** and a **bearing** it reports?
3. You have one node with one good bearing. Can you place the emitter on the map? Why or why not?
4. Two nodes give you a clean cross-fix. A third node is added and its ray misses the crossing point by a lot. What does that tell you, and what can the system now do that it couldn't with two nodes?
5. Why does a cross-fix come out as an **oval** and not a **circle**?
6. Two nodes are positioned so their bearings to the emitter cross at a very **shallow** angle. Is the oval small or long? Would moving one node so the bearings cross nearer 90° help?

---

**Answers:**

1. **West.** (270° is West on the compass rose.) Distance is **unknown** — a single bearing gives direction only, never range.
2. **Heading** = which way the node's own antenna is aimed (about the node). **Bearing** = the direction to the emitter the node detected (about the target). They can be totally different numbers at the same moment.
3. **No.** One node gives one line of position (a ray). The emitter is somewhere along it, but you can't tell *where* — you need a second ray to cross it.
4. The third ray missing the knot tells you that **node's bearing is probably bad** (an echo, a calibration error). With three nodes the system can **see and flag the disagreement** — self-checking — instead of trusting a possibly-wrong two-node crossing.
5. Because each bearing is really a fuzzy **wedge**, not a thin line. The overlap of two wedges is **narrow across the crossing** but **stretched along it** — narrow one way and long the other makes an **oval**. A circle would mean equal uncertainty in all directions, which a cross-fix almost never has.
6. **Long.** A shallow crossing smears the overlap into a long, skinny oval. Yes — repositioning so the bearings cross **nearer 90°** would shrink it into a compact oval, a much tighter fix. (This is the GDOP idea, covered later.)
