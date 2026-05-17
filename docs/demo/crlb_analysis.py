"""CRLB analysis for scenarios/trench_demo.yaml geometry.

Reproduces the numbers in docs/demo/trench-demo-geometry.md §2.2 and §5,
and the per-beat ``expected_fix`` values in scenarios/trench_demo.yaml.

Run from the workspace root::

    uv run python docs/demo/crlb_analysis.py

ENU frame, origin at the L1 node centroid (the YAML's enu_origin would
locate the absolute lat/lon; the CRLB math is origin-independent).

For bearings-only AoA in the ENU plane, with azimuth measured CW from
north,

    theta_i(x) = atan2(x_E - p_{i,E}, x_N - p_{i,N})

Jacobian row of the predicted azimuth in *radians* w.r.t. emitter
position (x_E, x_N):

    d(theta_i)/dx_E =  (x_N - p_{i,N}) / r_i^2
    d(theta_i)/dx_N = -(x_E - p_{i,E}) / r_i^2

where r_i = || x - p_i ||.

Fisher info: F = H^T W H, with W = diag(1 / sigma_i^2) in rad^-2.
Cov = F^-1 (m^2).
Eigenvalues -> 95 % ellipse semi-axes via sqrt(chi2_{0.95, df=2} * lambda).

GDOP per ADR-007 D4: sqrt(trace((H^T H)^-1)), unweighted H, normalised
by mean square node-emitter range so the result is dimensionless.
"""

import math

import numpy as np

# scipy.stats.chi2.ppf(0.95, df=2)
CHI2_95_DF2 = 5.991464547107979


def jac_row(emitter_xy, node_xy):
    """Return [d(theta)/dE, d(theta)/dN] (rad / m) for one node."""
    e_em, n_em = emitter_xy
    e_n, n_n = node_xy
    dE = e_em - e_n
    dN = n_em - n_n
    r2 = dE * dE + dN * dN
    return np.array([dN / r2, -dE / r2])


def crlb(emitter_xy, nodes_xy, sigmas_deg):
    """Return (semi_major_m, semi_minor_m, orient_deg, gdop, ranges)."""
    H = np.array([jac_row(emitter_xy, p) for p in nodes_xy])
    sigmas_rad = np.array([math.radians(s) for s in sigmas_deg])
    W = np.diag(1.0 / (sigmas_rad ** 2))
    F = H.T @ W @ H
    cov = np.linalg.inv(F)
    eigvals, eigvecs = np.linalg.eigh(cov)
    semi_major = math.sqrt(CHI2_95_DF2 * eigvals[1])
    semi_minor = math.sqrt(CHI2_95_DF2 * eigvals[0])
    v_major = eigvecs[:, 1]
    orient_deg = math.degrees(math.atan2(v_major[1], v_major[0]))
    HtH_inv = np.linalg.inv(H.T @ H)
    ranges = [math.hypot(emitter_xy[0] - p[0], emitter_xy[1] - p[1])
              for p in nodes_xy]
    mean_sq_range = sum(r * r for r in ranges) / len(ranges)
    gdop = math.sqrt(np.trace(HtH_inv) / mean_sq_range)
    return semi_major, semi_minor, orient_deg, gdop, ranges


def report(label, emitter_xy, nodes, sigmas):
    sm, sn, ori, gdop, ranges = crlb(emitter_xy, nodes, sigmas)
    centroid = (sum(p[0] for p in nodes) / len(nodes),
                sum(p[1] for p in nodes) / len(nodes))
    range_m = math.hypot(emitter_xy[0] - centroid[0],
                         emitter_xy[1] - centroid[1])
    pct = 100.0 * sm / range_m
    print(f"=== {label} ===")
    for i, (p, s, r) in enumerate(zip(nodes, sigmas, ranges)):
        print(f"  node {i}: pos=({p[0]:+7.0f},{p[1]:+7.0f}) m  "
              f"sigma={s:.2f} deg  range={r:.0f} m")
    print(f"  emitter:    ({emitter_xy[0]:+7.0f},{emitter_xy[1]:+7.0f}) m")
    print(f"  centroid:   ({centroid[0]:+7.0f},{centroid[1]:+7.0f}) m  "
          f"range_m={range_m:.0f} m")
    print(f"  semi_major: {sm:7.2f} m   semi_minor: {sn:7.2f} m   "
          f"orient: {ori:+.1f} deg")
    print(f"  pct/range:  {pct:5.2f} %  "
          f"(BoTH3 spec <=1.0 % | HIGH band <=5.0 %)")
    print(f"  GDOP:       {gdop:.2f}")
    print()
    return sm, sn, gdop, range_m, pct


# ===== Geometry — matches scenarios/trench_demo.yaml =====

emitter = (0.0, 3000.0)

node_A = (-1800.0, +1800.0)   # NW flank
node_B = (+1800.0, +1800.0)   # NE flank
node_C = (    0.0,  +900.0)   # central south
l1_nodes = [node_A, node_B, node_C]
l1_sigmas = [5.0, 5.0, 5.0]

node_D = (+2000.0, +3500.0)   # L2 overwatch, NE forward
all_nodes = l1_nodes + [node_D]
all_sigmas = l1_sigmas + [1.5]


if __name__ == "__main__":
    print("\n>>>>> SIMULATOR-LEVEL SIGMAS (clean) <<<<<\n")
    report("Beat B: two L1 nodes (A+B)",
           emitter, [node_A, node_B], [5.0, 5.0])
    report("Beat C: three L1 nodes (A+B+C) -> MEDIUM target",
           emitter, l1_nodes, l1_sigmas)
    report("Beat D: three L1 + one L2 -> HIGH + spec-compliant",
           emitter, all_nodes, all_sigmas)

    print("\n>>>>> PHASE-C-PESSIMISTIC (2x multipath inflation) <<<<<\n")
    report("Beat C pess: three L1 (sigma=10 deg)",
           emitter, l1_nodes, [10.0, 10.0, 10.0])
    report("Beat D pess: three L1 (10 deg) + L2 (3 deg)",
           emitter, all_nodes, [10.0, 10.0, 10.0, 3.0])

    print("\n>>>>> 1.5x INFLATION (mid Phase-C pessimism) <<<<<\n")
    report("Beat C mid: three L1 (sigma=7.5 deg)",
           emitter, l1_nodes, [7.5, 7.5, 7.5])
    report("Beat D mid: three L1 (7.5 deg) + L2 (2.25 deg)",
           emitter, all_nodes, [7.5, 7.5, 7.5, 2.25])
