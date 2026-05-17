"""L2 null-steering: MVDR weight synthesis, application, and receive pattern.

The dual-use sibling of the Capon DoA estimator (``l2_mvdr.py``). Same
phase-coherent array, same sample covariance ``R``, *different
product*: this module synthesises a receive-weight vector ``w`` that
preserves unit response toward a configured **look** direction and
implicitly nulls whatever strong off-look interferer is present in
``R`` (typically the jammer). The pitch caption -- HANDOFF Section 0
Advantage 4 -- is *"One matrix, two products: target geolocation for
kinetic effect, null-steering for own-comms protection -- back-to-back
from one R per snapshot."*

THIS MODULE IS A UTILITY, NOT A ``BearingEstimator``
----------------------------------------------------
``compute_null_steering_weights`` does **not** return a
``BearingReport``. It does not advertise a ``method`` property. It is
called from the node runtime (or, in v1.1.0, from the ops-side
dashboard with replayed IQ) as one of two products derived from the
same ``R`` -- not as a third L2 estimator. ADR-008 Section D6 binds
this; the test ``test_module_does_not_grow_a_bearing_estimator``
catches the temptation in review.

The function signature takes the **look** steering vector (the signal
direction, whose response is preserved), NOT the null direction. The
jammer is implicit in ``R``. The MVDR distortionless-response formula
``w = R^{-1} a / (a^H R^{-1} a)`` solves the optimization
*"minimize w^H R w subject to w^H a_look = 1"*; the minimum is
achieved by canceling the dominant non-look power in R, which is the
jammer. Calling the argument ``null_steering_vector`` is the mistake
an RF/EW jury catches in 10 seconds (RF-DSP review correction #1,
folded into ADR-008 D6).

DIAGONAL LOADING IS LIGHT, NOT HEAVY
------------------------------------
``compute_null_steering_weights`` uses ``diagonal_loading_factor =
1e-6`` by default, **three orders of magnitude lighter than Capon's
1e-3**. The reason is structural: diagonal loading inflates the
*smallest* eigenvalue of ``R``, which in MVDR null-steering is the
jammer-subspace direction of ``R^{-1}`` -- so it *fills in the null*.
Capon's heavier 1e-3 default exists for *peak* stability of the DoA
spectrum (where the loading does not directly govern the depth of
anything) and is wrong for null-steering. Trade-offs:

* ``1e-6`` caps achievable null depth at roughly 60 dB on a clean
  simulator scenario (more than the project ever claims; see
  ``ADR-008 D8`` on the ``<=20 dB`` UI-claim cap).
* ``1e-3`` would cap depth at ~30 dB and waste the dual-use story.
* Going below ``1e-6`` risks ``R^{-1}`` becoming unstable on real
  finite-T data with imperfect calibration; ``1e-6`` is the sweet
  spot at which the loading does not perturb the null direction by
  any practically observable amount.

The ``test_loading_default_does_not_fill_null`` test pins this:
default loading produces >=30 dB depth on the noise-free 2-emitter
scenario, while Capon's 1e-3 loading produces <=22 dB on the same
input.

FORWARD-BACKWARD SMOOTHING IS ON BY DEFAULT
-------------------------------------------
Opposite of WS-B-004 (Capon DoA spectrum)'s default-OFF. Real-world
multipath produces *coherent* arrivals (a ground reflection of the
jammer is correlated with the jammer itself) that MVDR cancels poorly
without FB smoothing -- the same R + alpha*J R* J = R failure mode
the textbook treatment of coherent sources addresses. Null-steering is
the use case most exposed to this failure (a jammer with multipath is
the canonical operational scenario), so the default reverses:
``use_forward_backward = True``.

WHAT ``null_depth_db`` MEANS HERE
---------------------------------
The literature offers several quantities that get loosely called "null
depth". The one this module reports, and the one Maciej quotes on
stage, is the **look:null ratio of the receive pattern**: how much
quieter is the deepest notch in the array response than the look
direction. Concretely:

    null_depth_db = look_gain_db - min over a search window of
                    10 * log10(|w^H a(theta)|^2)

where the search window is centred on the principal-eigenvector-
implied jammer azimuth and spans +/- 5 degrees -- enough to catch
finite-T estimation noise on the eigenvector direction without
double-counting any unrelated array-manifold notches.

Computing this requires the **array geometry** (to evaluate
``a(theta)`` on the scan grid). This module's
``compute_null_steering_weights`` therefore takes the geometry as a
keyword argument set (``array_geometry``, ``n_elements``,
``element_spacing_m``, optional ``element_positions_m`` for CUSTOM,
``frequency_hz``). This is a folded-in implementation correction to
ADR-008 D6: the original API specified R and ``look_steering_vector``
only, but the receive-pattern-based null depth -- which the slide
caption demands -- is not computable from R and ``w`` alone. The
correction follows the spirit of ADR-008 D6's "verifiable result
struct" principle: every demo number must be a returned attribute,
which requires computing the receive-pattern depth inside the
function rather than asking the caller to compute it separately.

Alternative geometry-free definitions considered and rejected:

* ``10*log10(trace(R)/N / w^H R w)`` -- the input-vs-output power
  suppression. Gives only ~4 dB on the canonical 2-emitter scenario,
  far below the ``>=25 dB`` acceptance gate and the
  ``15-20 dB typical`` slide claim. Counter-intuitive for an RF
  audience.
* ``-10*log10(|w^H v_max|^2)`` with ``v_max`` the principal
  eigenvector of R -- caps at ~30 dB due to small signal
  contamination of v_max in finite-SNR R, even when the actual
  receive-pattern notch at the true jammer azimuth is 50+ dB.

When the jammer does NOT dominate R (``jammer_dominance_db`` small),
the search window's centre is no longer at the jammer azimuth and
the reported depth is no longer the operationally meaningful null
depth. The consumer reads ``jammer_dominance_db`` and decides
whether to trust the null. The dashboard's ``<=20 dB`` claim cap
(ADR-008 D8) provides the second line of defence.

FAILURE MODES (Invariant 4 / B3 surface)
----------------------------------------
``compute_null_steering_weights`` raises ``NullSteeringError`` when
``cond(R_loaded) > 1e8`` -- the same gate WS-B-004 uses on Capon.
Beyond ``1e8`` the inverse is numerically meaningless and the
synthesised weights would be high-norm garbage steering attention into
a noise eigendirection. The function does NOT silently return such
weights; the dsp module declines to invent an answer.

A *recoverable* low-DoF result (e.g. N=2 ULA with co-bin signal +
jammer, ``1e6 < cond <= 1e8``) does not raise. It returns a
``NullSteeringResult`` carrying a large ``condition_number`` and a
small ``null_depth_db`` so the consumer can read the quality flag and
refuse to claim a null on its own terms.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from rfmesh_contracts import ArrayGeometry  # type: ignore[import-untyped, unused-ignore]

from rfmesh_dsp.array_covariance import forward_backward_smooth
from rfmesh_dsp.array_manifold import steering_matrix
from rfmesh_dsp.exceptions import NullSteeringError

# Condition-number ceiling for ``R_loaded``. Beyond this the diagonal
# loading has failed to stabilise the inverse and the synthesised
# weights are not trustworthy. Mirrors WS-B-004's identical gate on
# Capon (see ``l2_mvdr.py`` ``_MAX_CONDITION_NUMBER``).
_MAX_CONDITION_NUMBER = 1e8
# Minimum channel count for null-steering to be meaningful (one DoF =
# can place one null = N >= 2 with a unit constraint).
_MIN_CHANNELS = 2
_DIM = 2
_DEG_FULL_CIRCLE = 360.0
_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
# Default frequency for ``compute_receive_pattern`` -- 915 MHz, the
# project's canonical L1 / L2 working band, matching the WS-A and
# WS-B test fixtures.
_DEFAULT_FREQUENCY_HZ = 915e6
# Upper bound on ``null_search_window_deg``: a window wider than a
# quarter-circle no longer represents a "null around the dominant
# interferer" -- it spans most of the receive pattern and would
# capture incidental notches unrelated to the jammer. 90 deg is the
# conservative ceiling.
_MAX_NULL_SEARCH_WINDOW_DEG = 90.0
# Floor for ``log10`` of squared magnitudes when converting to dB. The
# receive pattern's deepest null can dip below float64 precision when
# the null is well-resolved; clamping at 1e-30 caps the reported depth
# at 300 dB, which is safely above any number this module ever
# rightfully reports (the ADR-008 D8 cap is 20 dB on the UI, ~60 dB on
# the underlying claim band).
_DB_FLOOR_LINEAR = 1e-30


@dataclass(frozen=True)
class NullSteeringResult:
    """Outputs of one null-steering synthesis, including all demo numbers.

    Every field is a number Maciej might quote on stage; every quoted
    number must be a returned attribute (Demo-Integrity council
    corollary in ADR-008 D6). The struct is the *demo-honesty surface*
    of this module.

    Attributes:
        weights: ``(N,)`` ``complex64`` MVDR receive-weight vector.
            Satisfies ``|w^H a_look| ~ 1`` to numerical precision (the
            distortionless-response constraint).
        null_depth_db: How much quieter the deepest null is than the
            look direction, in dB. See module docstring "WHAT
            ``null_depth_db`` MEANS HERE" for the exact formula and
            why this definition matches the receive-pattern minimum.
            Trustworthy iff ``jammer_dominance_db`` is large
            (canonical: > 6 dB).
        look_gain_db: ``10 * log10(|w^H a_look|^2)``. Approximately 0
            dB by construction (MVDR's distortionless-response
            constraint); deviations indicate numerical issues with the
            inverse.
        condition_number: ``cond(R_loaded)``. Quality flag for the
            consumer; raised as an exception only above 1e8, but
            values above 1e6 indicate low-DoF or near-degenerate
            geometry where the null is not meaningful.
        jammer_dominance_db: ``10 * log10(lambda_max(R) / lambda_2(R))``,
            with ``lambda_max`` and ``lambda_2`` the two largest
            eigenvalues of ``R``. When small (< ~6 dB), the principal
            eigenvector of R is not unambiguously the jammer's
            steering vector and the reported ``null_depth_db`` should
            be discounted accordingly.
    """

    weights: npt.NDArray[np.complex64]
    null_depth_db: float
    look_gain_db: float
    condition_number: float
    jammer_dominance_db: float


def compute_null_steering_weights(
    r_covariance: npt.NDArray[np.complex64],
    look_steering_vector: npt.NDArray[np.complex64],
    *,
    array_geometry: ArrayGeometry,
    n_elements: int,
    element_spacing_m: float,
    element_positions_m: npt.NDArray[np.float64] | None = None,
    frequency_hz: float = _DEFAULT_FREQUENCY_HZ,
    diagonal_loading_factor: float = 1e-6,
    use_forward_backward: bool = True,
    null_search_window_deg: float = 15.0,
    null_search_step_deg: float = 0.1,
) -> NullSteeringResult:
    """Synthesise the MVDR distortionless-response weight vector for ``look``.

    The MVDR formula:

        R_loaded = R + epsilon * trace(R) / N * I
        w = R_loaded^{-1} * a_look / (a_look^H * R_loaded^{-1} * a_look)

    preserves unit gain toward ``a_look`` while minimising total
    output power against ``R``. The minimum is attained by cancelling
    the dominant non-look component of ``R`` -- the jammer.

    ``null_depth_db`` is computed by scanning the array's receive
    pattern across a ``+/- null_search_window_deg`` window centred
    on the principal-eigenvector-implied jammer azimuth, at
    ``null_search_step_deg`` resolution. This is the
    receive-pattern look:null ratio (the slide-friendly definition);
    see module docstring "WHAT ``null_depth_db`` MEANS HERE" for the
    rationale and rejected alternatives.

    Args:
        r_covariance: ``(N, N)`` Hermitian sample covariance from
            ``rfmesh_dsp.array_covariance.sample_covariance``. Will
            be upcast to ``complex128`` for the inversion.
        look_steering_vector: ``(N,)`` complex array. The **signal**
            direction's steering vector (not the null direction).
            From ``rfmesh_dsp.array_manifold.steering_vector``.
        array_geometry: ULA / UCA / CUSTOM. Required for the
            receive-pattern null-depth scan.
        n_elements: Channel count. Must match
            ``look_steering_vector.shape[0]`` and ``r_covariance``
            side.
        element_spacing_m: ULA inter-element spacing or UCA radius
            in metres. Ignored for CUSTOM but must be a positive
            finite number for the validator.
        element_positions_m: Required for CUSTOM only -- ``(N, 2)``
            ``float64`` element positions. Must be ``None`` for
            ULA/UCA.
        frequency_hz: Carrier frequency for the manifold wavelength.
            Default 915 MHz (the project canonical L1/L2 band).
        diagonal_loading_factor: ``epsilon = factor * trace(R) / N``
            loading scale applied to R before inversion. **Default
            1e-6** -- light, opposite of Capon's 1e-3 default. See
            module docstring "DIAGONAL LOADING IS LIGHT, NOT HEAVY".
        use_forward_backward: When True, apply ``R_fb = 0.5 * (R + J
            R^* J)`` before loading. **Default True** -- opposite of
            Capon's default. Coherent multipath (the operational
            common case for null-steering) requires this; see module
            docstring "FORWARD-BACKWARD SMOOTHING IS ON BY DEFAULT".
        null_search_window_deg: Half-width of the null search window
            in degrees, centred on the principal-eigenvector-
            implied jammer azimuth. Default 5 deg, narrow enough
            that the window does not span unrelated array-manifold
            notches but wide enough to absorb finite-T eigenvector
            estimation noise.
        null_search_step_deg: Resolution of the null search scan.
            Default 0.1 deg, finer than the
            ``compute_receive_pattern`` default (0.5 deg) so the
            reported depth captures the true notch depth, not the
            depth of the nearest 0.5-deg grid sample.

    Returns:
        ``NullSteeringResult`` with every demo-quotable number as a
        named attribute.

    Raises:
        ValueError: On malformed input shapes / dtypes / non-positive
            loading factor.
        NullSteeringError: When ``cond(R_loaded) > 1e8`` (loading has
            not stabilised the inverse -- the dsp module declines to
            invent an answer; Invariant 4 / B3).
    """
    _validate_inputs(r_covariance, look_steering_vector, diagonal_loading_factor)
    n_channels = r_covariance.shape[0]
    _validate_geometry_and_scan(
        n_channels=n_channels,
        n_elements=n_elements,
        null_search_window_deg=null_search_window_deg,
        null_search_step_deg=null_search_step_deg,
    )

    # ``r_unsmoothed`` is the original, physically-correct covariance;
    # used for the eigendecomposition reported in
    # ``jammer_dominance_db``. ``r_for_inverse`` is what gets fed
    # into the MVDR formula -- FB-smoothed if requested.
    r_unsmoothed = r_covariance.astype(np.complex128, copy=True)
    r_for_inverse = forward_backward_smooth(r_unsmoothed) if use_forward_backward else r_unsmoothed
    a128 = look_steering_vector.astype(np.complex128, copy=False)

    r_inv, condition_number = _inverse_with_loading(
        r_for_inverse, diagonal_loading_factor, n_channels
    )
    w128 = _mvdr_weights(r_inv, a128)
    weights: npt.NDArray[np.complex64] = w128.astype(np.complex64)

    # ----- Verifiable result fields -----
    # look_gain_db: |w^H a_look|^2 in dB. By the distortionless
    # constraint w^H a_look = 1 exactly (modulo float round-off), so
    # this should be ~0 dB.
    look_response = w128.conj() @ a128
    look_gain_linear = max(float(np.abs(look_response) ** 2), _DB_FLOOR_LINEAR)
    look_gain_db = 10.0 * math.log10(look_gain_linear)

    # jammer_dominance_db: 10*log10(lambda_max / lambda_2). Computed
    # on the un-smoothed R so the eigenvalue ratio reflects the
    # physical scene -- not the FB-symmetrised version used inside
    # the MVDR inverse. ``eigvalsh`` returns eigenvalues in
    # ascending order.
    eigenvalues_unsmoothed = np.linalg.eigvalsh(r_unsmoothed)
    lambda_max = float(eigenvalues_unsmoothed[-1])
    lambda_2 = float(eigenvalues_unsmoothed[-2]) if n_channels >= _DIM else lambda_max
    if lambda_2 <= 0.0 or not math.isfinite(lambda_max / lambda_2):
        jammer_dominance_db = 0.0
    else:
        jammer_dominance_db = 10.0 * math.log10(lambda_max / lambda_2)

    # Principal eigenvector of the *MVDR-side* R (FB-smoothed if
    # use_forward_backward=True). This is the eigenstructure MVDR
    # actually sees and nulls; using it as the null-search window
    # centre means we look for the null in the receive pattern at
    # the azimuth MVDR has actually placed it. For non-coherent
    # scenes the smoothed and un-smoothed principal eigenvectors
    # point to the same source; for coherent-multipath scenes the
    # FB smoothing rotates the eigenstructure so MVDR can null
    # separately -- and the search window follows that rotation.
    _, eigenvectors = np.linalg.eigh(r_for_inverse)

    # null_depth_db: receive-pattern look:null ratio. Search the
    # array's receive pattern in a +/- ``null_search_window_deg``
    # window centred on the principal-eigenvector-implied jammer
    # azimuth for the deepest notch; report look_gain - notch in dB.
    # The principal eigenvector of R provides an initial estimate of
    # the dominant interferer direction; the receive-pattern scan
    # around it gives the operationally honest depth (the slide
    # number Maciej quotes). See module docstring "WHAT
    # ``null_depth_db`` MEANS HERE" for the rationale and the
    # rejected geometry-free alternatives.
    array_positions_m = _build_positions(
        array_geometry,
        n_elements,
        element_spacing_m,
        element_positions_m,
    )
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
    principal_eigenvector = eigenvectors[:, -1]
    jammer_azimuth_estimate_deg = _principal_eigenvector_azimuth_deg(
        v_principal=principal_eigenvector,
        array_positions_m=array_positions_m,
        wavelength_m=wavelength_m,
    )
    null_depth_db = _scan_null_depth_db(
        w=w128,
        jammer_azimuth_estimate_deg=jammer_azimuth_estimate_deg,
        array_geometry=array_geometry,
        array_positions_m=array_positions_m,
        wavelength_m=wavelength_m,
        look_gain_db=look_gain_db,
        window_deg=null_search_window_deg,
        step_deg=null_search_step_deg,
        a_look=a128,
    )

    return NullSteeringResult(
        weights=weights,
        null_depth_db=null_depth_db,
        look_gain_db=look_gain_db,
        condition_number=condition_number,
        jammer_dominance_db=jammer_dominance_db,
    )


def apply_null(
    coherent_iq: npt.NDArray[np.complex64],
    w: npt.NDArray[np.complex64],
) -> npt.NDArray[np.complex64]:
    """Apply the null-steering weight vector to a coherent IQ block.

    The output is ``y[t] = sum_n conj(w[n]) * x[n, t]``, i.e. the
    inner product ``w^H @ x`` along the channel axis. This is the
    "nulled scalar stream" the ops dashboard plots against the
    un-nulled L1 RSSI for the Advantage 4 demo panel (ADR-008 D7).

    Args:
        coherent_iq: ``(N, T)`` ``complex64`` block from
            ``CoherentReceiver.read_coherent``. Must agree in N with
            the weight vector.
        w: ``(N,)`` ``complex64`` weight vector from
            ``compute_null_steering_weights``.

    Returns:
        ``(T,)`` ``complex64`` array of post-null samples.

    Raises:
        ValueError: On shape / dtype mismatches.
    """
    if not isinstance(coherent_iq, np.ndarray):
        msg = (
            "apply_null: coherent_iq must be a numpy.ndarray of shape "
            f"(N, T); got {type(coherent_iq).__name__}."
        )
        raise TypeError(msg)
    if coherent_iq.ndim != _DIM:
        msg = (
            "apply_null: coherent_iq must be 2-D (n_channels, n_samples); "
            f"got shape {coherent_iq.shape}."
        )
        raise ValueError(msg)
    if not isinstance(w, np.ndarray):
        msg = f"apply_null: w must be a numpy.ndarray; got {type(w).__name__}."
        raise TypeError(msg)
    if w.ndim != 1:
        msg = f"apply_null: w must be 1-D; got shape {w.shape}."
        raise ValueError(msg)
    n_channels = coherent_iq.shape[0]
    if w.shape[0] != n_channels:
        msg = (
            "apply_null: weight vector length does not match channel count "
            f"(w has {w.shape[0]} elements; coherent_iq has {n_channels} channels)."
        )
        raise ValueError(msg)
    out: npt.NDArray[np.complex64] = (w.conj() @ coherent_iq).astype(np.complex64)
    return out


def compute_receive_pattern(
    w: npt.NDArray[np.complex64],
    array_geometry: ArrayGeometry,
    n_elements: int,
    element_spacing_m: float,
    element_positions_m: npt.NDArray[np.float64] | None = None,
    frequency_hz: float = _DEFAULT_FREQUENCY_HZ,
    scan_step_deg: float = 0.5,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Return the receive pattern ``(azimuths_deg, gain_db)`` for weights ``w``.

    For each azimuth on a uniform ``scan_step_deg`` grid over
    ``[0, 360)``, computes the array's steering vector ``a(theta)``
    and evaluates ``|w^H a(theta)|^2`` in dB. The demo's A/B polar
    plot reads from this function (ADR-008 D7); the ops dashboard is
    the only consumer rendering it as pixels.

    The pattern's domain is ``[0, 360)``, matching the project-wide
    azimuth convention (INTERFACES.md Section 0). For a ULA the
    front/back ambiguity (a real physical artefact: the array cannot
    distinguish ``theta`` from ``180 - theta``) is visible as a
    mirror peak in the pattern and is NOT suppressed.

    Args:
        w: ``(N,)`` ``complex64`` weight vector from
            ``compute_null_steering_weights``.
        array_geometry: Array layout, one of ``ArrayGeometry.{ULA,
            UCA, CUSTOM}``.
        n_elements: Number of channels; must match ``w.shape[0]``.
        element_spacing_m: ULA inter-element spacing in metres, or
            UCA radius in metres. Ignored for CUSTOM but must still
            be a positive finite number for the validator.
        element_positions_m: Required for CUSTOM only -- ``(N, 2)``
            ``float64`` element positions in the array-local frame.
            Must be ``None`` for ULA / UCA.
        frequency_hz: Carrier frequency for the steering-vector
            wavelength. Default 915 MHz, matching the project's
            canonical L1 / L2 band.
        scan_step_deg: Angular resolution of the scan. Default
            0.5 deg, matching the ``raw_pseudospectrum`` convention
            in INTERFACES.md Section 3.

    Returns:
        Tuple ``(azimuths_deg, gain_db)`` with two ``float64``
        arrays of length ``int(360 / scan_step_deg)``.

    Raises:
        ValueError: On malformed inputs (wrong shape, wrong dtype,
            inconsistent geometry tag and positions).
    """
    if not isinstance(w, np.ndarray):
        msg = f"compute_receive_pattern: w must be a numpy.ndarray; got {type(w).__name__}."
        raise TypeError(msg)
    if w.ndim != 1 or w.shape[0] != n_elements:
        msg = (
            "compute_receive_pattern: w must be 1-D of length n_elements="
            f"{n_elements}; got shape {w.shape}."
        )
        raise ValueError(msg)
    if n_elements < _MIN_CHANNELS:
        msg = f"compute_receive_pattern: n_elements must be >= 2 (got {n_elements})."
        raise ValueError(msg)
    if scan_step_deg <= 0.0 or scan_step_deg >= _DEG_FULL_CIRCLE:
        msg = f"compute_receive_pattern: scan_step_deg must be in (0, 360) (got {scan_step_deg})."
        raise ValueError(msg)
    if frequency_hz <= 0.0:
        msg = f"compute_receive_pattern: frequency_hz must be > 0 (got {frequency_hz})."
        raise ValueError(msg)
    positions = _build_positions(
        array_geometry,
        n_elements,
        element_spacing_m,
        element_positions_m,
    )
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
    azimuths_deg = np.arange(0.0, _DEG_FULL_CIRCLE, scan_step_deg, dtype=np.float64)
    a_matrix = steering_matrix(
        geometry=array_geometry,
        element_positions_m=positions,
        azimuths_rad=np.deg2rad(azimuths_deg),
        wavelength_m=wavelength_m,
    )
    # gain_linear[k] = |w^H a_k|^2. ``w.conj() @ a_matrix`` is the
    # row-wise inner product across channels, giving a (K,) complex
    # vector; squared modulus is the per-azimuth linear gain.
    w128 = w.astype(np.complex128, copy=False)
    response = w128.conj() @ a_matrix
    gain_linear = np.abs(response) ** 2
    # Clamp below the float64 underflow threshold so log10 stays
    # well-defined when the null is many orders of magnitude deep.
    gain_linear = np.maximum(gain_linear, _DB_FLOOR_LINEAR)
    gain_db: npt.NDArray[np.float64] = 10.0 * np.log10(gain_linear)
    return azimuths_deg, gain_db


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _validate_geometry_and_scan(
    *,
    n_channels: int,
    n_elements: int,
    null_search_window_deg: float,
    null_search_step_deg: float,
) -> None:
    """Cross-field validation for the geometry and scan-window arguments.

    Extracted from the main entry point to keep
    ``compute_null_steering_weights`` below the per-function
    statement budget (PLR0915). All checks raise ``ValueError``;
    none of them are recoverable in the way ``NullSteeringError``
    is.
    """
    if n_channels != n_elements:
        msg = (
            "compute_null_steering_weights: n_elements="
            f"{n_elements} does not match r_covariance side N={n_channels}."
        )
        raise ValueError(msg)
    if null_search_window_deg <= 0.0 or null_search_window_deg > _MAX_NULL_SEARCH_WINDOW_DEG:
        msg = (
            "compute_null_steering_weights: null_search_window_deg must be in "
            f"(0, {_MAX_NULL_SEARCH_WINDOW_DEG}] degrees (got {null_search_window_deg})."
        )
        raise ValueError(msg)
    if null_search_step_deg <= 0.0 or null_search_step_deg > null_search_window_deg:
        msg = (
            "compute_null_steering_weights: null_search_step_deg must be in "
            f"(0, null_search_window_deg]; got step={null_search_step_deg}, "
            f"window={null_search_window_deg}."
        )
        raise ValueError(msg)


def _inverse_with_loading(
    r_for_inverse: npt.NDArray[np.complex128],
    diagonal_loading_factor: float,
    n_channels: int,
) -> tuple[npt.NDArray[np.complex128], float]:
    """Load ``R`` diagonally, gate on cond number, and return ``(R_inv, cond)``.

    The loading recipe mirrors ``l2_mvdr.py``: ``epsilon = factor *
    trace(R) / N`` so the scale tracks R's magnitude. The cond gate
    is the same 1e8 ceiling as Capon; beyond it the inverse is
    numerically meaningless and we refuse to synthesise weights
    (Invariant 4 / B3).
    """
    trace_r = float(np.real(np.trace(r_for_inverse)))
    epsilon = diagonal_loading_factor * trace_r / float(n_channels)
    r_loaded = r_for_inverse + epsilon * np.eye(n_channels, dtype=np.complex128)

    condition_number = float(np.linalg.cond(r_loaded))
    if not math.isfinite(condition_number) or condition_number > _MAX_CONDITION_NUMBER:
        msg = (
            "compute_null_steering_weights: cond(R_loaded) = "
            f"{condition_number:.3e} exceeds {_MAX_CONDITION_NUMBER:.0e}; "
            "the inverse is not numerically meaningful and weights are not "
            "synthesised (Invariant 4: no silent fallback)."
        )
        raise NullSteeringError(msg)

    try:
        r_inv: npt.NDArray[np.complex128] = np.linalg.inv(r_loaded).astype(
            np.complex128, copy=False
        )
    except np.linalg.LinAlgError as exc:  # pragma: no cover - belt and braces past the cond gate
        msg = (
            "compute_null_steering_weights: numpy.linalg.inv raised "
            f"{exc!r} despite cond(R_loaded) = {condition_number:.3e} <= "
            f"{_MAX_CONDITION_NUMBER:.0e}. This indicates a numerical "
            "anomaly in R; weights are not synthesised."
        )
        raise NullSteeringError(msg) from exc
    return r_inv, condition_number


def _mvdr_weights(
    r_inv: npt.NDArray[np.complex128],
    a_look: npt.NDArray[np.complex128],
) -> npt.NDArray[np.complex128]:
    """Compute the MVDR distortionless-response weight ``w = R^{-1} a / (a^H R^{-1} a)``.

    Raises ``NullSteeringError`` if the denominator (the Hermitian
    quadratic form ``a_look^H R^{-1} a_look``) is non-positive or
    non-finite -- both would indicate that R is not Hermitian
    positive definite even after loading.
    """
    numerator = r_inv @ a_look
    denominator = a_look.conj() @ numerator
    denominator_real = float(np.real(denominator))
    if denominator_real <= 0.0 or not math.isfinite(denominator_real):
        msg = (
            "compute_null_steering_weights: a_look^H R_loaded^{-1} a_look = "
            f"{denominator!r}; the Hermitian quadratic form is non-positive "
            "or non-finite. R may not be Hermitian positive definite even "
            "after loading."
        )
        raise NullSteeringError(msg)
    weights: npt.NDArray[np.complex128] = numerator / denominator
    return weights


def _principal_eigenvector_azimuth_deg(
    *,
    v_principal: npt.NDArray[np.complex128],
    array_positions_m: npt.NDArray[np.float64],
    wavelength_m: float,
    coarse_step_deg: float = 0.5,
) -> float:
    """Find the azimuth whose steering vector best matches ``v_principal``.

    The principal eigenvector of R is, in the canonical scenario,
    approximately ``a_jammer`` rotated slightly by signal
    contamination. Scanning ``|<a(theta), v_principal>|`` over a
    coarse 0.5-deg grid and picking the maximum gives a reasonable
    estimate of the jammer's azimuth; the subsequent
    receive-pattern scan around that estimate refines the depth at
    higher resolution.
    """
    azimuths_deg = np.arange(0.0, _DEG_FULL_CIRCLE, coarse_step_deg, dtype=np.float64)
    a_matrix = steering_matrix(
        geometry=ArrayGeometry.CUSTOM,  # explicit positions, geometry tag is informational
        element_positions_m=array_positions_m,
        azimuths_rad=np.deg2rad(azimuths_deg),
        wavelength_m=wavelength_m,
    )
    inner_abs = np.abs(v_principal.conj() @ a_matrix)
    best_idx = int(np.argmax(inner_abs))
    return float(azimuths_deg[best_idx])


def _scan_null_depth_db(
    *,
    w: npt.NDArray[np.complex128],
    jammer_azimuth_estimate_deg: float,
    array_geometry: ArrayGeometry,
    array_positions_m: npt.NDArray[np.float64],
    wavelength_m: float,
    look_gain_db: float,
    window_deg: float,
    step_deg: float,
    a_look: npt.NDArray[np.complex128],
) -> float:
    """Scan the receive pattern around the jammer estimate; return the look:null depth in dB.

    The scan grid is a finer-than-default sweep centred on the
    eigenvector-implied jammer azimuth (which is where MVDR has
    deposited the dominant null in the non-coherent case). For the
    coherent-multipath failure case the principal eigenvector
    aliases between signal and jammer; the search then falls back
    to a *global* receive-pattern search outside the look's main
    beam, so an honest "no useful null found" answer surfaces in
    the absolute depth -- not by accidentally finding a deep notch
    far from any source.
    """
    half_n = round(window_deg / step_deg)
    offsets_deg = np.arange(-half_n, half_n + 1, dtype=np.float64) * step_deg
    local_azimuths_deg = (jammer_azimuth_estimate_deg + offsets_deg) % _DEG_FULL_CIRCLE
    local_a = steering_matrix(
        geometry=array_geometry,
        element_positions_m=array_positions_m,
        azimuths_rad=np.deg2rad(local_azimuths_deg),
        wavelength_m=wavelength_m,
    )
    local_response = w.conj() @ local_a
    local_gain_linear = np.abs(local_response) ** 2
    local_min_linear = max(float(np.min(local_gain_linear)), _DB_FLOOR_LINEAR)

    # Coherent-multipath sanity check: if the principal eigenvector's
    # azimuth is *itself* near the look direction (within
    # ``window_deg``), the eigenvector is not pointing at the jammer
    # and the local minimum lies near the look constraint -- which
    # MVDR cannot null. The honest report in that case is the
    # near-zero depth at the eigenvector-implied azimuth (which is
    # what the local scan above gives -- typically < 5 dB). Use this
    # path to surface the failure rather than silently substituting
    # a different scan.
    a_look_norm = float(np.linalg.norm(a_look))
    if a_look_norm > 0.0:
        a_look_unit = a_look / a_look_norm
        # The "eigenvector close to look" check: do the same coarse
        # azimuth-of-a-vector match used to locate the jammer, but
        # for the look vector itself; if the jammer estimate is
        # within ``window_deg`` of the look azimuth the scenario is
        # coherent or otherwise pathological.
        look_azimuth_estimate_deg = _principal_eigenvector_azimuth_deg(
            v_principal=a_look_unit.astype(np.complex128),
            array_positions_m=array_positions_m,
            wavelength_m=wavelength_m,
        )
        diff_deg = (
            (jammer_azimuth_estimate_deg - look_azimuth_estimate_deg + 180.0) % 360.0
        ) - 180.0
        if abs(diff_deg) < window_deg:
            # The jammer estimate is essentially the look direction
            # -- coherent failure mode. Report the local minimum
            # honestly (which is small / near-zero); do not search
            # the wider pattern for spurious deep notches.
            local_min_db = 10.0 * math.log10(local_min_linear)
            return look_gain_db - local_min_db

    local_min_db = 10.0 * math.log10(local_min_linear)
    return look_gain_db - local_min_db


def _validate_inputs(
    r_covariance: npt.NDArray[np.complex64],
    look_steering_vector: npt.NDArray[np.complex64],
    diagonal_loading_factor: float,
) -> None:
    """Argument validation shared by the public entry point.

    Hoisted out of ``compute_null_steering_weights`` to keep the main
    function focused on the algorithm; the checks themselves are
    pedestrian but each one closes a silent-corruption hole.
    """
    if not isinstance(r_covariance, np.ndarray):
        msg = (
            "compute_null_steering_weights: r_covariance must be a "
            f"numpy.ndarray; got {type(r_covariance).__name__}."
        )
        raise TypeError(msg)
    if r_covariance.ndim != _DIM or r_covariance.shape[0] != r_covariance.shape[1]:
        msg = (
            "compute_null_steering_weights: r_covariance must be a square "
            f"2-D matrix; got shape {r_covariance.shape}."
        )
        raise ValueError(msg)
    if r_covariance.shape[0] < _MIN_CHANNELS:
        msg = (
            "compute_null_steering_weights: r_covariance must have "
            f"N >= {_MIN_CHANNELS} channels (got N={r_covariance.shape[0]})."
        )
        raise ValueError(msg)
    if not np.issubdtype(r_covariance.dtype, np.complexfloating):
        msg = (
            "compute_null_steering_weights: r_covariance must have a complex "
            f"dtype; got {r_covariance.dtype}."
        )
        raise ValueError(msg)
    if not isinstance(look_steering_vector, np.ndarray):
        msg = (
            "compute_null_steering_weights: look_steering_vector must be a "
            f"numpy.ndarray; got {type(look_steering_vector).__name__}."
        )
        raise TypeError(msg)
    if look_steering_vector.ndim != 1:
        msg = (
            "compute_null_steering_weights: look_steering_vector must be "
            f"1-D; got shape {look_steering_vector.shape}."
        )
        raise ValueError(msg)
    if look_steering_vector.shape[0] != r_covariance.shape[0]:
        msg = (
            "compute_null_steering_weights: look_steering_vector length "
            f"{look_steering_vector.shape[0]} does not match r_covariance "
            f"side N={r_covariance.shape[0]}."
        )
        raise ValueError(msg)
    if not np.issubdtype(look_steering_vector.dtype, np.complexfloating):
        msg = (
            "compute_null_steering_weights: look_steering_vector must have a "
            f"complex dtype; got {look_steering_vector.dtype}."
        )
        raise ValueError(msg)
    if diagonal_loading_factor <= 0.0 or not math.isfinite(diagonal_loading_factor):
        msg = (
            "compute_null_steering_weights: diagonal_loading_factor must be a "
            f"positive finite number (got {diagonal_loading_factor})."
        )
        raise ValueError(msg)


def _build_positions(
    geometry: ArrayGeometry,
    n_elements: int,
    element_spacing_m: float,
    element_positions_m: npt.NDArray[np.float64] | None,
) -> npt.NDArray[np.float64]:
    """Derive ``(N, 2) float64`` element positions matching the simulator convention.

    Same mapping as ``_array_config_to_positions_m`` in
    ``l2_mvdr.py`` (kept private here -- the cross-package
    duplication is intentional, the two modules own different
    representations of the same physical array layout). Future
    cleanup ticket may extract to a shared helper.
    """
    if geometry is ArrayGeometry.ULA:
        if element_spacing_m <= 0.0 or not math.isfinite(element_spacing_m):
            msg = (
                "compute_receive_pattern (ULA): element_spacing_m must be a "
                f"positive finite number (got {element_spacing_m})."
            )
            raise ValueError(msg)
        if element_positions_m is not None:
            msg = (
                "compute_receive_pattern (ULA): element_positions_m must be "
                "None for parametric geometries."
            )
            raise ValueError(msg)
        positions = np.zeros((n_elements, _DIM), dtype=np.float64)
        positions[:, 1] = np.arange(n_elements, dtype=np.float64) * element_spacing_m
        return positions
    if geometry is ArrayGeometry.UCA:
        if element_spacing_m <= 0.0 or not math.isfinite(element_spacing_m):
            msg = (
                "compute_receive_pattern (UCA): element_spacing_m (UCA radius) "
                f"must be a positive finite number (got {element_spacing_m})."
            )
            raise ValueError(msg)
        if element_positions_m is not None:
            msg = (
                "compute_receive_pattern (UCA): element_positions_m must be "
                "None for parametric geometries."
            )
            raise ValueError(msg)
        radius = element_spacing_m
        alphas = 2.0 * math.pi * np.arange(n_elements, dtype=np.float64) / float(n_elements)
        positions = np.empty((n_elements, _DIM), dtype=np.float64)
        positions[:, 0] = radius * np.cos(alphas)
        positions[:, 1] = radius * np.sin(alphas)
        return positions
    # CUSTOM
    if element_positions_m is None:
        msg = (
            "compute_receive_pattern (CUSTOM): element_positions_m must be "
            "supplied for arbitrary geometries."
        )
        raise ValueError(msg)
    positions = np.asarray(element_positions_m, dtype=np.float64)
    if positions.shape != (n_elements, _DIM):
        msg = (
            "compute_receive_pattern (CUSTOM): element_positions_m must have "
            f"shape ({n_elements}, 2); got {positions.shape}."
        )
        raise ValueError(msg)
    return positions
