"""both3-poc — PoC web ops layer (map + CoT/TAK send + ingest API) for rfmesh.

Lives entirely under deployment/. Imports rfmesh_contracts and rfmesh_cot
read-only; never modifies them (the contracts are frozen, Invariant B1).
"""

__version__ = "0.1.0"
