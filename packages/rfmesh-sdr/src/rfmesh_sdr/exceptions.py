"""Exceptions raised by rfmesh-sdr.

Each exception type makes a *specific* failure category loud at the call
site: a caller that reads before opening, a caller that asks for zero
samples. The Receiver Protocol contract is "no silent fallbacks"
(Invariant 4) -- silent short reads, silent zero-samples, silent
post-close reads are all banned. These exceptions are the surface those
bans are enforced through.
"""

from __future__ import annotations


class ReceiverNotOpenError(RuntimeError):
    """Raised when ``read()`` is called before ``open()`` or after ``close()``.

    A caller that reaches this state has a lifecycle bug: the DSP and node
    runtime depend on a strict ``open -> configure -> read* -> close``
    ordering, and a silent zero-buffer here would corrupt every downstream
    estimate.
    """


class InvalidReadSizeError(ValueError):
    """Raised when ``read(n)`` is called with ``n <= 0``.

    Asking a receiver for zero or negative samples is a programming error,
    not a graceful degradation case. Surfacing it as an exception keeps the
    Receiver Protocol's "exact-n or raise" guarantee honest.
    """


class CalibrationFailedError(RuntimeError):
    """Raised when ``CoherentReceiver.calibrate()`` cannot establish a valid solution.

    Surfaces the measured-vs-required SNR (or other failure mode) so the
    operator sees *why* calibration was refused rather than silently
    proceeding with an unusable calibration. ``is_calibrated`` remains
    ``False`` after this is raised -- there is no partial-success state.
    """


class MalformedIQFileError(ValueError):
    """Raised when an on-disk ``.iq`` capture cannot be interpreted as uint8 IQ.

    Concrete failure modes the I/O layer surfaces through this exception:

    * Odd byte count -- the rtl_sdr-style format is two bytes per sample
      (I + Q), so an odd file length cannot be a clean capture.
    * Payload size disagrees with the sidecar's ``n_samples`` -- the
      capture was truncated mid-write (disk full / process killed) or
      the sidecar was authored against a different file.
    * Sidecar JSON exists but is unparseable / has the wrong
      ``schema_version`` / fails Pydantic validation.

    Reading from a missing path raises ``FileNotFoundError`` directly --
    that is the stdlib idiom and any user already handles it. This
    exception covers the case where the file *exists* but its bytes do
    not honour the format contract.
    """
