"""Tests for the position covariance + 95% confidence ellipse (WS-CD-004).

Structured to make a CI failure point at exactly one property:

* chi-square constant honesty (the load-bearing 5.99146 number that
  ADR-009 corrects ADR-005 on)
* end-to-end CRLB agreement with ``docs/demo/crlb_analysis.py`` on the
  trench-demo geometry (Beat C + Beat D) -- the gate that catches
  azimuth-convention sign errors
* eigendecomposition + orientation convention (East-toward-North)
* singular / non-PSD inputs raise loudly (Invariant B3)
* ``EllipseENU`` contract compliance (semi-major >= semi-minor always)
* the inclusion-rate honesty test: 10000 Gaussian samples, 95 +/- 2 %
  fall inside the ellipse derived from their own covariance.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from _helpers import (  # type: ignore[import-not-found, unused-ignore]
    MakeBearing,
    azimuth_node_to_emitter_deg,
)
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    EllipseENU,
)
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
)
from rfmesh_fusion.covariance import (
    CHI2_95_DF2,
    compute_covariance,
    covariance_to_ellipse,
)
from rfmesh_fusion.exceptions import (
    DegenerateGeometryError,
    FusionError,
    MLEConvergenceError,
    SingularFisherInformationError,
)

# Honesty / tolerance bands used across tests. Hoisted to module
# constants both because ruff PLR2004 flags them as magic in
# comparisons and because naming them documents *what* each number
# means at the call site.

# Beat-C / Beat-D semi-axis tolerance band: ten percent of the
# reference CRLB number from docs/demo/trench-demo-geometry.md §2.2.
# Wider than the ~1 m floor seen in practice (the test passes at
# better than 1% on the current implementation) so the test stays
# robust to minor refactors that round somewhere differently.
_CRLB_REFERENCE_TOLERANCE_FRAC: float = 0.10

# Inclusion-rate honesty band on the 95% ellipse: 10000 samples,
# theoretical inclusion = 0.95, +/- 2% band. The +/-2% is ~10 sigma
# at this sample count (sqrt(p(1-p)/n) = 0.0022), so only a real bug
# fails this, not statistical noise. ADR-009 / INTERFACES.md §2.
_INCLUSION_RATE_LOWER: float = 0.93
_INCLUSION_RATE_UPPER: float = 0.97

# Aspect-ratio threshold for the "highly anisotropic geometry"
# elongation test. The close-spaced south-baseline geometry produces
# a measured aspect ratio ~25; 3.0 is a deliberately loose floor
# that future minor refactors will not trip on.
_ASPECT_RATIO_ELONGATED_FLOOR: float = 3.0

# Near-circular threshold for the isotropic-geometry test. The
# symmetric 3-node arc produces semi_minor / semi_major within
# 1e-12; 0.999 is again a loose floor.
_CIRCULAR_RATIO_FLOOR: float = 0.999

# Off-axis cross-term tolerance for the isotropic-geometry test:
# |sigma_xy| / sqrt(sigma_xx * sigma_yy) < this. Catches a coupling
# bug that introduces a spurious cross-term.
_ISOTROPIC_OFFAXIS_TOLERANCE: float = 1e-6


# ---------------------------------------------------------------------------
# §1 Chi-square constant honesty
# ---------------------------------------------------------------------------


def test_chi_square_constant() -> None:
    """``CHI2_95_DF2`` agrees with ``scipy.stats.chi2.ppf(0.95, df=2)``.

    Oracle (scipy) is guarded by ``importorskip`` -- if scipy is not
    available the test is skipped, not failed. The constant is the
    load-bearing scale (ADR-009) that turns 1-sigma principal
    variances into the 95% ellipse semi-axes; a typo would silently
    make every published ellipse the wrong size.
    """
    scipy_stats = pytest.importorskip("scipy.stats")
    expected = float(scipy_stats.chi2.ppf(0.95, df=2))
    assert math.isclose(CHI2_95_DF2, expected, rel_tol=0.0, abs_tol=1e-12)


# ---------------------------------------------------------------------------
# §2 End-to-end CRLB reproduction (trench-demo geometry)
# ---------------------------------------------------------------------------
#
# The numbers come from ``docs/demo/crlb_analysis.py`` and are quoted
# in ``docs/demo/trench-demo-geometry.md`` §2.2. Beat C and Beat D each
# exercise the full Jacobian + Fisher inversion + chi-square scaling
# chain on a real demo scenario -- any sign or units bug surfaces
# here, not in a synthetic micro-test.

# Beat-D trench-demo geometry (ENU metres, centroid-relative origin).
_TRENCH_EMITTER_ENU: tuple[float, float] = (0.0, 3000.0)
_TRENCH_NODES_BEAT_C: tuple[tuple[float, float], ...] = (
    (-1800.0, 1800.0),  # node A: NW flank
    (1800.0, 1800.0),  # node B: NE flank
    (0.0, 900.0),  # node C: central south
)
_TRENCH_SIGMAS_BEAT_C_DEG: tuple[float, ...] = (5.0, 5.0, 5.0)

_TRENCH_NODES_BEAT_D: tuple[tuple[float, float], ...] = (
    *_TRENCH_NODES_BEAT_C,
    (2000.0, 3500.0),  # node D: bladeRF L2 overwatch
)
_TRENCH_SIGMAS_BEAT_D_DEG: tuple[float, ...] = (*_TRENCH_SIGMAS_BEAT_C_DEG, 1.5)


def _bearings_for_geometry(
    make_bearing: MakeBearing,
    nodes_enu: tuple[tuple[float, float], ...],
    sigmas_deg: tuple[float, ...],
    emitter_enu: tuple[float, float],
) -> list[BearingReport]:
    """Construct perfect ``BearingReport`` s pointing at ``emitter_enu``."""
    bearings = []
    for idx, (node_enu, sigma_deg) in enumerate(
        zip(nodes_enu, sigmas_deg, strict=True),
    ):
        az_deg = azimuth_node_to_emitter_deg(node_enu, emitter_enu)
        bearings.append(
            make_bearing(
                node_id=f"trench-{idx}",
                azimuth_deg=az_deg,
                sigma_deg=sigma_deg,
            ),
        )
    return bearings


def test_trench_demo_geometry_reproduces_crlb_beat_d(
    make_bearing: MakeBearing,
) -> None:
    """Beat D (3 L1 + 1 L2): semi-major ~= 359 m, semi-minor ~= 125 m (+/- 10%).

    Reference: ``docs/demo/trench-demo-geometry.md`` §2.2. The CRLB
    script ``docs/demo/crlb_analysis.py`` computes these numbers from
    closed form; this test runs ``compute_covariance`` +
    ``covariance_to_ellipse`` on the same geometry and asserts they
    agree to within 10% -- which is the gate that catches a Jacobian
    sign flip or a missing sigma-deg-to-rad conversion.
    """
    bearings = _bearings_for_geometry(
        make_bearing,
        _TRENCH_NODES_BEAT_D,
        _TRENCH_SIGMAS_BEAT_D_DEG,
        _TRENCH_EMITTER_ENU,
    )

    cov = compute_covariance(
        _TRENCH_EMITTER_ENU,
        bearings,
        _TRENCH_NODES_BEAT_D,
    )
    ellipse = covariance_to_ellipse(cov)

    # Reference values: docs/demo/trench-demo-geometry.md §2.2 Beat D.
    expected_semi_major_m = 359.0
    expected_semi_minor_m = 125.0
    tolerance = _CRLB_REFERENCE_TOLERANCE_FRAC

    assert abs(ellipse.semi_major_m - expected_semi_major_m) <= tolerance * expected_semi_major_m, (
        f"Beat-D semi_major_m {ellipse.semi_major_m:.1f} m disagrees "
        f"with the CRLB reference {expected_semi_major_m} m by more "
        f"than {tolerance:.0%}; check Jacobian convention against "
        "docs/demo/crlb_analysis.py:jac_row."
    )
    assert abs(ellipse.semi_minor_m - expected_semi_minor_m) <= tolerance * expected_semi_minor_m, (
        f"Beat-D semi_minor_m {ellipse.semi_minor_m:.1f} m disagrees "
        f"with the CRLB reference {expected_semi_minor_m} m by more "
        f"than {tolerance:.0%}."
    )


def test_trench_demo_geometry_reproduces_crlb_beat_c(
    make_bearing: MakeBearing,
) -> None:
    """Beat C (3 L1): semi-major ~= 393 m, semi-minor ~= 357 m (+/- 10%).

    Two beats, two independent gates on the Jacobian sign and the
    chi-square constant. A Beat-D test that passes by luck (e.g.
    sign-correct but units-wrong, with the two errors cancelling on
    one geometry) would fail Beat C as the L2 sigma drops out.
    """
    bearings = _bearings_for_geometry(
        make_bearing,
        _TRENCH_NODES_BEAT_C,
        _TRENCH_SIGMAS_BEAT_C_DEG,
        _TRENCH_EMITTER_ENU,
    )

    cov = compute_covariance(
        _TRENCH_EMITTER_ENU,
        bearings,
        _TRENCH_NODES_BEAT_C,
    )
    ellipse = covariance_to_ellipse(cov)

    expected_semi_major_m = 393.0
    expected_semi_minor_m = 357.0
    tolerance = _CRLB_REFERENCE_TOLERANCE_FRAC

    assert abs(ellipse.semi_major_m - expected_semi_major_m) <= tolerance * expected_semi_major_m, (
        f"Beat-C semi_major_m {ellipse.semi_major_m:.1f} m disagrees "
        f"with the CRLB reference {expected_semi_major_m} m by more "
        f"than {tolerance:.0%}."
    )
    assert abs(ellipse.semi_minor_m - expected_semi_minor_m) <= tolerance * expected_semi_minor_m, (
        f"Beat-C semi_minor_m {ellipse.semi_minor_m:.1f} m disagrees "
        f"with the CRLB reference {expected_semi_minor_m} m by more "
        f"than {tolerance:.0%}."
    )


# ---------------------------------------------------------------------------
# §3 Geometry shape -> ellipse shape (isotropic / asymmetric)
# ---------------------------------------------------------------------------


def test_isotropic_geometry_yields_circular_ellipse(
    make_bearing: MakeBearing,
) -> None:
    """3 nodes on a symmetric arc -> covariance near-scalar, ellipse near-circular.

    Pins that the Jacobian + Fisher inversion together preserve the
    symmetry of the geometry; an axis-coupling bug (cross-term in the
    covariance that should not be there) shows up as ``|sigma_xy|``
    not approaching zero.
    """
    emitter = (0.0, 0.0)
    radius_m = 3000.0
    # Three nodes equally spaced on a circle of radius ``radius_m``
    # around the emitter. By symmetry the Fisher information is a
    # scalar multiple of the identity, so the covariance is too, and
    # the ellipse is a circle.
    angles_rad = [math.radians(deg) for deg in (90.0, 90.0 + 120.0, 90.0 + 240.0)]
    nodes_enu: tuple[tuple[float, float], ...] = tuple(
        (radius_m * math.cos(a), radius_m * math.sin(a)) for a in angles_rad
    )
    bearings = _bearings_for_geometry(
        make_bearing,
        nodes_enu,
        (3.0, 3.0, 3.0),
        emitter,
    )

    cov = compute_covariance(emitter, bearings, nodes_enu)

    sigma_xx = float(cov[0, 0])
    sigma_yy = float(cov[1, 1])
    sigma_xy = float(cov[0, 1])

    # sigma_xx ~= sigma_yy to 1e-6 relative.
    assert math.isclose(sigma_xx, sigma_yy, rel_tol=1e-6)
    # |sigma_xy| / sqrt(sigma_xx * sigma_yy) -- normalised cross-term.
    assert (abs(sigma_xy) / math.sqrt(sigma_xx * sigma_yy)) < _ISOTROPIC_OFFAXIS_TOLERANCE, (
        f"isotropic geometry produced off-axis sigma_xy={sigma_xy:.3e}"
    )

    ellipse = covariance_to_ellipse(cov)
    # semi_minor / semi_major > circular floor -> within ~0.1% of circular.
    assert ellipse.semi_minor_m / ellipse.semi_major_m > _CIRCULAR_RATIO_FLOOR


def test_asymmetric_geometry_elongates_ellipse(
    make_bearing: MakeBearing,
) -> None:
    """Two close-spaced nodes south of the emitter -> ellipse along the LOS.

    Two nodes at (-100, -3000) and (+100, -3000), emitter at (0, 0):
    both bearings point nearly north (LOS along +N), with only ~2 deg
    angular separation between them. The cross-fix tightly constrains
    the *cross-range* (East) coordinate via the small angular
    parallax, but is much weaker along the *down-range* (North)
    coordinate -- moving the emitter +/-100 m N barely changes either
    bearing. The 95% ellipse is therefore elongated along North, with
    aspect ratio >> 1 and orientation_deg ~= +90 deg.
    """
    emitter = (0.0, 0.0)
    nodes_enu: tuple[tuple[float, float], ...] = (
        (-100.0, -3000.0),
        (100.0, -3000.0),
    )
    bearings = _bearings_for_geometry(
        make_bearing,
        nodes_enu,
        (5.0, 5.0),
        emitter,
    )

    cov = compute_covariance(emitter, bearings, nodes_enu)
    ellipse = covariance_to_ellipse(cov)

    # The covariance must have larger variance along North than East.
    assert cov[1, 1] > cov[0, 0]

    # Aspect ratio >> 1 -- the geometry is highly anisotropic
    # (close-spaced baseline at long down-range stand-off).
    assert ellipse.semi_major_m / ellipse.semi_minor_m > _ASPECT_RATIO_ELONGATED_FLOOR

    # orientation_deg ~= +90 deg (East-frame angle of the major axis,
    # which points North).
    assert math.isclose(ellipse.orientation_deg, 90.0, abs_tol=0.5)


# ---------------------------------------------------------------------------
# §4 Orientation convention: East-toward-North
# ---------------------------------------------------------------------------


def _covariance_from_axes(
    semi_major_var: float,
    semi_minor_var: float,
    orientation_deg_in: float,
) -> np.ndarray:
    """Build a 2x2 covariance with given principal variances and orientation.

    ``orientation_deg_in`` is the angle (East -> North) of the major
    eigenvector. The returned covariance has eigenvalues
    ``(semi_major_var, semi_minor_var)`` and the major eigenvector
    pointing at that angle. Used to *check* ``covariance_to_ellipse``
    recovers the orientation we put in.
    """
    theta = math.radians(orientation_deg_in)
    c, s = math.cos(theta), math.sin(theta)
    # Rotation matrix that sends East-unit to the desired major-axis
    # direction.
    rot = np.array([[c, -s], [s, c]], dtype=np.float64)
    diag = np.array(
        [[semi_major_var, 0.0], [0.0, semi_minor_var]],
        dtype=np.float64,
    )
    return rot @ diag @ rot.T


@pytest.mark.parametrize(
    "orientation_deg_in",
    [0.0, 30.0, 90.0, -45.0, 120.0, -150.0],
)
def test_ellipse_orientation_convention_east_to_north(
    orientation_deg_in: float,
) -> None:
    """Constructed covariance with known orientation -> recovered to 1e-6 deg.

    Sweeps several orientations including the cardinal directions and
    a couple of obtuse ones (covariance is sign-symmetric under v ->
    -v, so 120 deg and -60 deg describe the same axis; the function
    canonicalises by flipping the vector so east_comp >= 0).
    """
    cov = _covariance_from_axes(
        semi_major_var=400.0,  # m^2
        semi_minor_var=100.0,  # m^2
        orientation_deg_in=orientation_deg_in,
    )

    ellipse = covariance_to_ellipse(cov)

    # Canonicalise the input the same way the module does: if the
    # major-axis east-component (cos(theta)) is negative, the angle is
    # the supplement.
    theta_in = math.radians(orientation_deg_in)
    east_comp = math.cos(theta_in)
    north_comp = math.sin(theta_in)
    if east_comp < 0.0:
        east_comp = -east_comp
        north_comp = -north_comp
    expected_orientation_deg = math.degrees(math.atan2(north_comp, east_comp))

    assert math.isclose(
        ellipse.orientation_deg,
        expected_orientation_deg,
        abs_tol=1e-6,
    ), (
        f"orientation_deg {ellipse.orientation_deg} != "
        f"expected {expected_orientation_deg} for input "
        f"orientation_deg_in={orientation_deg_in}"
    )

    # The semi-axes are sqrt(chi2 * lambda) of the eigenvalues, which
    # we put in directly. Verify they round-trip.
    assert math.isclose(
        ellipse.semi_major_m,
        math.sqrt(CHI2_95_DF2 * 400.0),
        rel_tol=1e-9,
    )
    assert math.isclose(
        ellipse.semi_minor_m,
        math.sqrt(CHI2_95_DF2 * 100.0),
        rel_tol=1e-9,
    )


# ---------------------------------------------------------------------------
# §5 EllipseENU contract: semi_major_m >= semi_minor_m always
# ---------------------------------------------------------------------------


def test_semi_major_ge_semi_minor_always(
    seeded_rng: np.random.Generator,
) -> None:
    """50 random PSD covariances -> EllipseENU constructs every time.

    Generates random ``cov = A @ A.T + jitter * I`` matrices (PSD by
    construction). The Pydantic validator on ``EllipseENU`` raises if
    ``semi_major_m < semi_minor_m``, so a successful construction *is*
    the assertion -- the implementation must sort eigenvalues
    descending before naming them "major" and "minor".
    """
    rng = seeded_rng
    for _ in range(50):
        # Random 2x2 with eigenvalues in [0.1, 100] -- comfortable PSD.
        eigvals = rng.uniform(0.1, 100.0, size=2)
        theta = rng.uniform(-math.pi, math.pi)
        c, s = math.cos(theta), math.sin(theta)
        rot = np.array([[c, -s], [s, c]], dtype=np.float64)
        diag = np.diag(eigvals)
        cov = rot @ diag @ rot.T
        # Symmetrise to absorb any rounding asymmetry.
        cov = 0.5 * (cov + cov.T)

        ellipse = covariance_to_ellipse(cov)
        assert ellipse.semi_major_m >= ellipse.semi_minor_m


def test_covariance_to_ellipse_returns_ellipse_enu_instance() -> None:
    """``covariance_to_ellipse`` returns an ``EllipseENU`` (not a tuple).

    Pins the return type for downstream consumers (WS-CD-007 wires
    this into ``FixEvent.confidence_ellipse_95``, which is typed
    ``EllipseENU`` -- any tuple return would mypy-fail at that seam).
    """
    cov = np.eye(2, dtype=np.float64) * 100.0  # 100 m^2 isotropic.
    ellipse = covariance_to_ellipse(cov)
    assert isinstance(ellipse, EllipseENU)


# ---------------------------------------------------------------------------
# §6 Inclusion-rate honesty (the load-bearing test)
# ---------------------------------------------------------------------------


def test_chi_square_inclusion_rate_matches_95_percent(
    seeded_rng: np.random.Generator,
) -> None:
    """10000 Gaussian samples from known cov -> 95 +/- 2 % inside the ellipse.

    The chi-square scaling is right iff the inclusion rate matches
    0.95. This is the empirical honesty test that ties the constant
    ``CHI2_95_DF2`` to the *meaning* of the ellipse the contract
    specifies (``EllipseENU`` "95 % confidence contour",
    INTERFACES.md §2). Any sign flip, missing radians conversion, or
    eigendecomposition bug surfaces as an inclusion rate that drifts
    away from 95 %.

    The tolerance band [0.93, 0.97] is +/- 2 % (sqrt(p*(1-p)/n) for
    p=0.95, n=10000 is ~0.0022, so 2-sigma is ~0.4 %; 2 % is ~10
    sigma -- a tolerance so wide that only a *real* bug fails it,
    not statistical noise).
    """
    # Asymmetric known covariance with non-trivial rotation so the
    # test exercises the eigendecomposition + orientation handling,
    # not just an axis-aligned identity.
    true_cov = _covariance_from_axes(
        semi_major_var=400.0,  # m^2 -> sigma_major ~= 20 m
        semi_minor_var=100.0,  # m^2 -> sigma_minor = 10 m
        orientation_deg_in=37.0,  # arbitrary
    )

    ellipse = covariance_to_ellipse(true_cov)

    # Draw samples from N(0, true_cov). ``rng.multivariate_normal`` is
    # the canonical way.
    n_samples = 10_000
    samples = seeded_rng.multivariate_normal(
        mean=np.zeros(2, dtype=np.float64),
        cov=true_cov,
        size=n_samples,
    )

    # Test inclusion: a point (x, y) is inside the ellipse iff its
    # coordinates in the ellipse's axis-aligned frame, scaled by the
    # semi-axes, lie inside the unit circle. The frame is rotated by
    # ``orientation_deg`` (East-toward-North), so we un-rotate.
    theta = math.radians(ellipse.orientation_deg)
    c, s = math.cos(theta), math.sin(theta)
    # Rotation matrix that maps the ellipse frame -> world frame is
    # [[c, -s], [s, c]]; its transpose ([[c, s], [-s, c]]) maps world
    # -> ellipse frame.
    rotated = samples @ np.array([[c, s], [-s, c]], dtype=np.float64).T
    normalised = (rotated[:, 0] / ellipse.semi_major_m) ** 2 + (
        rotated[:, 1] / ellipse.semi_minor_m
    ) ** 2
    inside_fraction = float(np.mean(normalised <= 1.0))

    assert _INCLUSION_RATE_LOWER <= inside_fraction <= _INCLUSION_RATE_UPPER, (
        f"95% ellipse inclusion rate {inside_fraction:.3f} not in "
        f"[{_INCLUSION_RATE_LOWER}, {_INCLUSION_RATE_UPPER}]; chi-square "
        "scaling is wrong somewhere (sigma units, eigenvalue sort order, "
        "or the CHI2_95_DF2 constant itself)."
    )


# ---------------------------------------------------------------------------
# §7 Singular / non-PSD inputs raise loudly
# ---------------------------------------------------------------------------


def test_singular_fisher_information_raises_on_collinear_geometry(
    make_bearing: MakeBearing,
) -> None:
    """All nodes collinear with the emitter -> ``SingularFisherInformationError``.

    When every node sits on a single line through the emitter, every
    Jacobian row ``[-Δn/r², Δe/r²]`` is parallel to the same 2-vector
    (the 90 deg rotation of the shared LOS direction). ``J^T W J`` is
    therefore rank-1 and singular; ``numpy.linalg.solve`` raises
    ``LinAlgError`` which the module wraps into the domain exception.

    In production this case is filtered upstream by ``stansfield_seed``
    (it raises ``DegenerateGeometryError`` first), but the local check
    is the Invariant-B3 safety net for future refactors that change
    the upstream gating.
    """
    # Three nodes spaced along +East from the emitter at the origin.
    # Every node-to-emitter LOS points along -East (azimuth 270 deg);
    # the perpendicular direction is +/-North, so each Jacobian row is
    # ``[0, +Δe/r²]`` -- all rows collinear, ``J^T W J`` is rank-1.
    nodes_enu: tuple[tuple[float, float], ...] = (
        (1000.0, 0.0),
        (2000.0, 0.0),
        (3000.0, 0.0),
    )
    emitter_xy = (0.0, 0.0)
    # Each node looks back at the emitter (azimuth 270 deg = due
    # West). The actual azimuth value does not matter for the
    # Fisher-info matrix at this evaluation point -- only the
    # node-emitter geometry does -- but we set it correctly so the
    # bearings are not contract-invalid for any incidental check.
    bearings = [
        make_bearing(node_id=f"col-{i}", azimuth_deg=270.0, sigma_deg=5.0)
        for i in range(len(nodes_enu))
    ]

    with pytest.raises(SingularFisherInformationError) as exc_info:
        compute_covariance(emitter_xy, bearings, nodes_enu)
    msg = str(exc_info.value).lower()
    assert "singular" in msg or "rank" in msg


def test_compute_covariance_raises_on_emitter_at_node(
    make_bearing: MakeBearing,
) -> None:
    """Emitter exactly on a node -> ``SingularFisherInformationError``.

    ``r^2 = 0`` for that node makes the Jacobian row infinite; the
    module refuses to invent a derivative (Invariant B3) and raises.
    """
    nodes_enu: tuple[tuple[float, float], ...] = (
        (0.0, 0.0),
        (1000.0, 0.0),
        (0.0, 1000.0),
    )
    bearings = _bearings_for_geometry(
        make_bearing,
        nodes_enu[1:],  # avoid building a bearing AT the first node
        (5.0, 5.0),
        (0.0, 0.0),
    )
    # Repeat the first node to keep counts aligned; the test point
    # coincides with node 0.
    bearings = [
        make_bearing(node_id="at-node", azimuth_deg=0.0, sigma_deg=5.0),
        *bearings,
    ]

    with pytest.raises(SingularFisherInformationError) as exc_info:
        compute_covariance((0.0, 0.0), bearings, nodes_enu)
    msg = str(exc_info.value).lower()
    assert "node" in msg or "singular" in msg


def test_compute_covariance_raises_on_too_few_bearings(
    make_bearing: MakeBearing,
) -> None:
    """Single bearing -> ``SingularFisherInformationError``.

    One bearing is a ray, not a fix; two is the floor for any
    meaningful covariance. The function refuses below that (same
    floor as ``stansfield_seed`` and ``solve_mle``).
    """
    bearings = [make_bearing(node_id="lone", azimuth_deg=45.0, sigma_deg=5.0)]
    nodes_enu = [(0.0, 0.0)]
    with pytest.raises(SingularFisherInformationError):
        compute_covariance((100.0, 100.0), bearings, nodes_enu)


def test_compute_covariance_raises_on_length_mismatch(
    make_bearing: MakeBearing,
) -> None:
    """``len(bearings) != len(node_positions_enu)`` -> raises."""
    bearings = [
        make_bearing(node_id="a", azimuth_deg=0.0, sigma_deg=5.0),
        make_bearing(node_id="b", azimuth_deg=90.0, sigma_deg=5.0),
    ]
    nodes_enu = [(0.0, 0.0), (1000.0, 0.0), (0.0, 1000.0)]
    with pytest.raises(SingularFisherInformationError):
        compute_covariance((500.0, 500.0), bearings, nodes_enu)


def test_negative_eigenvalue_raises() -> None:
    """Non-PSD input matrix -> ``SingularFisherInformationError``.

    Pass a diagonal matrix with a negative entry directly to
    ``covariance_to_ellipse`` (this is not a runtime path on real
    inputs, but defends the function in isolation against a future
    refactor that adds caller-side filtering and silently clamps).
    """
    cov = np.array([[100.0, 0.0], [0.0, -25.0]], dtype=np.float64)
    with pytest.raises(SingularFisherInformationError) as exc_info:
        covariance_to_ellipse(cov)
    msg = str(exc_info.value).lower()
    assert "eigenvalue" in msg or "psd" in msg or "non-psd" in msg


def test_covariance_to_ellipse_raises_on_wrong_shape() -> None:
    """Non-(2,2) input -> ``SingularFisherInformationError``.

    Defensive shape check; surfaces a structural error rather than
    letting ``np.linalg.eigh`` raise its own message that does not
    mention the fusion module.
    """
    cov = np.eye(3, dtype=np.float64)
    with pytest.raises(SingularFisherInformationError) as exc_info:
        covariance_to_ellipse(cov)
    assert "shape" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# §8 Exception hierarchy assertions (mirror test_exceptions.py format)
# ---------------------------------------------------------------------------


def test_singular_fisher_information_error_subclasses_fusion_error() -> None:
    """``SingularFisherInformationError`` derives from ``FusionError``."""
    assert issubclass(SingularFisherInformationError, FusionError)


def test_singular_fisher_information_error_is_sibling_of_others() -> None:
    """``SingularFisherInformationError`` is a *sibling* of the other two.

    Three different stages, three different failure modes, three
    sibling exceptions all under ``FusionError``. A future ``except
    DegenerateGeometryError`` block must not silently swallow a
    Fisher-information failure (Invariant B3 -- silent swallow there
    would let a non-PSD covariance through into the EllipseENU
    validator, where the failure becomes much harder to diagnose).
    """
    assert not issubclass(SingularFisherInformationError, DegenerateGeometryError)
    assert not issubclass(SingularFisherInformationError, MLEConvergenceError)
    assert not issubclass(DegenerateGeometryError, SingularFisherInformationError)
    assert not issubclass(MLEConvergenceError, SingularFisherInformationError)


# ---------------------------------------------------------------------------
# §9 Smoke: covariance_m2 tuple shape compatible with FixEvent contract
# ---------------------------------------------------------------------------


def test_covariance_matrix_to_tuple_shape(
    make_bearing: MakeBearing,
) -> None:
    """``(cov[0,0], cov[0,1], cov[1,1])`` is the FixEvent.covariance_m2 layout.

    The ticket's out-of-scope list keeps the conversion to a tuple
    out of this module (WS-CD-007 does it), but the test pins that
    the matrix the module returns is the right shape to drive that
    conversion -- the symmetric ``[[xx, xy], [xy, yy]]`` layout the
    contract requires (INTERFACES.md §3).
    """
    nodes_enu: tuple[tuple[float, float], ...] = (
        (0.0, 0.0),
        (1000.0, 0.0),
        (500.0, 1000.0),
    )
    emitter = (500.0, 2000.0)
    bearings = _bearings_for_geometry(
        make_bearing,
        nodes_enu,
        (5.0, 5.0, 5.0),
        emitter,
    )
    cov = compute_covariance(emitter, bearings, nodes_enu)
    assert cov.shape == (2, 2)
    # Symmetric to within rounding.
    assert math.isclose(float(cov[0, 1]), float(cov[1, 0]), abs_tol=1e-12)
