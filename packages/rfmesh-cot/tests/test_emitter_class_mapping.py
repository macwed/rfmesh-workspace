"""Tests for ``emitter_class_to_cot_type``: closed mapping, never raises.

Every ``EmitterClass`` member plus ``None`` must yield a valid CoT type
string. Adding a new class member without updating ``markers.py``'s
table must NOT crash the publisher; it must degrade to the nonspecific
electronic type. This file enforces that contract.
"""

from __future__ import annotations

import pytest
from rfmesh_contracts import EmitterClass
from rfmesh_cot import emitter_class_to_cot_type

#: CoT type for nonspecific electronic emitter. The catch-all.
_NONSPEC = "a-h-G-E-X-N"
_RC = "a-h-G-E-X-N-RC"
_JAMMER = "a-h-G-E-X-N-J"


@pytest.mark.parametrize(
    ("input_cls", "expected"),
    [
        (None, _NONSPEC),
        (EmitterClass.UNKNOWN, _NONSPEC),
        (EmitterClass.ELRS, _RC),
        (EmitterClass.CROSSFIRE, _RC),
        (EmitterClass.DRONEID, _RC),
        (EmitterClass.GSM_JAMMER, _JAMMER),
        (EmitterClass.POLE21, _JAMMER),
        (EmitterClass.VOLNOREZ, _JAMMER),
    ],
)
def test_emitter_class_mapping(
    input_cls: EmitterClass | None,
    expected: str,
) -> None:
    """Every documented mapping resolves to the right CoT type string."""
    assert emitter_class_to_cot_type(input_cls) == expected


def test_every_enum_member_maps_to_valid_cot_type() -> None:
    """Iterate over every EmitterClass; each must yield a known CoT type.

    Catches the regression where someone adds a new ``EmitterClass``
    member but forgets to add a row in ``markers.py``: the function
    still returns *something* valid (never raises), and that
    *something* is on the known type list.
    """
    valid_types = {_NONSPEC, _RC, _JAMMER}
    for cls in EmitterClass:
        result = emitter_class_to_cot_type(cls)
        assert result in valid_types, (
            f"EmitterClass.{cls.name} mapped to unknown CoT type {result!r}"
        )


def test_mapping_never_raises() -> None:
    """Even pathological inputs do not raise; they degrade to nonspecific.

    The publisher must never abort mid-demo because a future
    classifier produced an unmapped class. We verify this for every
    member and ``None``.
    """
    for cls in [None, *list(EmitterClass)]:
        try:
            _ = emitter_class_to_cot_type(cls)
        except Exception as exc:
            pytest.fail(f"emitter_class_to_cot_type raised on {cls!r}: {exc}")
