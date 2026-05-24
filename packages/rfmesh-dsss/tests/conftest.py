"""Pytest fixtures for ``rfmesh-dsss``.

Iter 0 ships this file with no fixtures -- the package skeleton has
nothing to fixture yet. Iter 1 adds fixtures for PN sequences,
generated frames at known seeds, and the SNR-sweep scenario the
``test_ber_honesty.py`` Monte-Carlo runs against. Pattern follows
``packages/rfmesh-dsp/tests/conftest.py`` (scenario factories +
deterministic seeds).
"""

from __future__ import annotations
