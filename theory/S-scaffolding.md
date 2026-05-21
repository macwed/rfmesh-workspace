# Module S — Scaffolding: the software & hardware plumbing

> **Track:** scaffolding. This explains the *plumbing* around the radio math —
> the simulator, the data contracts, the wires, the test beacon, the timing,
> the map output, and the rules that kept the project honest. It blocks
> nothing. Read it any time. There is no DSP math here — only systems ideas.

---

## What you'll be able to do after this

- Explain how the team built and tested the whole system **with no radio plugged in**.
- Say what a "contract" is, why it's frozen, and why that let many people (and AI agents) work at once without colliding.
- Read the little message frame that drives the antenna motor, and name its three tricks (TLV, CRC, COBS).
- Explain why the project uses **Angle-of-Arrival** instead of **Time-Difference-of-Arrival** — and why that means it needs **no GPS**.
- Describe how a hostile-emitter marker, with an honest uncertainty oval, lands live on a soldier's tablet.
- Name the three habits (golden files, ADRs, a review council) that made a hackathon codebase trustworthy.

---

## Questions this answers

- What is the "SyntheticReceiver" / simulator, and what does "same interface" mean?
- Why does a fake radio let many people build in parallel?
- What's a Pydantic model? A Protocol? A "data contract"? What does "frozen" mean?
- Why is the dependency graph a **star**? What's a schema version?
- What's UART? TLV? CRC-16? COBS? Why bother with all three?
- What's the LoRa beacon, and what's a "controlled reference emitter"? (And SF7 / 868.1 MHz?)
- What's NTP, and what's the ~10 ms about?
- AoA vs TDOA — what's the difference, and why was AoA chosen? Why no GPS or compass?
- What are CoT, ATAK, PyTAK, FreeTAKServer? What's the "equirectangular polygon"?
- What's a golden-file test? An ADR? A council review?

---

## Lesson

### (a) The simulator — a flight simulator for a radio

Imagine you're training pilots, but you only have one real plane and twenty
students. So you build flight simulators. A good simulator is so faithful that
the cockpit controls behave **exactly** like the real plane — same throttle,
same instruments, same response. The student can't tell the difference, and
neither can the cockpit wiring.

The project did the same thing for radios. It built a fake radio called the
**SyntheticReceiver**. A real radio hands the software a stream of raw signal
samples ("IQ samples" — module 01). The SyntheticReceiver hands over the *same
kind* of stream, but the samples are **made up**: invented from a pretend map
of where emitters are sitting, complete with realistic noise and signal bounce
("multipath" — module 09).

The key word is **interface**. An interface is the small, fixed list of buttons
a thing offers: a real radio offers `open`, `configure`, `read`, `close`. The
SyntheticReceiver offers the **exact same buttons**, in the exact same order,
with the exact same behavior. So the rest of the system literally cannot tell
whether it's talking to a $30 USB dongle or a pile of math. You swap one for
the other by changing a single word in a config file: `driver: "sim"` instead
of `driver: "rtlsdr"`.

**Why this matters so much.** Hardware shows up late, breaks often, and only
one person can hold it at a time. A simulator has none of those problems. So
while the radios were still in the mail, the DSP team, the fusion team, the
map team, and a swarm of AI coding agents could **all** build and test their
pieces at once, end to end, against a fake radio that told no lies. Hardware
integration became one short focused session at the end — not a daily traffic
jam. The simulator isn't a test toy bolted on the side; it's treated as
production code, reviewed as strictly as everything else. A simulator that
lies would turn every test into fiction.

```
   config word: "rtlsdr"            config word: "sim"
        │                                 │
        ▼                                 ▼
  ┌───────────────┐                ┌───────────────────┐
  │  Real radio   │                │ SyntheticReceiver │
  │ (USB dongle)  │                │  (pretend IQ from │
  │               │                │  a made-up map)   │
  └───────┬───────┘                └─────────┬─────────┘
          │  open/configure/read/close   ◄── SAME BUTTONS ──►
          └───────────────┬────────────────────────┘
                          ▼
                ┌──────────────────────┐
                │ The rest of the system│  ← cannot tell which one it's holding
                └──────────────────────┘
```

---

### (b) Contracts & protocols — the frozen star

Now picture a big building project with many crews working at once. The
plumber, the electrician, and the carpenter never talk to each other directly.
Instead, everyone agrees on **one shared blueprint**. As long as each crew
builds to the blueprint, their work fits together when they meet — even though
they never coordinated day to day.

In this codebase, the blueprint is a small package called **rfmesh-contracts**.
It fixes the exact **shape** of every piece of data that passes between teams,
and the exact **list of actions** each component must offer. It is two ideas:

**1. Pydantic models = strict data forms.** A Pydantic model is like a paper
form with labeled, typed boxes that **rejects anything malformed at the door**.
If you misspell a field, or put text where a number goes, or add a stray extra
field, it's refused *immediately* when the message is parsed — not silently
ignored to cause a weird bug three steps later. (Every form in this project is
set to `extra="forbid"`: an unexpected field is a loud error, never a shrug.)

**2. Protocols = agreed lists of what a component must *do*.** A Protocol is
like the power-socket standard. The wall doesn't care who made your plug or how
it works inside — if it fits the socket, it works. The contract says "any
Receiver must offer `read()`," and that's it. It does **not** say how. The real
radio and the simulator both "fit the socket," so both just work. Crucially,
nobody has to *inherit* or import machinery from the contract to fit it — you
fit it simply by having the right shape. (That's the whole point of choosing
"Protocols" over the older "base class" style; it keeps the teams from getting
tangled into each other.)

The shape this creates is a **star**. Everything depends on the one contracts
package in the middle. Nothing on the outside depends on anything else on the
outside. The SDR team doesn't import the fusion team's code; the map team
doesn't import the node team's code. They only ever touch the hub.

```
                       ┌──────────┐
                       │   SDR    │
                       └────┬─────┘
                            │
        ┌──────────┐        │        ┌──────────┐
        │   OPS    │────┐   │   ┌────│   DSP    │
        └──────────┘    │   │   │    └──────────┘
                        ▼   ▼   ▼
                   ┌──────────────────┐
                   │    CONTRACTS     │   ← the hub everyone shares
                   │ (the blueprint)  │
                   └──────────────────┘
                        ▲   ▲   ▲
        ┌──────────┐    │   │   │    ┌──────────┐
        │   NODE   │────┘   │   └────│  FUSION  │
        └──────────┘        │        └──────────┘
                            │
                  (no spokes between the outer parts —
                   that's what keeps the work from colliding)
```

**Bonus: the version stamp.** Every message carries a **schema version**
number (today, `"1.1.0"`). If the blueprint ever changes, this number bumps.
Any team still built against the old blueprint gets caught **automatically** by
the type checker before a single wrong message can travel — a tripwire, not a
hope. Only the lead may change the blueprint, and only through a written
decision record (see part g). That's why it's called **frozen**: not "never
changes," but "changes only through one careful, announced front door."

---

### (c) The servo link — talking to the antenna motor

An L1 node finds direction by physically **turning** a directional antenna and
watching where the signal is strongest. A small motor (a "servo") does the
turning. The computer tells the motor what angle to point at, over a plain
serial wire — **UART**, the simplest "send bytes down a wire one after another"
link, the same idea as an old serial cable.

A USB cable in a noisy field is not a clean place. A flipped byte could make
the antenna jump to the wrong angle and quietly poison every bearing. So the
team wrapped the motor commands in a tiny, well-tested protocol with three
jobs, each a one-line idea:

- **TLV — "Type, Length, Value."** A tidy way to pack a message: first say
  *what it is* (Type, e.g. "MOVE"), then *how long the data is* (Length), then
  *the data itself* (Value). The reader always knows how much to read and what
  it means. No guessing.

- **CRC-16 — a checksum.** A short 16-bit number computed from the message
  contents. The sender attaches it; the receiver recomputes it and compares. If
  even one byte got corrupted in transit, the two numbers won't match and the
  frame is thrown away. Think of the parity trick in tic-tac-toe, but far
  stronger — strong enough that random line noise practically never sneaks past.

- **COBS — the framing trick.** How does the receiver know where one message
  ends and the next begins? COBS reserves one special byte value — zero — to
  mean "message ends here," and cleverly rewrites the data so a zero never
  appears *inside* a message. So the receiver just reads until it hits a zero,
  and it knows it has exactly one clean frame.

If anything is wrong — bad framing, bad checksum, unknown command — the receiver
**silently drops the frame and waits for the next zero** to resync, rather than
acting on garbage. The result is a small, robust, byte-exact protocol so a
flaky cable never makes the antenna lie about where it's pointing.

```
  one frame on the wire:

  ┌─────[ COBS wrapper ]───────────────────────────────────┐
  │   ┌──────┬────────┬───────────────┬──────────┐         │
  │   │ TYPE │ LENGTH │     VALUE      │  CRC-16  │   0x00  │
  │   │ "what"│ "how  │  "the data"    │ "is it   │  "end   │
  │   │      │  long" │                │ intact?" │ marker" │
  │   └──────┴────────┴───────────────┴──────────┘         │
  └────────────────────────────────────────────────────────┘
       └── COBS guarantees no 0x00 hides inside here ──┘
```

---

### (d) The LoRa beacon — a practice target with a known answer

To test direction-finding on the bench, you need something to point at whose
answer you already know. So the team built a small transmitter: the **LoRa
beacon**. It sits at a **known spot**, on a **known frequency** (868.1 MHz,
using **LoRa** — a long-range, low-power radio mode; the "SF7" setting just
means short, fast little packets), sending a **known signal** once a second.

This is a **controlled reference emitter** — engineer-speak for "a target with
a guaranteed right answer." It's the eye-chart at a known distance you use to
test eyesight: because you know exactly what the chart says, you can measure how
well the eye reads it. Because the beacon's position and signal are certain, the
team can check whether the direction-finder points the right way, and how
tightly, before ever trusting it on a real unknown emitter. To the receiver,
the beacon is just RF energy at a known center — it doesn't "know" it's a test
rig. That honesty is the point.

---

### (e) Timing — why AoA, not TDOA (and why no GPS)

Computers on a network keep their clocks roughly in step using **NTP**, the
ordinary internet clock-sync method. NTP over a local network is good to about
**~10 milliseconds**. That's loose — but loose is fine here, and *that* is a
deliberate, load-bearing design choice. Here's the contrast.

There are two big families of ways to locate an emitter from several listening
nodes:

- **AoA — Angle of Arrival.** Each node measures a **direction** (a bearing)
  to the emitter. Draw a line from each node along its bearing; where the lines
  cross is the emitter. This is what this project does. It only needs every
  node's bearings to refer to *roughly the same moment*, so ~10 ms NTP sync is
  plenty of margin.

- **TDOA — Time Difference of Arrival.** Compare the **exact arrival time** of
  the signal at each node; tiny time differences pin the location down. It can
  be very precise — but it needs **nanosecond** clock sync and GPS-disciplined
  timing. Radio waves move ~30 cm per nanosecond, so a clock off by even a
  microsecond throws the answer off by hundreds of meters.

The project deliberately chose AoA, for a blunt reason: **the enemy jams GPS
first.** TDOA leans on GPS and ns-level timing — exactly the infrastructure
that disappears in a contested environment. AoA needs only cheap NTP timing and
no GPS at all. It also degrades *gracefully*: multipath just widens the
uncertainty oval (honest), whereas it makes TDOA's answer plain wrong.

```
        AoA  (lines crossing)                 TDOA  (timing rings)

   N1 \                    / N2          N1 (•)         (•) N2
        \                /                    \   rings  /
         \   ┌──────┐  /                       (  ( ● ) )    ← intersection of
          \  │emitter│/                         (  rings )      time-difference
           \ │  ●   ├/                           ( from )       hyperbolas
            \└──────┘                             ( each )
             ╳  ← bearings intersect              ( node )
            /        \                       N3 (•)
           /          \                      needs NANOSECOND clocks + GPS
       N3 /            \  loose ~10 ms       (jammed first)
          each node measures a DIRECTION
```

And because of the same philosophy, the system needs **no GPS or compass on
the critical path**: node positions are **surveyed** once at setup (write the
location into the config), and each antenna's **heading is set by aiming at a
known landmark** — a mast, a chimney, a building corner — and reading the
azimuth off a map. A magnetometer at a metal mast full of motors and cables
would just measure local junk anyway. Survey-and-aim is more accurate and can't
be jammed.

---

### (f) The map output — getting it onto the soldier's tablet

The whole point is a marker the operator can act on. Soldiers use **ATAK** —
the Android Team Awareness Kit, a tactical map app on a phone or tablet. ATAK
speaks a standard message format called **CoT** ("Cursor on Target") — a small,
agreed XML message that says "here's a thing, at this spot, of this type." The
team sends those messages using a library called **PyTAK**. (They usually flow
through a **FreeTAKServer** — a free server that relays CoT messages to all the
connected tablets.)

There's one wrinkle. The fusion math produces an **uncertainty ellipse** — an
oval showing how sure it is of the emitter's location. But ATAK can't draw a
true ellipse. So the system **approximates the oval as a many-sided polygon**
(72 little segments) and ships that. To place those polygon corners on the
globe, it converts the local oval-in-meters into latitude/longitude using a
simple flat-map (**equirectangular**) approximation — accurate to well under a
meter out to a few kilometers, which is exactly the operating range. (It uses
the proper Earth-curvature radius at that latitude, so it stays honest even up
north — no need to pull in a heavyweight geography library.)

The payoff is the demo's most honest moment: a hostile-emitter marker on the
operator's tablet, wrapped in an uncertainty oval that **shrinks visibly as
more nodes report** and **grows if a node is lost**. The system degrades out
loud — it never pretends to be more certain than it is.

---

### (g) How the project kept itself honest

A few habits made a hackathon codebase unusually trustworthy. **Golden-file
tests** freeze a known-good output to a file; if a future change alters the math
even slightly, the test fails loudly — silent numeric drift is impossible.
**ADRs** (Architecture Decision Records) are short documents that write down
every important decision *and why* — so "why not TDOA?" or "why Protocols not
base classes?" has a permanent, reasoned answer instead of being re-argued
forever. And a **council** of review agents — an architect, a code-reviewer, an
RF specialist, and a demo-integrity checker — reviews every change before it
lands, each with veto power in its lane. Frozen contracts, recorded decisions,
and a standing review board: that's why this code is more solid than its origins
would suggest.

---

## Where this lives in the code

- **The simulator:** `packages/rfmesh-sdr/src/rfmesh_sdr/simulator/synthetic_receiver.py`
- **The contracts (the hub):** `packages/rfmesh-contracts/` — data forms in `messages.py` and `config.py`, the `Protocol` action-lists in `protocols.py`, the version stamp in `version.py`
- **The servo link:** `packages/rfmesh-servo/` + the firmware C files `firmware/main/{cobs,crc16,protocol}.c`, with the frozen wire spec at `docs/wire-protocols/servo_uart_v1.md`
- **The LoRa beacon:** `firmware-beacon/lora_beacon_spec.md`
- **Transport & the AoA-vs-TDOA rationale:** `packages/rfmesh-node/src/rfmesh_node/bearer/` (WiFi/LoRa transport); the reasoning is in `ARCHITECTURE.md` §6, `INHERITED_CONTEXT.md` §2.1, and `docs/adr/ADR-003-no-gnss-no-tdoa-no-magnetometer.md`
- **The map output:** `packages/rfmesh-cot/` — the PyTAK publisher in `publisher.py`, the CoT XML in `markers.py`, the equirectangular polygon in `ellipse.py`
- **Governance:** `docs/adr/` (the ADRs), and `CLAUDE.md` (the council protocol)

---

## Click test

Quick check — answer these in your head; each is one sentence.

1. **The swap.** What single change tells the system to use the fake radio
   instead of a real one, and why can't the rest of the system tell the
   difference?
2. **The star.** Why does the SDR team's code never import the fusion team's
   code — and what sits in the middle that they *both* depend on?
3. **The form at the door.** If a config file has a misspelled field name, what
   happens, and which property of the Pydantic models causes it?
4. **Three tricks.** Match each to its job: TLV / CRC-16 / COBS →
   *detect corruption* / *mark where the message ends* / *pack what-it-is,
   how-long, the-data*.
5. **The big contrast.** AoA needs ~10 ms timing; TDOA needs nanoseconds and
   GPS. In one sentence, why did the project pick AoA?
6. **No compass.** If there's no magnetometer, how does a node know which way
   its antenna is pointing?
7. **The oval on the tablet.** ATAK can't draw a true ellipse — so what does
   the system send instead, and what does it mean when that shape **shrinks**
   during the demo?
8. **Staying honest.** Name the test that freezes known-good output so the math
   can't silently break, and the document type that records *why* a decision
   was made.

<details>
<summary>Answers</summary>

1. Change the config word to `driver: "sim"`; the simulator offers the **exact
   same interface** (`open/configure/read/close`) as a real radio, so callers
   can't distinguish them.
2. Because the architecture is a **star**: every team depends only on the
   central **rfmesh-contracts** package, never on each other — that's what lets
   them build in parallel without colliding.
3. It's **rejected loudly at parse time** (not silently ignored), because every
   model is set to `extra="forbid"`.
4. TLV = pack what-it-is/how-long/the-data; CRC-16 = detect corruption;
   COBS = mark where the message ends.
5. The enemy jams GPS first, and AoA needs neither GPS nor nanosecond timing —
   just loose NTP — so it keeps working in a contested environment.
6. Its **heading is surveyed by aiming at a known landmark** and reading the
   azimuth off a map (set once in the config).
7. A **many-sided polygon** approximating the uncertainty ellipse (placed via an
   equirectangular lat/lon conversion); when it **shrinks**, more nodes are
   reporting and the fix is getting more certain.
8. A **golden-file test**; an **ADR** (Architecture Decision Record).

</details>
