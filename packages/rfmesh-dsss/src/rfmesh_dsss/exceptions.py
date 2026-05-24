"""Internal exception hierarchy for ``rfmesh-dsss``.

Same governance as ``rfmesh-dsp``'s exception module: exceptions are
*not* part of ``rfmesh-contracts`` (ARCHITECTURE.md §3 -- the contracts
package stays pure data + Protocol; exceptions live with the workstream
that raises them). Cross-workstream callers handle errors at the
Protocol boundary; the comms loop in ``rfmesh-node`` surfaces failure
via structured node-status updates and refuses to silently lie about
link health (B3).

The hierarchy stays small on purpose. A new dsss-internal exception
class is justified only when a caller in the package genuinely needs
to distinguish it from a sibling class; otherwise it is a ``DsssError``
with a descriptive message.
"""

from __future__ import annotations


class DsssError(Exception):
    """Base class for all ``rfmesh-dsss`` internal exceptions.

    Subclasses surface specific failure modes a caller may need to
    distinguish. Outside ``rfmesh-dsss`` this class is not part of
    the contract surface; the framing / despread / acquisition
    functions surface failure via ``None`` / structured result
    objects where possible, and raise only on inputs that cannot
    physically be valid (e.g. zero LFSR seed, mismatched chip-rate).
    """


class InvalidPnSequenceError(DsssError):
    """Raised by ``pn_sequence`` when the LFSR configuration is invalid.

    Concretely: zero seed (fixed-point of any LFSR), taps with
    ``max(taps) != register_length``, empty taps, or any combination
    that cannot produce a maximal-length m-sequence. This is the
    DSP-side enforcement of the same invariants ``CommsConfig``
    validates at config-load time -- defensive, in case a caller
    bypasses the config layer (e.g. test code constructing PN
    directly).
    """


class AcquisitionFailedError(DsssError):
    """Raised by ``correlation.acquire_preamble`` when no preamble was found.

    Concretely: the correlation peak across the search window did not
    exceed the configured detection threshold, OR the peak was
    ambiguous (multiple peaks within a small ratio of each other --
    distinguishes preamble from a strong reflection). The caller
    (typically the comms loop) catches this and either retries the
    next slot or declares the link down loudly (B3) rather than
    handing the despreader a frame-start guess.
    """


class FrameDecodeError(DsssError):
    """Raised by ``framing.decode_frame`` on a structurally-invalid frame.

    Concretely: CRC mismatch, header length disagrees with payload
    length, sync-word bit-rotation more than the configured
    tolerance, or oversize payload exceeding
    ``CommsConfig.frame_payload_max_bytes``. Caller treats this as a
    dropped frame (not silently as an empty payload).
    """
