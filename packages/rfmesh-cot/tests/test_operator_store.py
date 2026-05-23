"""Tests for ``OperatorMarkerStore`` -- the thread-safe marker registry.

Covers put/get/remove/all semantics plus a concurrency stress that
hammers the store from many threads to assert the single-lock design
loses no writes and that ``all()`` snapshots are safe to iterate while
the store is being mutated.
"""

from __future__ import annotations

import threading

from rfmesh_cot import OperatorMarker, OperatorMarkerStore


def _marker(uid: str, lat: float = 0.0) -> OperatorMarker:
    return OperatorMarker(template_key="hostile", uid=uid, lat_deg=lat, lon_deg=0.0)


def test_put_get_remove_roundtrip() -> None:
    store = OperatorMarkerStore()
    m = _marker("a")
    assert store.put(m) is m
    assert store.get("a") is m
    assert "a" in store
    assert len(store) == 1
    assert store.remove("a") is m
    assert store.get("a") is None
    assert store.remove("a") is None  # idempotent: nothing to un-send


def test_put_same_uid_is_edit() -> None:
    store = OperatorMarkerStore()
    store.put(_marker("a", lat=1.0))
    store.put(_marker("a", lat=2.0))  # move
    assert len(store) == 1
    got = store.get("a")
    assert got is not None and got.lat_deg == 2.0


def test_all_returns_snapshot_copy() -> None:
    store = OperatorMarkerStore()
    store.put(_marker("a"))
    snap = store.all()
    store.put(_marker("b"))  # mutate after snapshot
    assert len(snap) == 1  # snapshot unaffected
    assert len(store.all()) == 2


def test_concurrent_puts_lose_nothing() -> None:
    store = OperatorMarkerStore()
    n_threads = 16
    per_thread = 100
    barrier = threading.Barrier(n_threads)

    def worker(tid: int) -> None:
        barrier.wait()
        for i in range(per_thread):
            store.put(_marker(f"t{tid}-{i}"))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(store) == n_threads * per_thread


def test_all_iterable_during_concurrent_mutation() -> None:
    """``all()`` snapshot must be safe to iterate while writers churn."""
    store = OperatorMarkerStore()
    for i in range(50):
        store.put(_marker(f"seed-{i}"))
    stop = threading.Event()

    def churn() -> None:
        i = 0
        while not stop.is_set():
            store.put(_marker(f"churn-{i}"))
            store.remove(f"churn-{i}")
            i += 1

    writer = threading.Thread(target=churn)
    writer.start()
    try:
        # Iterate snapshots repeatedly; no RuntimeError ("dict changed
        # size during iteration") may escape.
        for _ in range(1000):
            total = sum(1 for _m in store.all())
            assert total >= 50
    finally:
        stop.set()
        writer.join()
