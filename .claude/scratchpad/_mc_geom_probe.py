"""Probe Stansfield bias on different geometries to find a 15-30 m one."""
import math
import numpy as np
from rfmesh_contracts.geospatial import GeodeticPosition
from rfmesh_contracts.messages import BearingReport
from rfmesh_contracts.enums import Capability
from rfmesh_fusion.stansfield import stansfield_seed
from rfmesh_fusion.mle import solve_mle
from rfmesh_fusion.exceptions import DegenerateGeometryError, MLEConvergenceError


def az(node, em):
    de = em[0] - node[0]
    dn = em[1] - node[1]
    return math.degrees(math.atan2(de, dn)) % 360.0


def mkb(az_deg, sigma_deg, pos):
    return BearingReport(
        node_id="x",
        t_unix_ns=1_778_976_000_000_000_000,
        node_position=pos,
        azimuth_deg=az_deg,
        azimuth_sigma_deg=sigma_deg,
        method=Capability.L2_MUSIC,
        snr_db=None,
    )


origin_pos = GeodeticPosition(lat_deg=52.0, lon_deg=21.0, hae_m=0.0, sigma_m=5.0)
n_trials = 2000

# Larger sigma tests + N=2 cases.
for sigma_deg in [5.0]:
    print(f"\n=== sigma_deg = {sigma_deg} ===")
    geoms = {
        "Asym 2/3/5km": (
            [(2000.0, 0.0), (-1500.0, 2598.0), (-2500.0, -4330.0)],
            (0.0, 0.0),
        ),
        "Asym 1.5/3/5km": (
            [(1500.0, 0.0), (-1500.0, 2598.0), (-2500.0, -4330.0)],
            (0.0, 0.0),
        ),
        "Stretched 3/5/7km, R~5km": (
            [(3000.0, 0.0), (-2500.0, 4330.0), (-3500.0, -6062.0)],
            (0.0, 0.0),
        ),
        "Stretched 2/4/6km, R~4km": (
            [(2000.0, 0.0), (-2000.0, 3464.0), (-3000.0, -5196.0)],
            (0.0, 0.0),
        ),
        "Asym 4/6/8km, R~6km": (
            [(4000.0, 0.0), (-3000.0, 5196.0), (-4000.0, -6928.0)],
            (0.0, 0.0),
        ),
        "Asym 5/8/10km, R~8km": (
            [(5000.0, 0.0), (-4000.0, 6928.0), (-5000.0, -8660.0)],
            (0.0, 0.0),
        ),
    }
    for name, (nodes, emit) in geoms.items():
        rng_local = np.random.default_rng(seed=20260517)
        s_off, m_off = [], []
        for _ in range(n_trials):
            nz = rng_local.normal(loc=0.0, scale=sigma_deg, size=len(nodes))
            bearings = []
            for i, p in enumerate(nodes):
                ba = (az(p, emit) + float(nz[i])) % 360.0
                bearings.append(mkb(ba, sigma_deg, origin_pos))
            try:
                seed = stansfield_seed(bearings, nodes)
            except DegenerateGeometryError:
                continue
            try:
                res = solve_mle(bearings, nodes, seed)
            except MLEConvergenceError:
                continue
            s_off.append((seed[0] - emit[0], seed[1] - emit[1]))
            m_off.append((res.position[0] - emit[0], res.position[1] - emit[1]))
        s_arr = np.array(s_off)
        m_arr = np.array(m_off)
        s_bias = (
            float(np.linalg.norm(np.mean(s_arr, axis=0)))
            if len(s_off)
            else float("nan")
        )
        m_bias = (
            float(np.linalg.norm(np.mean(m_arr, axis=0)))
            if len(m_off)
            else float("nan")
        )
        print(
            f"  {name}: trials={len(s_off)}, S_bias={s_bias:.2f}m | "
            f"MLE_bias={m_bias:.2f}m"
        )

import sys
sys.exit(0)


geoms = {
    "Eq 3km, emit origin": (
        [(3000.0, 0.0), (-1500.0, 2598.0), (-1500.0, -2598.0)],
        (0.0, 0.0),
    ),
    "Asym 2.5/3/4km": (
        [(2500.0, 0.0), (-1500.0, 2598.0), (-2000.0, -3464.0)],
        (0.0, 0.0),
    ),
    "Asym 1.5/3/5km": (
        [(1500.0, 0.0), (-1500.0, 2598.0), (-2500.0, -4330.0)],
        (0.0, 0.0),
    ),
    "Tight 1/3/5km": (
        [(1000.0, 0.0), (-1500.0, 2598.0), (-2500.0, -4330.0)],
        (0.0, 0.0),
    ),
    "OffEmit (500,800) eq3km": (
        [(3000.0, 0.0), (-1500.0, 2598.0), (-1500.0, -2598.0)],
        (500.0, 800.0),
    ),
    "Collin-ish y=3km": (
        [(0.0, 3000.0), (1000.0, 3100.0), (-1000.0, 3100.0)],
        (0.0, 0.0),
    ),
    "Wide 2-3-6km": (
        [(2000.0, 0.0), (-1500.0, 2598.0), (-3000.0, -5196.0)],
        (0.0, 0.0),
    ),
    "Eq 5km": (
        [(5000.0, 0.0), (-2500.0, 4330.0), (-2500.0, -4330.0)],
        (0.0, 0.0),
    ),
}

for name, (nodes, emit) in geoms.items():
    rng_local = np.random.default_rng(seed=20260517)
    s_off, m_off = [], []
    for _ in range(n_trials):
        nz = rng_local.normal(loc=0.0, scale=sigma_deg, size=len(nodes))
        bearings = []
        for i, p in enumerate(nodes):
            ba = (az(p, emit) + float(nz[i])) % 360.0
            bearings.append(mkb(ba, sigma_deg, origin_pos))
        try:
            seed = stansfield_seed(bearings, nodes)
        except DegenerateGeometryError:
            continue
        try:
            res = solve_mle(bearings, nodes, seed)
        except MLEConvergenceError:
            continue
        s_off.append((seed[0] - emit[0], seed[1] - emit[1]))
        m_off.append((res.position[0] - emit[0], res.position[1] - emit[1]))
    s_arr = np.array(s_off)
    m_arr = np.array(m_off)
    s_bias = float(np.linalg.norm(np.mean(s_arr, axis=0))) if len(s_off) else float("nan")
    m_bias = float(np.linalg.norm(np.mean(m_arr, axis=0))) if len(m_off) else float("nan")
    print(
        f"{name}: trials={len(s_off)}, S_bias={s_bias:.2f}m | "
        f"MLE_bias={m_bias:.2f}m"
    )
