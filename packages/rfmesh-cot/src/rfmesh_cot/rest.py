"""``FreeTakServerRestClient`` -- push point updates over the FTS REST API.

A Python port of the ``drupal/atak`` module (the "Atak / CiviTAK" client,
``atak-1.0.x``). That module is a thin Guzzle wrapper around the
**FreeTAKServer REST API** with one job that matters: it injects an
``Authorization: Bearer <api_key>`` header on every request
(``AddAuthorizationSubscriber.php``) and points at the ``manageAPI`` /
``ManageGeoObject`` endpoints. This module is the same idea in Python.

WHY A REST PATH AT ALL (we already have ``PyTAKCotPublisher``)
-------------------------------------------------------------
``PyTAKCotPublisher`` ships raw CoT XML over a long-lived TCP/UDP socket.
That works, but FreeTAKServer only *relays* a socket sender's CoT to the
other connected ATAK clients while that sender keeps behaving like a
connected client -- which is why the publisher has the ``publish_raw``
self-SA keepalive dance (see ``publisher.py``). The REST API sidesteps
all of that: a single authenticated HTTP ``POST`` hands FTS a point and
FTS owns the fan-out to every client. No persistent connection, no
self-SA keepalive, no relay caveat. For an operator dropping the
occasional marker, REST is the simpler, more robust path; the streaming
CoT publisher remains the right tool for the high-rate machine ``FixEvent``
flow and for polygons / deletes that the REST API does not model.

WHAT THE REST API DOES AND DOES NOT OFFER (FTS 1.9, verified against the
FreeTAKServer-User-Docs ``REST_API_PublicDoc``)
-------------------------------------------------------------------------
* ``GET  /manageAPI/getHelp``            -- API version + supported endpoints
                                            (the one operation the Drupal
                                            module modelled; we keep it as a
                                            connectivity / auth check).
* ``POST /ManageGeoObject/postGeoObject`` -- create a point marker. Returns
                                            its uid. Sending a ``uid`` makes
                                            ATAK update an existing marker.
* ``PUT  /ManageGeoObject/putGeoObject``  -- update/move an existing marker
                                            (uid required).
* ``GET  /ManageGeoObject/getGeoObject``  -- list markers in a radius (query
                                            params, not a JSON body).

There is **no** ``deleteGeoObject`` endpoint. A REST-placed marker is
removed by letting it stale out via its ``timeout`` (seconds). We do not
fake a delete (Invariant B3 -- no silent fallback): :meth:`expire_marker`
is an honest "make it stale now" (re-PUT with a 1 s timeout), and true
CoT ``t-x-d-d`` deletes stay on the ``PyTAKCotPublisher`` path.

The REST API also models points only -- no polygons. :meth:`publish_marker`
therefore accepts the affiliation point templates (hostile / friendly /
neutral / unknown, or any ``a-<affiliation>-...`` ``cot_type_override``)
and raises ``CotRestError`` for polygon templates or types with no REST
attitude, pointing the caller at the CoT path rather than guessing.

AUTH
----
Like the Drupal module, the token is a *static* FreeTAKServer System-User
token (generated in the FTS Web UI, "User" section -- the prefix
``Bearer`` is not part of the token). There is no programmatic login. We
send ``Authorization: Bearer <token>`` on every request when a token is
set, and omit the header when it is empty -- matching the Drupal
subscriber, which skips the header when the key is empty so a no-auth
deployment still works.

DEPENDENCIES
------------
Standard library only (``urllib``, ``json``). The REST surface is a
handful of JSON request/response round-trips; pulling in ``httpx`` /
``requests`` would be a new runtime dependency for no benefit (AGENTS.md
§3 -- dependency adds are lead-gated). The client is synchronous; the
caller may run it under ``asyncio.to_thread`` if it needs to.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Final

from .exceptions import CotRestError
from .operator import OperatorMarker, template

#: Default FreeTAKServer REST API port (FTS docs example uses 19023; the
#: CoT *streaming* port is 8087, a different service -- do not confuse the
#: two). Used only when the caller passes a base URL with no explicit port.
DEFAULT_REST_PORT: Final[int] = 19023

#: Default ``geoObject`` for a point with no more specific type. "Ground"
#: is in the FTS "Basic GeoObjects" list and renders as a plain affiliation
#: frame, so the marker's colour comes from ``attitude`` alone.
_DEFAULT_GEO_OBJECT: Final[str] = "Ground"

#: Default ``how`` -- FTS maps "nonCoT" to the CoT ``h-g-i-g-o`` ("human
#: entered, garbage-in-garbage-out") string, which is what ATAK writes for
#: a hand-dropped marker. Matches ``operator._HOW_HUMAN_INPUT``.
_DEFAULT_HOW: Final[str] = "nonCoT"

#: How precisely lat/lon are sent. 7 dp ~= 1.1 cm at the equator -- well
#: below any rfmesh sigma; more would be misleading precision (mirrors
#: ``markers._LATLON_PRECISION``).
_LATLON_PRECISION: Final[int] = 7

#: CoT affiliation character (2nd field of an ``a-<x>-...`` type) -> the
#: FTS REST ``attitude`` term. FTS rejects anything not in its list, so we
#: only map the affiliations FTS accepts.
_AFFILIATION_TO_ATTITUDE: Final[dict[str, str]] = {
    "h": "hostile",
    "f": "friendly",
    "n": "neutral",
    "u": "unknown",
    "p": "pending",
    "a": "assumed",
    "s": "suspect",
}


def attitude_from_cot_type(cot_type: str) -> str | None:
    """Map a CoT type string to the FTS REST ``attitude``, or ``None``.

    Only ``a-<affiliation>-...`` atom types carry an affiliation the REST
    API understands (``a-h-G`` -> ``"hostile"``). Tactical-graphic types
    (``b-m-p-w`` waypoint), drawing shapes (``u-d-f`` polygon) and unknown
    affiliations return ``None`` -- the caller decides what to do (we raise,
    rather than silently substituting an attitude).
    """
    atom, affiliation, *_rest = (*cot_type.split("-"), "")
    if atom != "a":
        return None
    return _AFFILIATION_TO_ATTITUDE.get(affiliation)


class FreeTakServerRestClient:
    """Thin authenticated client for the FreeTAKServer REST API.

    The Python equivalent of the ``drupal/atak`` Guzzle service plus its
    ``AddAuthorizationSubscriber``. Synchronous, stdlib-only.

    Examples
    --------
    .. code-block:: python

        client = FreeTakServerRestClient(
            "http://tak.example.com:19023",
            api_token="meg@secre7apip@guesmeIfyouCan",
        )
        client.get_help()                 # connectivity + auth smoke test
        uid = client.post_geo_object(
            name="Jammer A",
            latitude=50.066, longitude=4.866,
            attitude="hostile",
        )
        client.put_geo_object(            # move it
            uid=uid, latitude=50.07, longitude=4.87, attitude="hostile",
        )

    Parameters
    ----------
    base_url:
        Scheme + host (+ optional port) of the FTS REST API, e.g.
        ``http://tak.example.com:19023``. A trailing slash is fine. If no
        port is given, :data:`DEFAULT_REST_PORT` is appended.
    api_token:
        The FTS System-User token (no ``Bearer`` prefix). An empty string
        means "send no Authorization header" (a no-auth deployment), exactly
        like the Drupal subscriber's empty-key behaviour.
    timeout_s:
        Per-request socket timeout in seconds. Default 10.
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self._base_url = self._normalise_base_url(base_url)
        self._api_token = api_token
        self._timeout_s = timeout_s

    @staticmethod
    def _normalise_base_url(base_url: str) -> str:
        """Validate scheme/host and default the port; return a clean base."""
        parsed = urllib.parse.urlsplit(base_url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            msg = (
                f"base_url must be an http(s) URL with a host, "
                f"e.g. http://host:{DEFAULT_REST_PORT}; got {base_url!r}"
            )
            raise ValueError(msg)
        netloc = parsed.netloc
        if parsed.port is None:
            netloc = f"{parsed.hostname}:{DEFAULT_REST_PORT}"
        # Drop any path/query/fragment; we own the paths.
        return urllib.parse.urlunsplit((parsed.scheme, netloc, "", "", ""))

    # ----- low-level request ------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> tuple[int, str]:
        """Perform one HTTP round trip; return ``(status_code, body_text)``.

        Adds the Bearer header (when a token is set) and a JSON
        ``Content-Type`` for requests that carry a body. Any non-2xx
        status, connection failure, or timeout becomes a
        :class:`CotRestError` -- never swallowed (B3).
        """
        url = self._base_url + path
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        data: bytes | None = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_token:
            # The one line the whole Drupal module exists to do.
            headers["Authorization"] = f"Bearer {self._api_token}"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(  # noqa: S310 -- scheme validated above
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 -- scheme validated above
                request, timeout=self._timeout_s
            ) as response:
                status = int(response.status)
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # The server answered with a non-2xx; surface status + body.
            err_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            msg = f"{method} {path} -> HTTP {exc.code}"
            raise CotRestError(msg, status_code=exc.code, body=err_body) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Never reached the server (refused / DNS / timeout).
            msg = f"{method} {path} failed: {type(exc).__name__}: {exc}"
            raise CotRestError(msg, status_code=None) from exc
        return status, text

    # ----- public API (mirrors the FTS endpoints) ---------------------

    def get_help(self) -> dict[str, Any]:
        """``GET /manageAPI/getHelp`` -- API version + supported endpoints.

        The one endpoint the Drupal module modelled. Cheap connectivity and
        auth check: a 401/403 here means the token is wrong before you try
        to post a real marker. Returns the parsed JSON
        (``{"APIVersion": ..., "SupportedEndpoints": [...]}``).
        """
        _, text = self._request("GET", "/manageAPI/getHelp")
        return self._parse_json_object(text, "getHelp")

    def post_geo_object(
        self,
        *,
        name: str,
        latitude: float,
        longitude: float,
        attitude: str,
        geo_object: str = _DEFAULT_GEO_OBJECT,
        how: str = _DEFAULT_HOW,
        uid: str | None = None,
        timeout_s: float | None = None,
        remarks: str | None = None,
    ) -> str:
        """``POST /ManageGeoObject/postGeoObject`` -- create a point marker.

        Returns the marker uid (the server generates one when ``uid`` is
        omitted; FTS echoes it back). Passing a ``uid`` lets ATAK update an
        existing marker of that uid.

        ``attitude`` must be one of the FTS terms (hostile / friendly /
        neutral / unknown / pending / assumed / suspect / friend) and
        ``geo_object`` one of its named types or nicknames -- both are
        **case-sensitive** server-side; a typo is an HTTP 500 surfaced as
        ``CotRestError``.
        """
        body: dict[str, Any] = {
            "name": name,
            "latitude": round(latitude, _LATLON_PRECISION),
            "longitude": round(longitude, _LATLON_PRECISION),
            "attitude": attitude,
            "geoObject": geo_object,
            "how": how,
        }
        if uid is not None:
            body["uid"] = uid
        if timeout_s is not None:
            body["timeout"] = int(timeout_s)
        if remarks:
            body["remarks"] = remarks
        _, text = self._request("POST", "/ManageGeoObject/postGeoObject", body=body)
        return self._extract_uid(text, fallback=uid)

    def put_geo_object(
        self,
        *,
        uid: str,
        latitude: float,
        longitude: float,
        attitude: str,
        geo_object: str = _DEFAULT_GEO_OBJECT,
        how: str = _DEFAULT_HOW,
        name: str | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """``PUT /ManageGeoObject/putGeoObject`` -- update / move a marker.

        ``uid``, ``latitude``, ``longitude`` and ``attitude`` are all
        required by FTS. Returns the uid.
        """
        body: dict[str, Any] = {
            "uid": uid,
            "latitude": round(latitude, _LATLON_PRECISION),
            "longitude": round(longitude, _LATLON_PRECISION),
            "attitude": attitude,
            "geoObject": geo_object,
            "how": how,
        }
        if name is not None:
            body["name"] = name
        if timeout_s is not None:
            body["timeout"] = int(timeout_s)
        _, text = self._request("PUT", "/ManageGeoObject/putGeoObject", body=body)
        return self._extract_uid(text, fallback=uid)

    def get_geo_object(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_m: float = 100.0,
        attitude: str | None = None,
    ) -> Any:
        """``GET /ManageGeoObject/getGeoObject`` -- markers within a radius.

        Uses URL query parameters (not a JSON body), per the FTS doc.
        Returns the parsed JSON the server sends (typically an array).
        """
        query: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "radius": radius_m,
        }
        if attitude is not None:
            query["attitude"] = attitude
        _, text = self._request("GET", "/ManageGeoObject/getGeoObject", query=query)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"getGeoObject returned non-JSON: {text[:200]!r}"
            raise CotRestError(msg, body=text) from exc

    # ----- OperatorMarker adapter -------------------------------------

    def publish_marker(self, marker: OperatorMarker) -> str:
        """Push an :class:`OperatorMarker` as a REST point update.

        The bridge between this repo's operator vocabulary and the REST
        API. Resolves the marker's template (or ``cot_type_override``) to an
        FTS ``attitude`` and POSTs it. Re-publishing the same ``uid`` moves
        the marker, mirroring ``PyTAKCotPublisher.publish_marker``.

        Raises ``CotRestError`` (fail loud, B3) when the marker has no REST
        equivalent -- a polygon template, or a CoT type with no affiliation
        attitude -- naming the CoT path as the right tool instead of
        guessing an attitude. Returns the uid FTS reports.
        """
        tmpl = template(marker.template_key)
        if tmpl.geometry != "point":
            msg = (
                f"template {tmpl.key!r} is a {tmpl.geometry}; the FTS REST API "
                f"models points only. Use PyTAKCotPublisher.publish_marker for "
                f"polygons."
            )
            raise CotRestError(msg)
        cot_type = marker.cot_type_override or tmpl.cot_type
        attitude = attitude_from_cot_type(cot_type)
        if attitude is None:
            msg = (
                f"CoT type {cot_type!r} (template {tmpl.key!r}) has no FTS REST "
                f"attitude; only a-<affiliation>-... point types map. Use "
                f"PyTAKCotPublisher.publish_marker for this marker."
            )
            raise CotRestError(msg)
        stale_s = marker.stale_after_s if marker.stale_after_s is not None else tmpl.default_stale_s
        return self.post_geo_object(
            name=marker.callsign or tmpl.label,
            latitude=marker.lat_deg,
            longitude=marker.lon_deg,
            attitude=attitude,
            uid=marker.uid,
            timeout_s=stale_s,
            remarks=marker.remarks or None,
        )

    def expire_marker(self, marker: OperatorMarker) -> str:
        """Stale a REST-placed marker *now* (re-PUT with a 1 s timeout).

        The REST API has no delete endpoint, so this is the honest closest
        thing: tell FTS the marker times out in one second, after which
        every client drops it. For an immediate, explicit CoT delete
        (``t-x-d-d``), use ``PyTAKCotPublisher.delete_marker`` instead --
        which is why this takes the marker (it needs the last-known
        position FTS's ``putGeoObject`` requires), not just a uid.
        """
        tmpl = template(marker.template_key)
        cot_type = marker.cot_type_override or tmpl.cot_type
        attitude = attitude_from_cot_type(cot_type) or "unknown"
        return self.put_geo_object(
            uid=marker.uid,
            latitude=marker.lat_deg,
            longitude=marker.lon_deg,
            attitude=attitude,
            name=marker.callsign or tmpl.label,
            timeout_s=1.0,
        )

    # ----- response parsing -------------------------------------------

    @staticmethod
    def _parse_json_object(text: str, what: str) -> dict[str, Any]:
        """Parse ``text`` as a JSON object or raise ``CotRestError``."""
        try:
            parsed: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"{what} returned non-JSON: {text[:200]!r}"
            raise CotRestError(msg, body=text) from exc
        if not isinstance(parsed, dict):
            msg = f"{what} returned a {type(parsed).__name__}, expected an object"
            raise CotRestError(msg, body=text)
        return parsed

    @staticmethod
    def _extract_uid(text: str, *, fallback: str | None) -> str:
        """Pull the uid out of a post/put response.

        FTS documents the success body as "uid" -- in practice that is
        either a bare uid string or a small JSON object containing it. We
        accept both, and fall back to the uid we sent (``fallback``) when
        the body is empty but the HTTP status was success.
        """
        stripped = text.strip()
        if not stripped:
            if fallback is not None:
                return fallback
            msg = "geoObject call succeeded but returned no uid"
            raise CotRestError(msg)
        try:
            parsed: Any = json.loads(stripped)
        except json.JSONDecodeError:
            # Bare uid string (possibly quoted by the server already).
            return stripped.strip('"')
        if isinstance(parsed, str):
            return parsed
        if isinstance(parsed, dict):
            for key in ("uid", "UID", "Uid"):
                value = parsed.get(key)
                if isinstance(value, str):
                    return value
        # JSON we did not expect; prefer the uid we sent over guessing.
        if fallback is not None:
            return fallback
        msg = f"could not find a uid in geoObject response: {stripped[:200]!r}"
        raise CotRestError(msg, body=stripped)
