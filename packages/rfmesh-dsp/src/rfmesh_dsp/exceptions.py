"""Internal exception hierarchy for ``rfmesh-dsp``.

These exceptions are deliberately *not* part of ``rfmesh-contracts``.
``ARCHITECTURE.md`` Section 3 fixes the rule: the contracts package
stays pure data + Protocol; exceptions live with the workstream that
raises them. Cross-workstream callers handle errors at the Protocol
boundary -- they do not import these symbols.

The hierarchy stays small on purpose. A new dsp-internal exception
class is justified only when a caller in the package genuinely needs to
distinguish it from a sibling class; otherwise it is a ``DspError``
with a descriptive message and an inheriting class is just noise.
"""

from __future__ import annotations


class DspError(Exception):
    """Base class for all ``rfmesh-dsp`` internal exceptions.

    Subclasses surface specific failure modes a caller may need to
    distinguish. Outside ``rfmesh-dsp`` this class is not part of the
    contract surface; the ``BearingEstimator`` Protocol's ``estimate``
    method returns ``BearingReport | None`` and surfaces failure via
    ``None`` rather than by raising (Invariant 4 surface).
    """


class NullSteeringError(DspError):
    """Raised by ``compute_null_steering_weights`` on a non-recoverable input.

    Concretely: when the loaded sample covariance is so ill-conditioned
    that no honest null can be synthesised --
    ``cond(R_loaded) > 1e8``. This mirrors WS-B-004's identical gate on
    the Capon estimator; the diagonal loading is the last line of
    defence against rank-deficient R, and beyond ``1e8`` the inverse is
    numerically meaningless.

    The function does **not** silently return high-norm weights that
    would steer attention into a noise eigendirection -- that is
    exactly the kind of silent fallback Invariant 4 / B3 forbids. A
    consumer that catches this exception decides what to do (skip the
    snapshot; fall back to MUSIC null projection; refuse to claim a
    null on the dashboard); the dsp module declines to invent an
    answer.

    A *recoverable* low-DoF result (e.g. N=2 ULA with co-bin jammer +
    signal, ``1e6 < cond <= 1e8``) does **not** raise; it returns a
    ``NullSteeringResult`` with a large ``condition_number`` so the
    consumer can read the quality flag and refuse to claim a null on
    its own terms. The split between "raise" and "return-with-flag"
    keeps low-quality-but-honest paths inspectable in tests and on the
    dashboard, while reserving the exception for genuinely unusable
    input.
    """
