"""Compact factories for Nivona ``StatDescriptor`` rows.

Each family enumerates 15+ stat registers; declaring full
``StatDescriptor`` literals would obscure which IDs map to which
counters. ``_count`` / ``_pct`` / ``_flag`` keep one row = one line.

Maintenance-section rows get ``is_diagnostic=True`` so HA hides them
from the default device-card view; beverage counters stay primary.
"""

from __future__ import annotations

from ..base import StatDescriptor


def _count(
    stat_id: int, key: str, title: str, section: str = "beverages", *,
    value_mask: int | None = None, entity_key: str | None = None,
) -> StatDescriptor:
    """Beverage / maintenance counter (unit = ``count``)."""
    return StatDescriptor(
        stat_id=stat_id, key=key, title=title,
        unit="count", is_diagnostic=(section == "maintenance"),
        value_mask=value_mask, entity_key=entity_key,
    )


def _pct(stat_id: int, key: str, title: str) -> StatDescriptor:
    """Maintenance progress gauge (unit = ``%``)."""
    return StatDescriptor(
        stat_id=stat_id, key=key, title=title,
        unit="%", is_diagnostic=True,
    )


def _flag(stat_id: int, key: str, title: str) -> StatDescriptor:
    """Warning / status boolean (unit = ``None``)."""
    return StatDescriptor(
        stat_id=stat_id, key=key, title=title,
        unit=None, is_diagnostic=True,
    )
