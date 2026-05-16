"""Sample-covariance estimation and forward-backward smoothing for L2 subspace DF.

The sample covariance ``R = (1/L) sum_l x_l x_l^H`` is the matrix MUSIC
eigendecomposes (to separate signal and noise subspaces) and MVDR inverts
(to synthesise spatial nulls). This module produces R from a coherent IQ
block and offers the standard forward-backward preprocessing for
de-correlating coherent sources (multipath, coherent jammers).

The numerics deliberately upcast to ``complex128`` regardless of input
dtype: ``CoherentReceiver`` produces ``complex64`` IQ, and accumulating
``x x^H`` over thousands of samples in single precision loses Hermitian
symmetry to round-off well before the eigendecomposition does.
"""

from __future__ import annotations

import numpy as np

_DIM = 2


def _validate_coherent_block(block: np.ndarray) -> None:
    if not isinstance(block, np.ndarray):
        msg = (
            "block must be a numpy.ndarray of shape (n_channels, n_samples); "
            f"got {type(block).__name__}."
        )
        raise TypeError(msg)
    if block.ndim != _DIM:
        msg = f"block must be 2-D (n_channels, n_samples); got shape {block.shape}."
        raise ValueError(msg)
    if not np.issubdtype(block.dtype, np.complexfloating):
        msg = f"block must have a complex dtype; got {block.dtype}."
        raise ValueError(msg)
    n_channels, n_samples = block.shape
    if n_channels < _DIM:
        msg = (
            "block must have at least 2 channels for covariance to be "
            f"meaningful; got n_channels={n_channels}."
        )
        raise ValueError(msg)
    if n_samples == 0:
        msg = "block must have at least one sample; got n_samples=0."
        raise ValueError(msg)


def sample_covariance(
    block: np.ndarray,
    *,
    n_snapshots: int | None = None,
) -> np.ndarray:
    """Compute the snapshot-averaged sample covariance R.

    When ``n_snapshots`` is ``None`` or 1, treats the whole block as a single
    snapshot and returns ``R = (1/T) * block @ block.conj().T`` where T is
    ``n_samples``.

    When ``n_snapshots = K > 1``, splits the block into K contiguous
    equal-length segments and averages the per-segment covariances:
    ``R = (1/K) * sum_k (1/T_seg) * seg_k @ seg_k.conj().T`` with
    ``T_seg = T / K``. The total normalisation collapses to ``1/T`` again,
    so for any block ``R(K) == R(None)`` exactly -- the snapshot count only
    changes the *bookkeeping*. The split form is the natural one for
    forward-backward and for SNR-vs-snapshot diagnostics in MUSIC.

    Args:
        block: ``(n_channels, n_samples)`` complex array. Accepted dtypes:
            any complex floating type (``complex64`` from
            ``CoherentReceiver``; ``complex128`` for internal use).
        n_snapshots: Number of equal-length segments to average over. Must
            divide ``n_samples`` evenly. ``None`` (default) is equivalent
            to 1.

    Returns:
        ``(n_channels, n_channels) complex128`` Hermitian matrix.

    Raises:
        TypeError: If ``block`` is not an ``ndarray``.
        ValueError: If shape / dtype is wrong, ``n_snapshots`` is < 1, or
            ``n_snapshots`` does not evenly divide ``n_samples``.
    """
    _validate_coherent_block(block)
    n_channels, n_samples = block.shape
    block128 = block.astype(np.complex128, copy=False)
    k = 1 if n_snapshots is None else n_snapshots
    if k < 1:
        msg = f"n_snapshots must be >= 1 (or None); got {n_snapshots}."
        raise ValueError(msg)
    if n_samples % k != 0:
        msg = f"n_snapshots={k} does not evenly divide n_samples={n_samples}."
        raise ValueError(msg)
    seg_len = n_samples // k
    if k == 1:
        cov = block128 @ block128.conj().T
        cov /= seg_len
        return cov
    cov = np.zeros((n_channels, n_channels), dtype=np.complex128)
    for snapshot_idx in range(k):
        start = snapshot_idx * seg_len
        end = start + seg_len
        seg = block128[:, start:end]
        cov += seg @ seg.conj().T
    cov /= float(seg_len * k)
    return cov


def forward_backward_smooth(covariance: np.ndarray) -> np.ndarray:
    """Forward-backward averaging: ``R_fb = (R + J R* J) / 2``.

    ``J`` is the n-by-n exchange matrix (anti-diagonal of ones), so
    ``J R* J`` is the row-and-column-reversed conjugate of R. The averaged
    matrix de-correlates pairs of coherent sources whose steering vectors
    are related by the array's centro-symmetry: a standard subspace-DF
    preprocessing for multipath-rich environments. R_fb stays Hermitian when
    R is Hermitian.

    Args:
        covariance: ``(N, N)`` square complex matrix. Typically the output of
            ``sample_covariance``.

    Returns:
        Fresh ``(N, N) complex128`` Hermitian matrix.

    Raises:
        TypeError: If ``covariance`` is not an ``ndarray``.
        ValueError: If ``covariance`` is not 2-D and square.
    """
    if not isinstance(covariance, np.ndarray):
        msg = (
            f"covariance must be a numpy.ndarray of shape (N, N); got {type(covariance).__name__}."
        )
        raise TypeError(msg)
    if covariance.ndim != _DIM or covariance.shape[0] != covariance.shape[1]:
        msg = f"covariance must be a square 2-D matrix; got shape {covariance.shape}."
        raise ValueError(msg)
    cov = covariance.astype(np.complex128, copy=True)
    # J @ R* @ J reverses rows and columns. Two flips along each axis are
    # equivalent and avoid materialising J explicitly.
    reflected = np.flip(np.flip(cov.conj(), axis=0), axis=1)
    smoothed: np.ndarray = (cov + reflected) / 2.0
    return smoothed
