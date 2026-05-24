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
* ``CotRestError``       -- failure to push a point update over the
                            FreeTAKServer REST API (HTTP non-2xx, a
                            connection error, or an unparseable response).

The first two subclasses let callers distinguish "the message was
unshippable" from "the transport was broken" -- the former is a code
bug, the latter is operational and may be retried. ``CotRestError`` is
the REST-path analogue of ``CotTransportError`` (the publisher uses raw
CoT over a socket; the REST client uses HTTP), and it carries the HTTP
``status_code`` and response ``body`` so a caller can tell a 401 (bad
token) from a 500 (bad payload) from a dead connection. None is ever
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


class CotRestError(CotError):
    """Raised when a FreeTAKServer REST API call failed.

    The REST-path analogue of :class:`CotTransportError`. Carries the
    HTTP ``status_code`` (``None`` if the request never got a response --
    DNS failure, connection refused, timeout) and the response ``body``
    text (empty string when there was none), so the caller can tell apart:

    * ``status_code == 401 / 403`` -- the Bearer token is missing or wrong;
    * ``status_code == 500`` -- the payload was rejected (FTS is strict and
      case-sensitive about ``geoObject`` / ``attitude`` values);
    * ``status_code is None`` -- the server was unreachable.

    Like every other error in this package it is never silently
    swallowed (Invariant B3).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
