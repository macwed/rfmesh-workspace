"""rfmesh-node -- asyncio node runtime.

Workstream C+D. Wires Receiver → DSP pipeline → Bearer. Detects hardware
capabilities at startup, intersects with ``NodeConfig.capabilities``, and
fails loudly on mismatch (Invariant 4). Tags every ``BearingReport`` with
the ``method`` actually used.
"""
