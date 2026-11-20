"""Incoming status semantics, independent of outgoing process-start codes."""

import struct

from ...coffee_platform.domain import MachineProcess, MachineStatus, Manipulation, SubProcess


FAMILY_STATUS_PROFILES = {
    "600": "legacy-600", "700": "ef573", "79x": "ef253",
    "900": "legacy", "900-light": "ef698",
    "1030": "ef568", "1040": "ef568", "8000": "ef1040", "9101": "ef1109",
}

_COMMON_MESSAGES = {
    1: "bu_removed", 2: "trays_missing", 3: "empty_trays", 4: "fill_water",
}
_EF253_MESSAGES = {
    **_COMMON_MESSAGES, 5: "fill_powder", 6: "fill_beans",
    20: "wait", 40: "process_aborted",
}
_MESSAGES = {
    "legacy-600": {2: "trays_missing", 4: "fill_water"},
    "ef1109": {**_COMMON_MESSAGES, 5:"close_powder_lid", 6:"fill_beans",
               7:"fill_powder", 8:"wait", 9:"other", 254:"process_aborted", 255:"recipe_not_valid"},
    "ef573": {
        **_COMMON_MESSAGES,
        **dict.fromkeys((5, 6, 8, 9, 10, 11, 12, 13, 14), "other"),
        7: "fill_powder", 16: "wait", 20: "confirm_rinse",
    },
    "ef253": _EF253_MESSAGES,
    "ef253-795": _EF253_MESSAGES,
    "ef698": {
        **_COMMON_MESSAGES, 5: "close_powder_lid", 10: "fill_beans",
        11: "recipe_not_valid", 20: "confirm_rinse", 40: "process_aborted",
    },
    "ef568": {**_COMMON_MESSAGES, 5: "close_powder_lid", 6: "fill_beans"},
    "ef1040": {
        **_COMMON_MESSAGES, 5: "fill_powder", 6: "fill_beans",
        254: "process_aborted", 255: "recipe_not_valid",
    },
}
_ACTIONS = {
    "bu_removed": Manipulation.BU_REMOVED,
    "trays_missing": Manipulation.TRAYS_MISSING,
    "empty_trays": Manipulation.EMPTY_TRAYS,
    "fill_water": Manipulation.FILL_WATER,
    "fill_powder": Manipulation.FILL_POWDER,
    "close_powder_lid": Manipulation.CLOSE_POWDER_LID,
    "confirm_rinse": Manipulation.FLUSH_REQUIRED,
}
_LEGACY_SUBPROCESS = {
    1: SubProcess.GRINDING, 2: SubProcess.COFFEE,
    3: SubProcess.STEAM, 4: SubProcess.WATER,
}
_SUBPROCESSES = {
    "ef1109": {2:SubProcess.GRINDING, 5:SubProcess.COFFEE, 7:SubProcess.STEAM,
               8:SubProcess.STEAM, 9:SubProcess.WATER},
    "ef573": _LEGACY_SUBPROCESS, "ef698": _LEGACY_SUBPROCESS,
    "ef568": _LEGACY_SUBPROCESS,
    "ef1040": {
        1: SubProcess.GRINDING, 2: SubProcess.COFFEE,
        4: SubProcess.STEAM, 8: SubProcess.WATER,
    },
}


def parse_status(
    family_key: str | None, data: bytes, *, status_profile_key: str | None = None,
) -> MachineStatus:
    """Decode HX with family-specific messages and observed process states.

    Nonzero messages block brewing, including those without a known action.
    The model-specific frother rinse reports progress in per mille.
    """
    profile = status_profile_key or FAMILY_STATUS_PROFILES.get(family_key or "", "unknown")
    if len(data) < 8:
        return MachineStatus(
            status_profile_key=profile, brew_blocked=True, prompt_confirmable=False,
        )
    raw_process, raw_sub, message, raw_progress = struct.unpack(">hhhh", data[:8])
    ready, product = (3, 4) if family_key in ("8000", "9101") else (8, 11)
    process = None
    if family_key in FAMILY_STATUS_PROFILES:
        process = {ready: MachineProcess.READY, product: MachineProcess.PRODUCT}.get(raw_process)
    if family_key == "9101" and raw_process == -1:
        process = MachineProcess.READY
    progress = raw_progress
    maintenance_raw_progress_max = None
    if family_key == "79x" and profile == "ef253-795" and raw_process == 13:
        process = MachineProcess.EASY_CLEAN
        maintenance_raw_progress_max = 1000
        progress = min(max(raw_progress, 0), maintenance_raw_progress_max) // 10

    sub_process = _SUBPROCESSES.get(profile, {}).get(raw_sub)
    if profile == "ef698" and process == MachineProcess.PRODUCT and raw_sub == 8:
        # Americano bypass water is part of the same whole-drink progress.
        sub_process = SubProcess.WATER

    key = "none" if message == 0 else _MESSAGES.get(profile, {}).get(message, "unknown")
    return MachineStatus(
        process=process,
        sub_process=sub_process,
        manipulation=_ACTIONS.get(key, Manipulation.NONE),
        progress=progress,
        raw_process=raw_process, raw_sub_process=raw_sub, raw_message=message,
        message_key=key, status_profile_key=profile,
        brew_blocked=message != 0,
        prompt_confirmable=(
            key == "confirm_rinse" and process in (MachineProcess.READY, MachineProcess.PRODUCT)
        ),
        raw_progress=raw_progress,
        maintenance_raw_progress_max=maintenance_raw_progress_max,
    )
