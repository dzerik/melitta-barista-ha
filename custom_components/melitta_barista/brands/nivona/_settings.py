"""Verified setting masks and raw-value domains."""

from dataclasses import replace

from ...coffee_platform.domain import MachineCapabilities, SettingDescriptor
from ._options import _AUTO_OFF_9XX_OPTIONS, nivona_number_range


def settings_for_family(family: str, settings: tuple[SettingDescriptor, ...]) -> tuple[SettingDescriptor, ...]:
    """Withhold ambiguous values while keeping stable setting identities."""
    result = []
    for setting in settings:
        if setting.key == "auto_off":
            if family in ("700", "79x"):
                setting = replace(setting, options=tuple(x for x in setting.options if x[0] < 9))
            elif family in ("1040", "8000"):
                setting = replace(setting, options=_AUTO_OFF_9XX_OPTIONS[:10])
        if family == "8000" and setting.setting_id == 105:
            setting = replace(setting, is_verified=False, is_writable=False)
        result.append(setting)
    return tuple(result)


def can_read_setting(caps: MachineCapabilities | None, setting_id: int) -> bool:
    """Keep generic registers separate from the model-specific setting namespace."""
    if setting_id < 100:
        return setting_id != 15 and (caps is None or setting_id not in caps.unsupported_generic_setting_ids)
    return caps is not None and any(
        s.setting_id == setting_id and s.is_verified for s in caps.settings
    )


def validate_setting(caps: MachineCapabilities | None, setting_id: int, value: object) -> int:
    """Reject unsupported settings, ambiguous codes and fractional input before HW."""
    if not can_read_setting(caps, setting_id):
        raise ValueError(f"Setting {setting_id} is unsupported or unverified for this machine")
    if not isinstance(value, (int, float)):
        raise ValueError(f"Setting {setting_id} must be an integer")
    try:
        number = int(value)
    except (ValueError, OverflowError) as err:
        raise ValueError(f"Setting {setting_id} must be a finite integer") from err
    if number != value or not -2147483648 <= number <= 2147483647:
        raise ValueError(f"Setting {setting_id} must be a signed 32-bit integer")
    if setting_id >= 100:
        setting = next(s for s in caps.settings if s.setting_id == setting_id)
        if not setting.is_writable:
            raise ValueError(f"Setting {setting_id} is read-only")
        if setting.options:
            if number not in dict(setting.options):
                raise ValueError(f"Invalid value {number} for setting {setting_id}")
        else:
            minimum, maximum, _ = nivona_number_range(setting)
            if not minimum <= number <= maximum:
                raise ValueError(f"Setting {setting_id} must be between {minimum} and {maximum}")
    return number
