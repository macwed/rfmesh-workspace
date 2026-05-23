"""CRLB analysis for trench_demo.yaml geometry (throwaway scratch script).

ENU frame, origin at node centroid. Emitter at +north.

For bearings-only AoA in ENU plane, with azimuth measured CW from north:
    azimuth_i = atan2(E - e_i, N - n_i)
Jacobian row of the predicted azimuth w.r.t. emitter (E, N):
    d(theta_i)/dE = (N - n_i) / r_i^2
    d(theta_i)/dN = -(E - e_i) / r_i^2
where r_i = || x - p_i ||, theta in radians.

Fisher info: F = H^T W H, with W = diag(1/sigma_i^2) in rad^-2.
Cov = F^-1 (m^2).
Eigenvalues -> 95% ellipse semi-axes via sqrt(chi2_0.95_df2 * lambda).

GDOP per ADR-007 D4: sqrt(trace((H^T H)^-1)), unweighted H,
normalised by mean square node-emitter range so the result is
dimensionless.
"""

import math

import numpy as np

CHI2_95_DF2 = 5.991464547107979  # scipy.stats.chi2.ppf(0.95, df=2)


def jac_row(emitter_xy, node_xy):
    e_em, n_em = emitter_xy
    e_n, n_n = node_xy
    dE = e_em - e_n
    dN = n_em - n_n
    r2 = dE * dE + dN * dN
    return np.array([dN / r2, -dE / r2])


def crlb(emitter_xy, nodes_xy, sigmas_deg):
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


# Two-emitter-range sweep: 2 km and 3 km. Three L1 nodes flanking the
# emitter on a wide arc; L2 forward observation post.
emitter = (0.0, 3000.0)

# L1 nodes: wide arc, ~2 km from emitter, around the south + flanks.
node_A = (-1800.0, +1800.0)  # NW flank
node_B = (+1800.0, +1800.0)  # NE flank
node_C = (    0.0,  +900.0)  # central south
l1_nodes = [node_A, node_B, node_C]
l1_sigmas = [5.0, 5.0, 5.0]

# L2 node off east-NE forward, ~2 km from emitter, distinct baseline.
node_D = (+2000.0, +3500.0)
all_nodes = l1_nodes + [node_D]
all_sigmas = l1_sigmas + [1.5]


print("\n>>>>> SIMULATOR-LEVEL SIGMAS (clean) <<<<<\n")
report("Beat B: two L1 nodes (A+B)", emitter, [node_A, node_B], [5.0, 5.0])
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
