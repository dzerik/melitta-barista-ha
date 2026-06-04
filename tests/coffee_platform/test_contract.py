"""Contract compliance — MelittaBleClient must satisfy CoffeeMachineClient."""

from __future__ import annotations

import typing

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.coffee_platform.contract import (
    CoffeeMachineClient,
)


def _protocol_members(proto) -> set[str]:
    """Declared members of a Protocol: methods (via dir) + data attrs (via
    __annotations__), minus typing.Protocol machinery.

    Bare-annotation data attributes (e.g. `address: str`) don't appear in
    dir(proto) — they only live in __annotations__ — so we must union both
    sources to check the full contract surface.
    """
    base = set(dir(typing.Protocol))
    from_dir = {name for name in dir(proto) if not name.startswith("_")} - base
    from_annotations = set(getattr(proto, "__annotations__", {}))
    return from_dir | from_annotations


def test_melitta_client_satisfies_contract():
    """Every member declared in CoffeeMachineClient exists on MelittaBleClient.

    Catches contract drift: if the contract gains a member the Eugster client
    doesn't provide, this fails — forcing the provider to implement it or the
    contract to drop it.
    """
    required = _protocol_members(CoffeeMachineClient)
    missing = sorted(m for m in required if not hasattr(MelittaBleClient, m))
    assert not missing, f"MelittaBleClient missing contract members: {missing}"


import pathlib


def test_no_private_capabilities_leak_in_consumers():
    """Consumers must read `.capabilities` (contract), not `._capabilities`.

    Guards against re-introducing private-attribute coupling that would break
    when a non-Eugster brand provider (no `_capabilities` attr) is registered.
    The client's OWN backing field (`self._capabilities`) is allowed; reaching
    into another object's `_capabilities` is not.
    """
    root = (
        pathlib.Path(__file__).resolve().parents[2]
        / "custom_components"
        / "melitta_barista"
    )
    offenders = []
    for name in ("capabilities.py", "sommelier_api.py"):
        text = (root / name).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            # Match only lines that access `._capabilities` as an attribute
            # on *another* object (i.e., not `self._capabilities`).
            # We check for the string `"_capabilities"` in getattr calls and
            # for direct attribute access `.<something>._capabilities` — but
            # NOT lines that merely contain `_capabilities` as part of a longer
            # identifier like `derive_capabilities` or `machine_capabilities`.
            stripped = line.strip()
            if (
                '"_capabilities"' in line
                or "._capabilities" in line
            ) and "self._capabilities" not in line:
                offenders.append(f"{name}:{lineno}: {stripped}")
    assert not offenders, (
        "private _capabilities access in consumers:\n" + "\n".join(offenders)
    )
