"""rfmesh-dsp -- L1 RSSI bearing estimation and L2 MUSIC/MVDR subspace DF.

Workstream B. Pure: no network, no file I/O beyond vendor data tables at
import, no subprocess, no SDR access (Invariant 5). Every new function must
have a golden-file test in ``tests/golden/`` (Invariant 3).
"""
