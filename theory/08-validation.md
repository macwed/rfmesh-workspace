# Module 08 — Validation & Honesty: proving the error bars are real

*Builds on module 03 (sigma, honest sigma) and module 07 (the fusion ellipse). Module 03 told you sigma is everything. This module is how the team **proves** their sigmas aren't lying — automatically, on every build.*

---

## What you'll be able to do after this

- Say in one sentence **why** a lying sigma is a disaster, not just a small bug.
- Explain **Monte-Carlo simulation** in plain words: run it thousands of times, watch the scatter.
- Explain why a **simulator** lets you know the "true" answer when the real world never does.
- Read the **sigma-honesty test**: claimed sigma vs real scatter, must match within ±20%.
- Recall why the test runs at **SNR = 10, 20, 30 dB** (module 01).
- Say why **different estimators need different sigma recipes** — without any math.
- Explain the **Phase-C pessimism factor**: why the team inflates pretty simulator numbers.
- Explain **"no silent fallbacks"** and the **golden-file test**: the system fails loudly, never fakes it.

---

## Questions this answers

- Why bother validating sigma at all? Isn't a sigma just a sigma?
- What is Monte-Carlo? What's "ground truth"?
- Why does a simulator let me know the truth when the field never does?
- What's the ±20% honesty band, and why exactly 20%?
- Why test at three SNR levels (10/20/30 dB)?
- What does "claimed vs real spread" mean?
- Why can't every estimator use the same sigma formula?
- What's the Phase-C 1.5–2× inflation / "pessimism factor"?
- What does "no silent fallbacks" mean, and what's a golden-file test?

---

## Lesson

### (a) Why this even matters — recall module 03

Module 03 hammered one fact: **sigma is the most load-bearing number in the system.** Quick recall of where it goes:

- It's the **fusion weight**. Fusion trusts each bearing by `1/σ²`. Smaller sigma = bigger vote.
- It's the **ellipse size**. The oval on the operator's map is drawn straight from the sigmas.

So picture a node that **lies** — it claims `±0.5°` when its real scatter is `8°`. Module 03 already showed the damage: its weight balloons to `1/0.25 = 4`, it out-votes every honest node, and it drags the fix — and the ellipse — confidently to the wrong place. A *too-small* sigma poisons everything downstream.

And it gets caught. An RF/EW expert watching the demo sees a cheap single-antenna node claiming `±0.5°` and knows in **30 seconds** that it's not believable. A dishonest sigma isn't just a math bug — it's a credibility own-goal in front of the exact people you're trying to convince.

So the team does **not** just *hope* the sigmas are honest. They **measure** it. Mechanically. On every build. That's what this whole module is about.

> **The honesty reflex.** The rule across the codebase: every estimator must report a sigma that genuinely matches its real scatter — never a hopeful constant, never a number that makes the demo look pretty. This module is the machine that enforces it.

---

### (b) Monte-Carlo simulation — run it a thousand times and watch the scatter

Here's the catch. To check if a sigma is honest, you'd need to know **how wrong the answer really was**. But in the real world you *don't know the true answer* — that's the whole point of measuring it. You can't grade a test when you don't have the answer key.

**A simulator hands you the answer key.** Inside a simulator, *you* set the true emitter angle. You typed it in. So you know it exactly.

Now the trick. Run the same measurement **thousands of times**, each time with **fresh random noise**, and watch how much the answers scatter around the true angle you set. That real scatter **is** the honest sigma. No formula — just measured, by brute force. This is **Monte-Carlo simulation**: don't reason about the noise, *roll the dice many times and look at the spread.*

> **The bathroom-scale analogy.** Want to know if a scale is honest? Take a known **10 kg** weight and step on it **1000 times**. If the readings cluster tightly around 10 (say, 9.9 to 10.1), the scale is precise. If they spray from 7 to 13, it's sloppy — *no matter what the label promises.* You know the truth (10 kg) because you brought the weight. The simulator is your known weight.

That measured spread — the actual scatter of the answers — is what we call the **ground truth sigma**. Not what the estimator *claims*; what it actually *does*.

Here's what 200 recovered angles look like piling up around a true bearing of 137°, with the measured spread marked:

```
   Monte-Carlo: 200 runs, true angle = 137 deg, fresh noise each time

  count
   45 |                      ####
   40 |                    ########
   35 |                   ##########
   30 |                  ############
   25 |                 ##############
   20 |               ##################
   15 |             ######################
   10 |           ##########################
    5 |        ################################
    0 +----+----+----+----+----+----+----+----+----  recovered angle (deg)
      131  133  135  136  137  138  139  141  143
                          ^^^
                       true = 137
            |<----------- measured scatter ----------->|
                     this width IS the real sigma
```

Most answers land near 137. A few stragglers land out in the tails. That bell, and how wide it is, is the ground truth. **The width of that pile is the honest sigma — measured, not promised.**

---

### (c) The sigma-honesty test — the core gate

Now we have two numbers we can put side by side:

- **Claimed sigma** — what the estimator *wrote on the report* (`azimuth_sigma_deg`).
- **Real sigma** — the actual scatter the Monte-Carlo measured.

The test is dead simple: **do they match?**

The pass rule: the claimed sigma must land **within ±20%** of the real spread. Too optimistic — claims `±1°` but really scatters `±3°` — **fails the build.** Too pessimistic — claims `±5°` but really scatters `±1°` — also fails, because then fusion under-trusts a good node and throws away precision it paid for.

The test runs this check at **three signal strengths: SNR = 10, 20, and 30 dB** (recall dB and SNR from module 01 — higher dB means a louder signal over the noise). Why three? Because an honest sigma has to be honest *everywhere*, not just where the signal is strong. A weak signal (10 dB) should produce a wider, honestly-larger sigma; a strong one (30 dB) a tighter one. The estimator has to track that on its own. Testing at three levels catches a recipe that's only honest in the easy case.

Here's the band check — a bar for the claimed sigma sitting inside (or outside) the ±20% band around the real sigma:

```
  PASS  (claimed sigma sits inside the +/-20% band)

         lower (-20%)      real          upper (+20%)
              |             |                 |
   real  -----[=============O=================]-----
   sigma       \____________ band ____________/
                            |
   claimed ----------------[##]----------------       <- inside -> PASS


  FAIL  (over-optimistic: claims it's far tighter than it is)

         lower (-20%)      real          upper (+20%)
              |             |                 |
   real  -----[=============O=================]-----
   claimed [##]                                       <- way left -> FAIL
            ^
        "I'm +/-1 deg!"  ...but it really scatters +/-3 deg
```

This is **automated**. It lives in the test suite and runs on every build. And there is one iron rule, written right into the test files:

> **Do not widen the tolerance to make it pass.** If the ±20% band breaks, the *estimator* is wrong, not the test. You fix the sigma recipe — you do **not** loosen the band to make red turn green. Loosening the band to pass is exactly the dishonesty the band exists to catch.

The fusion side has its own version of this gate. It builds 1000 fixes with honest noise and checks that the true emitter lands **inside the 95% ellipse 92–98% of the time** — proving the ellipse from module 07 is *really* a 95% ellipse, not a hopeful oval. Same idea: roll the dice 1000 times, count, and refuse to widen the band.

---

### (d) Different estimators, different honest-sigma recipes

You might expect every estimator to compute its sigma the same way. They don't — and they shouldn't.

Each estimator behaves differently, so each computes its sigma a different **correct** way. L1 (the amplitude sweep) reads its sigma from the **shakiness of the parabola** it fits to the RSSI peak — a sharp, clean peak means a small sigma; a flat, wobbly one means a large sigma. MUSIC and Capon (the L2 array estimators) get their sigma from **established radar formulas** built for subspace direction-finding. You don't need the formulas. You need the principle:

> **The sigma recipe must match how that estimator actually behaves — and the Monte-Carlo test is the referee.** It doesn't care *how* you computed the sigma. It only checks that the number you wrote down matches the scatter you actually produced. Different recipes, one impartial judge.

That's why a single honesty test, pointed at each estimator in turn, keeps them all honest without anyone hand-checking the math.

---

### (e) The real world is noisier — Phase-C inflation

Here's the humble part. A simulator is **clean**. The real world adds bounce off buildings, ground reflections, and clutter the simulator's tidy noise model doesn't fully capture.

Bench testing (Phase C — module 09) showed it: at a real site, the **real sigma runs bigger than the simulator's** — roughly **1.5× to 2×** bigger in tough conditions. So a pretty `±1.5°` from the simulator might really be `±3°` on the mast.

The team's response is exactly the honesty reflex again, now turned on the *simulator itself*. They carry a **"pessimism factor"**: they don't trust clean simulator numbers blindly — they **inflate** them to stay honest in the field. It's the same instinct that built the ±20% band: assume the world is worse than your nicest measurement says, and you'll never get caught over-promising.

In the code this is a literal dial: a `pessimism_factor` knob in the demo-replay pipeline that raises the simulated noise floor. Turn it to `2.0` and the simulator gets meaner — roughly 3 dB more noise (`10·log₁₀(2)`) — so the demo rehearses against a world tougher than the clean sim. Honest by construction.

---

### (f) No silent fallbacks — the design rule behind all of it

Everything above flows from one design rule that runs through the whole system:

> **If something can't be measured honestly, it must FAIL LOUDLY — never quietly fake it.**

A guess dressed up as a measurement is the single worst thing this system can do, because it looks exactly like a real answer. So the system refuses to do it. Concrete examples you've already met:

- **An estimator returns "no bearing" instead of a guess.** If the peak is too weak or too flat to trust, L1 returns `None` (with a reason), not a made-up angle. That's the prominence gate from module 04.
- **Fusion returns a clearly-labelled LOW-confidence fallback**, not a fake-precise fix. When the geometry is bad, module 07's fuser hands back a wide, honestly-labelled result — it doesn't draw a tight ellipse it can't defend.
- **A config typo is rejected at load**, not silently ignored. A misspelled key in a YAML config is a loud error at startup (`extra="forbid"`), never a quiet "I'll just use the default."

And one more guard for the *math itself* — the **golden-file test**. You freeze a known-correct output to a file on disk (a "golden" file). Every build re-runs the code and compares the new output to the frozen one. If future code ever **drifts** — even by a tiny numeric amount — the build fails and asks "did you mean to change this?" It's a tripwire against silent regressions: the day someone accidentally breaks the DSP math, the golden file catches it before it ever reaches a demo.

Put it all together and you get the project's whole credibility story in one line: **when the system says `±3°` or draws a 95% ellipse, it is telling the truth — and there's an automated test that proves it.**

---

## Where this lives in the code

- **The sigma-honesty tests (the core gate).** `packages/rfmesh-dsp/tests/test_sigma_honesty.py` (L1), `test_l2_music_sigma_honesty.py` (MUSIC), and `test_l2_mvdr_sigma_honesty.py` (Capon/MVDR). Each runs 200 Monte-Carlo trials at SNR ∈ {10, 20, 30} dB, reseeding the noise each trial with `receiver.reseed(i)` while keeping the geometry fixed, then asserts `0.8 · claimed ≤ real_scatter ≤ 1.2 · claimed` — the ±20% band. The docstring states the rule outright: *"do NOT widen this tolerance to make the test pass. If the band breaks, the sigma estimator is wrong, not the test."*

- **The honest-ellipse Monte-Carlo (the fusion gate).** `packages/rfmesh-fusion/tests/test_honest_ellipse_monte_carlo.py` runs 1000 trials and checks the true emitter lands inside the 95% ellipse **92–98%** of the time, plus a covariance-honesty cross-check. Same rule: *"Do not widen the band."*

- **Golden-file regression.** `packages/rfmesh-dsp/tests/golden/` holds frozen `.npz` outputs (sigma tables, sweep peaks, pseudospectra, manifolds). Workspace discipline **WD-2** in `AGENTS.md` requires every new DSP function to ship a golden-file test — the tripwire against silent numeric drift.

- **The pessimism factor.** `apps/demo-replay` carries a `pessimism_factor` on the replay orchestrator that inflates the simulated noise floor (a `2.0×` factor adds ≈ 3 dB), so demos rehearse against a world tougher than the clean simulator.

- **The invariants behind it all.** `AGENTS.md` §1 — **B2 (honest sigma on every `BearingReport`)**, called *"the single most load-bearing technical invariant in the project,"* and **B3 (no silent fallbacks)**, which enumerates the loud-failure examples above.

- **The real-world inflation evidence.** `docs/phase-c-report/findings.md` — the bench campaign that grounds the simulator's sigma against real RF and feeds the "trust it less than it looks" reflex.

---

## Click test

You've got it when you can answer these without peeking:

1. Why can't you measure a sigma's honesty using real-world field data alone?
   *(In the field you don't know the true answer, so you can't tell how wrong each measurement was. A simulator lets you set the true angle, so you have an answer key.)*

2. In one sentence, what is Monte-Carlo simulation here?
   *(Run the same measurement thousands of times with fresh random noise and watch how widely the answers scatter — that scatter is the real sigma.)*

3. The bathroom-scale analogy: what's the "known weight," and what does the spread of readings tell you?
   *(The known weight is the true emitter angle you typed into the simulator; the spread of recovered angles is the estimator's real sigma, regardless of what it claims.)*

4. An estimator claims `±1°` but the Monte-Carlo scatter is `±3°`. Pass or fail, and why?
   *(Fail — it's far outside the ±20% band, and it's over-optimistic, which would over-weight the node in fusion and poison the fix.)*

5. Why run the test at SNR 10, 20, and 30 dB instead of just one level?
   *(Honest sigma must track signal strength — wider when weak, tighter when strong — so testing three levels catches a recipe that's only honest in the easy case.)*

6. Why do L1, MUSIC, and Capon each compute sigma differently, and what keeps them all honest?
   *(Each behaves differently, so each has its own correct recipe — L1 from the parabola's shakiness, MUSIC/Capon from radar formulas — and the Monte-Carlo test is the impartial referee that checks every claim against real scatter.)*

7. What is the "pessimism factor," and what honesty instinct does it come from?
   *(A deliberate inflation of clean simulator numbers — about 1.5–2× — because real sites scatter more than the sim; it's the same "assume worse than the prettiest number" reflex as the ±20% band.)*

8. Give two examples of "no silent fallbacks," and say what a golden-file test guards against.
   *(E.g. an estimator returns None instead of a guessed bearing; fusion returns a LOW-confidence fallback instead of a fake precise fix; a config typo is rejected at load. A golden-file test freezes a known-correct output and fails the build if the math ever drifts.)*
