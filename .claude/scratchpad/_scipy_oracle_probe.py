"""Probe the in-house vs scipy LM disagreement on the oracle test."""
import math
import numpy as np
from scipy.optimize import least_squares  # type: ignore[import-untyped]
from rfmesh_contracts.geospatial import GeodeticPosition
from rfmesh_contracts.messages import BearingReport
from rfmesh_contracts.enums import Capability
from rfmesh_fusion.stansfield import stansfield_seed
from rfmesh_fusion.mle import solve_mle


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
sigma_deg = 5.0

# Same geometry as scipy test
side_m = 5196.0
circumradius_m = side_m / math.sqrt(3.0)
angles_rad = [math.radians(a) for a in (90.0, 210.0, 330.0)]
nodes = [
    (circumradius_m * math.cos(a), circumradius_m * math.sin(a))
    for a in angles_rad
]
emitter = (0.0, 0.0)

# Same RNG and draw as conftest seeded_rng
rng = np.random.default_rng(seed=20260517)
noise_draws_deg = rng.normal(loc=0.0, scale=sigma_deg, size=3)
print("noise draws:", noise_draws_deg)

bearings = []
for i, p in enumerate(nodes):
    ba = (az(p, emitter) + float(noise_draws_deg[i])) % 360.0
    bearings.append(mkb(ba, sigma_deg, origin_pos))

seed = stansfield_seed(bearings, nodes)
print("Stansfield seed:", seed)

# In-house at various tolerances
for tol in [1e-3, 1e-6, 1e-9, 1e-12]:
    r = solve_mle(bearings, nodes, seed, tol_m=tol, max_iter=500)
    print(f"  in-house tol={tol}: pos={r.position}, n_iter={r.n_iter}")

# Scipy
nodes_array = np.array(nodes, dtype=np.float64)
theta_meas = np.array([math.radians(b.azimuth_deg) for b in bearings])
sigma_rad = np.array([math.radians(b.azimuth_sigma_deg) for b in bearings])


def scipy_residual(x):
    de = x[0] - nodes_array[:, 0]
    dn = x[1] - nodes_array[:, 1]
    theta_pred = np.arctan2(de, dn)
    wrapped = np.arctan2(np.sin(theta_meas - theta_pred), np.cos(theta_meas - theta_pred))
    return wrapped / sigma_rad


def scipy_jac(x):
    de = nodes_array[:, 0] - x[0]
    dn = nodes_array[:, 1] - x[1]
    r_sq = de * de + dn * dn
    # Sign convention: scipy uses jac = d(residual)/dx; residual is
    # theta_meas - theta_pred (whitened), so d/dx = -d(theta_pred)/dx.
    # theta_pred derivatives are -dn/r^2 and de/r^2; whitened residual
    # is divided by sigma_rad. So J row: (dn/r^2, -de/r^2) / sigma_rad.
    jac = np.empty((len(de), 2))
    jac[:, 0] = (dn / r_sq) / sigma_rad
    jac[:, 1] = (-de / r_sq) / sigma_rad
    return jac


for method in ["lm", "trf"]:
    res = least_squares(
        scipy_residual,
        x0=np.array(seed),
        jac=scipy_jac,
        method=method,
        xtol=1e-15,
        ftol=1e-15,
        gtol=1e-15,
    )
    print(f"scipy {method} (analytic jac): pos={tuple(res.x)}, nfev={res.nfev}")

# Numerical scipy
for method in ["lm", "trf"]:
    res = least_squares(
        scipy_residual,
        x0=np.array(seed),
        method=method,
        xtol=1e-15,
        ftol=1e-15,
        gtol=1e-15,
    )
    print(f"scipy {method} (FD jac): pos={tuple(res.x)}, nfev={res.nfev}")
