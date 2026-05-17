"""CLI entrypoints for ``apps/demo-replay``.

Two scripts, registered via ``[project.scripts]`` in this app's
``pyproject.toml``:

* ``rfmesh-demo-replay`` (``run_replay.run_replay_main``) -- play a
  scenario through the in-process orchestrator.
* ``rfmesh-demo-record`` (``run_record.run_record_main``) -- capture
  per-node IQ from the simulator (or real hardware once the SDR
  workstream's hardware path lands) into ``.iqx + .json`` files.
"""

from __future__ import annotations
