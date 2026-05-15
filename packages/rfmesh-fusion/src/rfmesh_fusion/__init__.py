"""rfmesh-fusion -- bearing cross-fix, GDOP, and confidence ellipses.

Workstream C+D. Implements ``rfmesh_contracts.Fuser``. Pure: no network,
no file I/O, no subprocess, no SDR access (Invariant 5). Must run fully
in pytest on a CI runner with no hardware.
"""
