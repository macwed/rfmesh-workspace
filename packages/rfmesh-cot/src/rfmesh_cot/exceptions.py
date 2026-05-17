"""Exceptions raised by ``rfmesh-cot``.

Per ``ARCHITECTURE.md`` §3, the contracts package carries no exception
hierarchy; exceptions live with the workstream that raises them. The CoT
workstream owns these three.

The hierarchy is intentionally shallow:

* ``CotError``           -- base class for everything in this package.
* ``CotEncodingError``   -- failure to turn a ``FixEvent`` into CoT XML
                            (bad ellipse, malformed input -- contract
                            said it could not happen but did).
* ``CotTransportError``  -- failure to ship an already-encoded CoT blob
                            over PyTAK's TCP/UDP transport (connection
                            refused, broken pipe, write timeout).

Both subclasses exist so callers can distinguish "the message was
unshippable" from "the transport was broken" -- the former is a code
bug, the latter is operational and may be retried. Neither is ever
silently swallowed (Invariant B3, ``AGENTS.md`` §1 Invariant 4).
"""

from __future__ import annotations


class CotError(Exception):
    """Base class for every error raised by ``rfmesh_cot``."""


class CotEncodingError(CotError):
    """Raised when a ``FixEvent`` cannot be serialised to CoT XML.

    Typically a programming error: the input was malformed in a way the
    Pydantic validators would normally catch but the caller bypassed
    them (e.g. by constructing a ``FixEvent`` from a dict with
    ``model_construct``).
    """


class CotTransportError(CotError):
    """Raised when shipping an encoded CoT blob to the TAK endpoint failed.

    Wraps the underlying transport exception (connection refused, broken
    pipe, write timeout, closed writer). The publisher does not retry;
    surfacing the failure is the operational layer's call -- the
    publisher's only contract is "do not silently swallow it".
    """
