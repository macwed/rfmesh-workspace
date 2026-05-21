# Module 00 — Mission & System Shape

*The very first lesson. It assumes you know nothing about radio, nothing about
this project, and nothing about the jargon. By the end you will understand the
whole system in one picture, and you will know why the hard part is hard.*

---

## What you'll be able to do after this

- Say, in one plain sentence, what this whole project ("rfmesh") is for.
- Explain the mission: who asked for it, what it must do, and how good it must be.
- Describe the core trick the system uses — many cheap boxes each pointing a
  direction, and a computer crossing those directions to find a spot on a map.
- Name the three "capability layers" (L1, L2, L3) and say one line about each.
- Explain *why* finding something to within 20 meters from 3 kilometers away is
  genuinely hard — and back it up with one small number you can compute yourself.

You will not be asked to derive anything or do any heavy math. You just need to
understand the shape of the thing. Later modules zoom in on each piece.

---

## Questions this answers

- What does "**RF**" mean?
- What is an "**emitter**"?
- What is "**geolocation**"?
- What is a "**jammer**", and what are "**jamming**" and "**counter-jamming**"?
- What is the "**MoD**"? What is "**BoTH3**"?
- What is a "**node**"? What does "**distributed**" mean?
- What is a "**bearing**"?
- What is a "**fusion server**"?
- What is "**ATAK**"?
- What is an "**uncertainty ellipse**"?
- What is "**triangulation**"?
- What does the project name mean — "**cooperative bearing mesh for RF emitter
  geolocation**" — word by word?
- **Why is hitting 20 m at 2–5 km hard?**

---

## Lesson

We will build the whole idea from the ground up. First the words. Then the trick.
Then a picture. Then the one number that explains why this is a real engineering
challenge and not a weekend toy.

### Step 1 — What is "RF"? What is an "emitter"?

**RF** is short for **radio-frequency**. It just means **radio waves** — the same
invisible waves that carry your FM radio station, your Wi-Fi, your phone signal,
and a TV remote's cousin. They are a kind of energy that travels through the air
at the speed of light. You can't see them or hear them directly; you need a radio
to pick them up.

Think of radio waves like ripples on a pond, except the pond is the air all
around you, and the ripples are invisible. Anything that makes those ripples on
purpose is **transmitting**.

An **emitter** is simply **anything that transmits radio**. That's the whole
definition. A few examples:

- A phone making a call (it emits to reach the cell tower).
- A drone's control link (the box on the ground that tells the drone where to go).
- A **jammer** (more on this in a second).

So when this project says "RF emitter," read it as "a thing putting out radio
waves that we want to find."

> **Greenhorn check:** "RF = radio waves. Emitter = a thing sending radio waves."
> If you can say that, you're good.

### Step 2 — What is "geolocation"?

**Geolocation** means **figuring out where something is and pinning it on a map**.
"Geo" = Earth, "location" = place. When your phone shows a blue dot for "you are
here," that's geolocation.

In this project, we are not locating ourselves. We are locating *someone else's*
radio transmitter — and they are not telling us where they are. We have to figure
it out from the outside, just by listening to their radio waves.

### Step 3 — What is a "jammer"? What is "jamming"?

Normally radios are polite: each one transmits its own signal, and receivers can
pick it out. A **jammer** is a radio that is deliberately rude. Instead of sending
a useful message, it **blasts radio noise** on the same frequencies other people
are using — loud enough to drown them out.

Here is an everyday analogy. Imagine you're trying to have a quiet conversation
with a friend across a room. Now someone walks up with a megaphone and just plays
static at full volume. You can no longer hear your friend — not because your ears
broke, but because the noise buried the conversation. **That is jamming.**

- **Jamming** = blasting radio noise to drown out other people's communications,
  or to drown out **GPS** (the satellite signal phones and vehicles use to know
  where they are). On a battlefield, the enemy jams your radios and your GPS so
  your forces go deaf and blind.
- **Counter-jamming** = fighting back against a jammer. The most useful first step
  is simple: **find where the jammer is.** Once you know the jammer's exact spot,
  you can do something about it (move it, shut it down, or target it). This whole
  project is a counter-jamming tool: **it locates the jammer.**

> **Greenhorn check:** A jammer is the megaphone-of-static. Jamming = playing the
> static. Counter-jamming = finding who's holding the megaphone.

### Step 4 — Who wants this, and how good must it be? (The mission)

This system is built for a real event: the **BoTH3 Counter-Jamming Challenge**,
run by the **Belgian Ministry of Defence**.

- **MoD** = **Ministry of Defence** — the government department in charge of a
  country's military. (Belgium's, in this case.)
- **BoTH3** is just the name of the challenge / competition where we demonstrate
  the system to expert judges.

The mission, stated plainly:

> Find an enemy jammer's (or transmitter's) antenna to within about **20 meters**,
> when that jammer is **2 to 5 kilometers away**, and drop a marker on a military
> map app so the right people can act on it.

The map app is called **ATAK** (we'll cover it below). "Within 20 meters" means:
if the jammer's antenna is really sitting at some spot, our marker on the map
should land within 20 meters of that true spot. 20 meters is roughly the length
of a basketball court. From 2–5 km away — that's 2,000 to 5,000 meters, more than
a 20-minute walk — that is a very small target. Hold onto that thought; Step 9
shows exactly why it's hard.

### Step 5 — The core trick: each box only measures a *direction*

Here is the clever idea at the heart of the whole project.

You might think each radio box has to somehow compute the jammer's position by
itself. It does **not**. That would be hard and expensive. Instead, each cheap box
does just **one** simple job: it figures out **which direction the radio waves are
coming from**. Not how far. Just the direction — like a compass needle that points
at the noise instead of pointing north.

That direction is called a **bearing**.

A **bearing** is a **compass direction toward something**, given as an angle.
North is 0°, East is 90°, South is 180°, West is 270°. So "the jammer is at
bearing 130°" means "point yourself a bit south of east, and that's the way toward
the jammer." A bearing tells you the *line* the jammer is on, but not *how far
along* that line it sits.

```
            N (0 deg)
            |
            |
 W (270) ---+--- E (90 deg)
            |
            |
            S (180)

  Bearing 130 deg points down-and-right,
  between East and South:

            N
            |
            |
 W ---------+--------- E
             \
              \   <-- "the emitter is somewhere out this way"
               \
                * (bearing ~130 deg)
```

One box, one bearing. That box now knows the jammer lies *somewhere along a line*
in that direction — but it has no idea whether the jammer is 1 km or 4 km out
along that line. One direction alone is not enough to find a point.

### Step 6 — Crossing the lines: triangulation

So how do we get an actual point if each box only gives a direction? We use
**more than one box**, sitting in **different places**.

Here's the everyday version. Imagine a hot-air balloon floating far away. You and
a friend stand 100 meters apart. You both point straight at the balloon. Your arm
makes one line; your friend's arm makes another. The two lines **cross** at exactly
one spot — and that spot is where the balloon is. Neither of you measured the
distance. You just crossed two directions.

```
   You                          Friend
    \                            /
     \                          /
      \                        /
       \                      /
        \                    /
         \                  /
          \                /
           \              /
            \            /
             *  <-- balloon is where the two lines cross
```

That's it. That is **triangulation**: combining two or more directions (bearings)
from known viewpoints to pin down a single point where they cross. The more
viewpoints you add, the more confident you can be. (Real lines never cross
*perfectly* because every measurement has a little error — we deal with that in
Step 8.)

In our system, the "you and your friend pointing" are the radio boxes, and the
"pointing" is each box measuring its bearing toward the jammer.

### Step 7 — Now the project name makes sense, word by word

The official one-liner for this project is:

> **"A cooperative bearing mesh for RF emitter geolocation."**

That sounds like a mouthful, but you now know every word. Let's unpack it:

- **RF emitter** — a thing transmitting radio waves (the jammer). *(Step 1)*
- **geolocation** — pinning its position on a map. *(Step 2)*
- **bearing** — the direction-angle each box measures toward the emitter. *(Step 5)*
- **mesh** — **many boxes networked together**, talking to each other and to a
  central computer. (Like a fishing net: lots of little knots all connected. A
  "mesh" of devices = a web of devices working as one.)
- **cooperative** — **the boxes work together.** No single box finds the answer
  alone. Each contributes its one bearing, and the answer comes from combining
  them. They cooperate.

Each box in the mesh is called a **node**. A **node** is a small box that contains
a **radio receiver** (the part that listens to radio waves) plus an **antenna**
(the metal part that actually catches the waves out of the air). One node = one
listening post that produces one bearing.

The nodes are **distributed**, which just means they are **spread out in different
places** rather than all bunched in one spot. (You *need* them spread out — go back
to Step 6: if you and your friend stand in the exact same place, your pointing
lines are the same line and never cross. Spreading out is what makes the crossing
work.)

> **Greenhorn check — the whole name in one breath:** "A bunch of spread-out boxes
> (a distributed mesh of nodes) that each measure a direction (bearing) toward a
> radio transmitter (RF emitter) and work together (cooperative) so a computer can
> figure out where it is (geolocation)."

### Step 8 — Where do the bearings go? The fusion server, and honest uncertainty

Each node measures its bearing, but a node does **not** cross the lines itself.
Instead, every node **ships its bearing** (sends it over the network) to one
central computer called the **fusion server**.

**Fusion** here means **combining many measurements into one answer**. (Like
fusing several streams into one river.) The fusion server collects all the bearings
that arrived at roughly the same moment, **crosses the lines** (the triangulation
from Step 6), and computes the single best estimate of where the emitter is.

But here's an important honesty point. In the real world, no bearing is perfect.
Each node's direction is a little bit off — maybe by a few degrees — because of
noise, reflections, and cheap hardware. So when you cross slightly-wrong lines,
they don't all meet at one perfect point. They make a small fuzzy region.

The fusion server is honest about this. Along with the position, it reports an
**uncertainty ellipse**: an **oval drawn on the map showing the region the emitter
is probably inside.** ("Ellipse" is the math word for an oval/squashed circle.)

- A **small, tight** ellipse means "we're confident — the emitter is almost
  certainly right here."
- A **big, stretched** ellipse means "we have a rough idea, but it could be
  anywhere in this larger area."

This is a feature, not an apology. A system that *only* gave a dot would be lying
about how sure it is. An oval tells the operator exactly how much to trust the
answer.

```
   Tight (confident):            Loose (less sure):

        .--.                      .---------.
       ( ** )                   (   ***       )
        '--'                    (    ***        )
                                  '-----------'

   small oval = "right here"     big oval = "somewhere in here"
```

### Step 9 — The output: a marker in ATAK

The final product is not a number on a screen for an engineer. It's a marker on a
map that a soldier or operator already uses.

**ATAK** is a **military map app that runs on a tablet or phone.** (The name comes
from "Android Team Awareness Kit.") Think of it as a tactical version of Google
Maps that military and emergency teams use to see where everyone and everything is.
It can show friendly units, hazards, and — in our case — **hostile emitters**.

So the very end of our pipeline is: the fusion server's answer (position + the
uncertainty ellipse) gets sent into ATAK and shows up as a **marker** — a labeled
pin on the map — with the oval drawn around it. Now an operator looking at their
tablet sees: "Hostile jammer, here, probably within this oval." That's the whole
point of the system, made visible on a screen someone already trusts.

### Step 10 — The three capability layers (L1, L2, L3) — names only, for now

Each node can do up to three different "levels" of work. You don't need details
yet — just collect the names, because the rest of the course is built around them.

- **L1 — cheap angle-finding.** Spin a directional antenna around (an antenna that
  hears best in one direction, like cupping your hand to your ear and turning your
  head). The direction where the signal is **strongest** is the bearing. Simple
  and cheap, but only roughly accurate. *(Taught in full later.)*

- **L2 — precise angle-finding.** Use a fixed cluster of several antennas (an
  "array") and some clever signal processing to pin the direction down much more
  tightly than L1 — without any spinning. More expensive, much sharper bearings.
  *(Taught in full later.)*

- **L3 — labeling what the emitter is.** Use **machine learning** (a computer
  program that learns to recognize patterns) to look at the signal and guess *what
  kind* of thing it is: a drone control link? a particular type of jammer? a phone?
  This adds a label to the marker, not a position. *(Taught in full later.)*

The neat part: the fusion server treats L1 and L2 bearings the same way — it just
trusts the sharper ones more. So a mix of cheap and fancy nodes can all work
together in the same mesh.

### Step 11 — The number that explains the whole challenge

We promised you a reason that "20 meters at 2–5 km" is genuinely hard. Here it is,
and you can check it with a calculator.

A bearing is a *direction* — an angle. The further away the target is, the more a
tiny mistake in the angle smears you sideways. A 1-degree mistake is nothing when
the target is across the room. But across kilometers, that same 1 degree fans out
into a big sideways miss.

Picture standing at the corner of a huge right-angle triangle. One side runs
straight out to the target (3 km long). If your aim is off by just 1 degree, the
*sideways* gap at the far end is:

```
   you
    o----------------------------------  (3000 m straight to target)
     \  )  <- 1 degree of aiming error
      \
       \
        \
         \   sideways miss at the far end
          \
           v
```

The sideways miss is:

```
   sideways miss  =  distance  x  tan(angle error)
                  =  3000 m    x  tan(1 degree)
                  =  3000 m    x  0.01746
                  ~=  52 meters
```

(`tan` is the tangent button on your calculator — for tiny angles it just measures
how fast the line spreads sideways. You don't need to understand *why* it works;
you only need to *use* it to see the size of the problem.)

Read that result again: being off by **a single degree** at 3 km already puts you
**~52 meters** off — and our target is **20 meters**. So one node, off by even one
degree, badly misses the goal on its own.

That single fact drives the entire rest of this course. It means two things must be
true:

1. **Each bearing must be accurate to a small fraction of a degree** (which is why
   L2's precise angle-finding matters), **and/or**
2. **Many bearings must be combined** so their errors partly cancel out (which is
   why we have a *mesh* and a *fusion server* that crosses many lines and reports
   an honest ellipse).

Everything else you'll learn — the antennas, the signal processing, the
line-crossing math, the uncertainty ovals — exists to beat this one number.

### The whole system in one picture

Here is the entire pipeline you just learned, top to bottom:

```
                          (((  EMITTER / JAMMER  )))
                                     *
                                    /|\
                          radio    / | \    radio waves
                          waves   /  |  \   spreading out
                                 /   |   \
                                /    |    \
                               /     |     \
                              /      |      \
                       bearing    bearing    bearing
                          /          |          \
                         /           |           \
                   [ NODE A ]   [ NODE B ]   [ NODE C ]      <- distributed mesh
                   receiver+    receiver+    receiver+          of cheap boxes;
                   antenna      antenna      antenna            each measures ONE
                      |            |            |               direction (bearing)
                      |            |            |
                      '------------+------------'
                                   |   (nodes ship their bearings
                                   v    over the network)
                          +-------------------+
                          |  FUSION  SERVER   |   <- crosses the bearing lines
                          |  (crosses lines,  |      (triangulation), computes
                          |   builds ellipse) |      position + uncertainty oval
                          +-------------------+
                                   |
                                   v
                          +-------------------+
                          |   ATAK MAP APP    |   <- marker dropped on the map,
                          |    [ X ]  .--.     |      with the uncertainty ellipse
                          |          ( () )    |      drawn around it, for the
                          |           '--'     |      operator to act on
                          +-------------------+
```

That picture *is* the project. Every later module is a close-up of one box in this
diagram.

**A real photo to anchor it:** here's what an actual antenna array (the kind L2
precise nodes use) looks like — a set of antennas working as one cluster:
[KrakenSDR five-antenna direction-finding array (Wikipedia / Wikimedia
Commons)](https://commons.wikimedia.org/wiki/File:KrakenSDR.jpg). Picture five of
these clusters spread across a few kilometers, each one quietly pointing at the
same jammer.

---

## Where this lives in the code

You don't need to open these yet — this is just so you know where the "official"
version of what you just learned lives.

- **`C:\Users\b\Desktop\belgia\rfmesh-workspace\README.md`** — the project's front
  page. The opening lines define rfmesh as a "Cooperative bearing mesh for RF
  emitter geolocation" and name the BoTH3 challenge (Belgian MoD).

- **`C:\Users\b\Desktop\belgia\rfmesh-workspace\ARCHITECTURE.md`** — the "why"
  document. The sections that match this lesson:
  - **§0 "What rfmesh is"** — the one-paragraph definition: distributed nodes, each
    with an antenna, compute *bearings* locally and ship them to a fusion server
    that cross-fixes them into positions with honest uncertainty, shown in ATAK.
    This is Steps 5–9 above.
  - **§1 "The three capability layers"** — the full version of Step 10 (L1
    detection, L2 precision DF, L3 classification).
  - **§7 "What the demo shows"** — what the judges actually see on screen,
    including the cross-fix with its confidence ellipse and the ATAK marker that
    "shrinks visibly as nodes are added" (Steps 8–9).

---

## Click test

If the lesson worked, you can now answer these out loud, in plain words. (Try it
before peeking at the hints.)

1. **A friend asks: "What does this whole project do?"** Answer in two or three
   sentences, using the words *emitter*, *bearing*, *mesh*, and *fusion server*.
   *(Hint: spread-out boxes each measure a direction toward a radio transmitter,
   and a central computer crosses those directions to find it on a map.)*

2. **Why can't one single node find the jammer by itself?** What does a node
   actually measure, and why do you need a second node somewhere else?
   *(Hint: a node only gives a direction, not a distance — one direction is a whole
   line; you need two crossing lines to get a point. That's triangulation.)*

3. **Why is "20 meters at 3 km" hard — and what's the one number that proves it?**
   *(Hint: a 1-degree aiming error at 3,000 m smears you about 52 meters sideways,
   which already blows past the 20-meter goal — so bearings must be very precise
   and/or many must be combined.)*
