"""Make ``both3_poc`` importable when the package isn't pip-installed.

In the deployed image the package is installed to site-packages and this is a
no-op; for a bare ``uv run pytest`` from ``deployment/backend`` it puts ``src``
on the path. No ``__init__.py`` in tests/ (AGENTS.md repo convention).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
