"""Register bases + address helpers for Nivona recipe regions.

Public constants:

- ``RECIPE_BASE_REGISTER`` / ``RECIPE_SLOT_STRIDE``
    Standard recipes live at ``10000 + selector*100``.
- ``TEMP_RECIPE_BASE_REGISTER`` / ``TEMP_RECIPE_TYPE_REGISTER``
    Single fixed temporary-override slot at ``9001`` — consumed by the
    next ``HE start_process`` and discarded.
- ``MY_COFFEE_BASE_REGISTER`` / ``MY_COFFEE_SLOT_STRIDE``
    MyCoffee user-defined slots at ``20000 + slot*100``.

Re-exported through ``brands.nivona`` because callers outside the
package (``_ble_commands.py``, ``test_ble_client.py``) reference
``TEMP_RECIPE_TYPE_REGISTER`` / ``MY_COFFEE_BASE_REGISTER`` /
``MY_COFFEE_SLOT_STRIDE`` directly.
"""

from __future__ import annotations

# Standard-recipe region: ``10000 + selector*100 + offset``.
RECIPE_BASE_REGISTER = 10000
RECIPE_SLOT_STRIDE = 100

# Per-brew temporary-override slot.
#
# The machine exposes a SINGLE fixed register (9001) for per-brew
# overrides — strength, two_cups, fluid amounts, temperatures — which
# is consumed by the next HE start_process and then discarded.
#
# Writing the same fields into the persistent per-selector slot
# (``10000 + selector*100 + offset``) permanently rewrites the standard
# recipe definition on the machine, which is why HA's previous
# implementation was data-destructive when users adjusted the Nivona
# override number entities.
TEMP_RECIPE_BASE_REGISTER = 9001  # overrides land at this + field offset
TEMP_RECIPE_TYPE_REGISTER = 9001  # same register, written first with
                                  # the recipe-class selector, telling
                                  # the firmware which recipe the
                                  # subsequent offsets belong to

# MyCoffee region: ``20000 + slot*100 + offset``.
MY_COFFEE_BASE_REGISTER = 20000
MY_COFFEE_SLOT_STRIDE = 100


def standard_recipe_register(selector: int, offset: int) -> int:
    """Absolute register ID for ``(selector, offset)`` in the standard-recipe region.

    Returns ``10000 + selector*100 + offset``.
    """
    return RECIPE_BASE_REGISTER + selector * RECIPE_SLOT_STRIDE + offset


def mycoffee_register(slot: int, offset: int) -> int:
    """Absolute register ID for ``(slot, offset)`` in the MyCoffee region.

    Returns ``20000 + slot*100 + offset``.
    """
    return MY_COFFEE_BASE_REGISTER + slot * MY_COFFEE_SLOT_STRIDE + offset
