"""Tests for the six `melitta_barista/sommelier/config/*` WS commands.

House WS shape: a hand-built `MagicMock` hass plus connection, `inspect.unwrap`
to strip the decorator stack, and assertions against
`connection.send_result.call_args.args[1]`.

The fixture stubs `hass.config.path` deliberately — on a bare `MagicMock` it
returns a `MagicMock`, and every snapshot helper takes a path, so the tests
would otherwise write somewhere unpredictable.
"""

from __future__ import annotations

import inspect
import json
import os
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.melitta_barista import (
    panel_api,
    sommelier_api,
    sommelier_backup,
)
from custom_components.melitta_barista.const import DOMAIN
from custom_components.melitta_barista.sommelier_db import SommelierDB

EXPORT_TYPE = "melitta_barista/sommelier/config/export"
IMPORT_TYPE = "melitta_barista/sommelier/config/import"
LIST_TYPE = "melitta_barista/sommelier/config/snapshots/list"
GET_TYPE = "melitta_barista/sommelier/config/snapshots/get"
RESTORE_TYPE = "melitta_barista/sommelier/config/snapshots/restore"
DELETE_TYPE = "melitta_barista/sommelier/config/snapshots/delete"

ALL_TYPES = [
    EXPORT_TYPE,
    IMPORT_TYPE,
    LIST_TYPE,
    GET_TYPE,
    RESTORE_TYPE,
    DELETE_TYPE,
]

HANDLERS = {
    EXPORT_TYPE: sommelier_api.ws_config_export,
    IMPORT_TYPE: sommelier_api.ws_config_import,
    LIST_TYPE: sommelier_api.ws_snapshots_list,
    GET_TYPE: sommelier_api.ws_snapshots_get,
    RESTORE_TYPE: sommelier_api.ws_snapshots_restore,
    DELETE_TYPE: sommelier_api.ws_snapshots_delete,
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def make_hass(tmp_path, known_entities=()):
    """MagicMock hass with a real config path, a sync executor and a states map."""
    hass = MagicMock()
    hass.data = {DOMAIN: {"ui_strings_version": "0.95.0b1"}}
    hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))

    async def fake_executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = fake_executor
    entities = set(known_entities)
    hass.states.get = lambda entity_id: (
        MagicMock() if entity_id in entities else None
    )
    hass.bus.async_fire = MagicMock()
    return hass


def make_connection(is_admin=True):
    """MagicMock WS connection with a controllable admin flag."""
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()
    connection.user.is_admin = is_admin
    return connection


async def call(hass, db, command_type, connection=None, **fields):
    """Invoke an unwrapped handler with the panel DB getter patched."""
    connection = connection or make_connection()
    msg = {"id": 11, "type": command_type, **fields}
    handler = inspect.unwrap(HANDLERS[command_type])
    with patch.object(
        panel_api, "_async_get_db", new=AsyncMock(return_value=db)
    ):
        await handler(hass, connection, msg)
    return connection


def result_of(connection):
    """The payload passed to `send_result`, after asserting no error."""
    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    return connection.send_result.call_args.args[1]


def error_of(connection):
    """The (code, message) pair passed to `send_error`."""
    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once()
    args = connection.send_error.call_args.args
    return args[1], args[2]


@pytest.fixture
async def db():
    """In-memory SommelierDB with the panel tables and a little content."""
    sdb = SommelierDB(":memory:")
    await sdb.async_setup()
    await panel_api._ensure_panel_schema(sdb)
    await sdb.async_add_bean(
        {
            "brand": "Melitta",
            "product": "BellaCrema",
            "roast": "dark",
            "bean_type": "arabica",
            "origin": "blend",
        }
    )
    await sdb.async_set_setting("llm_agent_id", "conversation.local")
    await sdb.async_set_preference("weather_entity", "weather.home")
    yield sdb
    await sdb.async_close()


async def export_bundle(hass, db, **fields):
    """Round-trip helper: run the export command and return its bundle."""
    connection = await call(hass, db, EXPORT_TYPE, include_history=False, **fields)
    return result_of(connection)["export"]


# ---------------------------------------------------------------------------
# Registration & schemas
# ---------------------------------------------------------------------------


def test_all_six_commands_registered():
    """Every handler is reachable — a missing registration is silent."""
    hass = MagicMock()
    hass.data = {}
    sommelier_api.async_register_websocket_handlers(hass)
    for command_type in ALL_TYPES:
        assert command_type in hass.data["websocket_api"]


def _schema_for(command_type):
    hass = MagicMock()
    hass.data = {}
    sommelier_api.async_register_websocket_handlers(hass)
    return hass.data["websocket_api"][command_type][1]


def test_export_schema_defaults_include_history_to_false():
    schema = _schema_for(EXPORT_TYPE)
    assert schema({"id": 1, "type": EXPORT_TYPE})["include_history"] is False
    assert schema(
        {"id": 1, "type": EXPORT_TYPE, "include_history": True}
    )["include_history"] is True


def test_import_schema_requires_export_and_defaults_the_flags():
    schema = _schema_for(IMPORT_TYPE)
    parsed = schema({"id": 1, "type": IMPORT_TYPE, "export": {}})
    assert parsed["include_history"] is False
    assert parsed["include_install_specific"] is True
    assert parsed["snapshot"] is True
    with pytest.raises(vol.Invalid):
        schema({"id": 1, "type": IMPORT_TYPE})
    with pytest.raises(vol.Invalid):
        schema({"id": 1, "type": IMPORT_TYPE, "export": "not a dict"})


@pytest.mark.parametrize("command_type", [GET_TYPE, RESTORE_TYPE, DELETE_TYPE])
def test_name_is_required(command_type):
    schema = _schema_for(command_type)
    schema({"id": 1, "type": command_type, "name": "a.json"})
    with pytest.raises(vol.Invalid):
        schema({"id": 1, "type": command_type})


def test_restore_defaults_include_history_to_true():
    """A snapshot is this install's own state — restore all of it by default."""
    schema = _schema_for(RESTORE_TYPE)
    parsed = schema({"id": 1, "type": RESTORE_TYPE, "name": "a.json"})
    assert parsed["include_history"] is True


@pytest.mark.parametrize("command_type", ALL_TYPES)
def test_command_requires_admin(command_type):
    """Behavioural probe — `require_admin` leaves no sentinel attribute.

    Walk the `__wrapped__` chain, pick the one sync layer (HA's admin gate) and
    confirm it raises `Unauthorized` for a non-admin user.
    """
    from homeassistant.exceptions import Unauthorized

    handler = HANDLERS[command_type]
    layers = [handler]
    fn = handler
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
        layers.append(fn)
    assert inspect.iscoroutinefunction(fn)
    admin_layer = next(
        (layer for layer in layers if not inspect.iscoroutinefunction(layer)),
        None,
    )
    assert admin_layer is not None, "no sync admin layer in the wrapper chain"

    connection = make_connection(is_admin=False)
    with pytest.raises(Unauthorized):
        admin_layer(
            MagicMock(), connection, {"id": 1, "type": command_type, "name": "a"}
        )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def test_export_returns_versioned_envelope_and_size(db, tmp_path):
    hass = make_hass(tmp_path)
    payload = result_of(
        await call(hass, db, EXPORT_TYPE, include_history=False)
    )
    assert payload["schema_version"] == 1
    assert payload["export"]["format"] == sommelier_backup.EXPORT_FORMAT
    assert payload["size_bytes"] > 0
    assert payload["export"]["integration_version"] == "0.95.0b1"


async def test_export_uses_the_panel_db_getter(db, tmp_path):
    """Only the panel getter bootstraps the five panel-owned tables.

    Both getters are patched, so this fails loudly if the handler reaches for
    the sommelier one — an export taken before any panel tab was opened would
    otherwise silently lose the Additives/Producers catalogue and the custom
    prompt templates.
    """
    hass = make_hass(tmp_path)
    connection = make_connection()
    panel_getter = AsyncMock(return_value=db)
    sommelier_getter = AsyncMock(return_value=db)
    handler = inspect.unwrap(HANDLERS[EXPORT_TYPE])
    with patch.object(panel_api, "_async_get_db", new=panel_getter), patch.object(
        sommelier_api, "_async_get_db", new=sommelier_getter
    ):
        await handler(
            hass,
            connection,
            {"id": 11, "type": EXPORT_TYPE, "include_history": False},
        )

    panel_getter.assert_awaited_once()
    sommelier_getter.assert_not_awaited()
    tables = result_of(connection)["export"]["tables"]
    for table in ("producers", "syrups", "toppings", "flavor_tags", "panel_prompts"):
        assert table in tables


async def test_export_reports_db_busy(db, tmp_path):
    hass = make_hass(tmp_path)
    async with sommelier_backup._BACKUP_LOCK:
        connection = await call(hass, db, EXPORT_TYPE, include_history=False)
    assert error_of(connection)[0] == "db_busy"


async def test_export_failure_is_reported_not_raised(db, tmp_path, monkeypatch):
    hass = make_hass(tmp_path)

    async def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(sommelier_backup, "async_build_export", boom)
    connection = await call(hass, db, EXPORT_TYPE, include_history=False)
    assert error_of(connection)[0] == "export_failed"


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


async def test_import_round_trip_writes_a_snapshot(db, tmp_path):
    hass = make_hass(tmp_path, {"conversation.local", "weather.home"})
    bundle = await export_bundle(hass, db)
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=True,
    )
    payload = result_of(connection)
    assert payload["schema_version"] == 1
    assert payload["snapshot"].startswith("pre-import-")
    directory = tmp_path / sommelier_backup.SNAPSHOT_DIR_NAME
    assert (directory / payload["snapshot"]).is_file()
    assert payload["skipped_install_specific"] == []


async def test_import_fires_the_bus_event_once(db, tmp_path):
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=False,
    )
    hass.bus.async_fire.assert_called_once()
    event_type, data = hass.bus.async_fire.call_args.args
    assert event_type == "melitta_barista_sommelier_imported"
    assert data["include_history"] is False
    assert "history_cleared" in data


async def test_no_bus_event_on_failure(db, tmp_path):
    hass = make_hass(tmp_path)
    await call(
        hass,
        db,
        IMPORT_TYPE,
        export={"format": "nope"},
        include_history=False,
        include_install_specific=True,
        snapshot=True,
    )
    hass.bus.async_fire.assert_not_called()


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda b: b.update({"format": "something.else"}), "unsupported_format"),
        (lambda b: b.update({"format_version": 99}), "unsupported_format"),
        (lambda b: b.update({"db_schema_version": 999}), "unsupported_db_schema"),
        (
            lambda b: b["tables"].update({"coffee_beans": [{"id": {"a": 1}}]}),
            "invalid_export",
        ),
    ],
)
async def test_import_error_codes(db, tmp_path, mutate, code):
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    mutate(bundle)
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=True,
    )
    assert error_of(connection)[0] == code


async def test_import_over_the_row_cap_refused(db, tmp_path, monkeypatch):
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    monkeypatch.setattr(sommelier_backup, "MAX_IMPORT_ROWS", 1)
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=True,
    )
    assert error_of(connection)[0] == "import_too_large"


async def test_import_reports_db_busy(db, tmp_path):
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    async with sommelier_backup._BACKUP_LOCK:
        connection = await call(
            hass,
            db,
            IMPORT_TYPE,
            export=bundle,
            include_history=False,
            include_install_specific=True,
            snapshot=True,
        )
    assert error_of(connection)[0] == "db_busy"


async def test_failed_snapshot_aborts_the_import(db, tmp_path, monkeypatch):
    """The DB must be untouched when the rollback artifact cannot be written."""
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    bundle["tables"]["coffee_beans"] = []

    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(sommelier_backup, "write_snapshot", boom)
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=True,
    )
    assert error_of(connection)[0] == "snapshot_failed"
    assert await db.async_list_beans()


async def test_missing_install_specific_entity_is_reported_not_written(
    db, tmp_path
):
    """A rejected value is reported AND the local one survives.

    Rejecting the bundle's value must not delete the install's own: the delete
    phase has already cleared the row by the time the check's verdict is
    applied, so "don't write the bundle's" has to mean "put mine back", not
    "leave it empty". Otherwise restoring your own snapshot — the exact case
    where the agent's integration failed to set up this boot — silently loses
    the LLM agent.
    """
    hass = make_hass(tmp_path)  # no entities exist at all
    bundle = await export_bundle(hass, db)
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=False,
    )
    payload = result_of(connection)
    assert sorted(payload["skipped_install_specific"]) == [
        "llm_agent_id",
        "weather_entity",
    ]
    settings = await db.async_get_settings()
    assert settings.get("llm_agent_id") == "conversation.local"
    prefs = await db.async_get_preferences()
    assert prefs.get("weather_entity") == "weather.home"


async def test_non_entity_agent_id_is_trusted_not_dropped(db, tmp_path):
    """`panel_api._check_llm_agent` trusts non-`conversation.` ids; so must this.

    A `smartchain.*` agent cannot be disproved through the state machine, so
    checking it there only ever produces a false negative.
    """
    hass = make_hass(tmp_path)  # nothing in the state machine
    await db.async_set_setting("llm_agent_id", "smartchain.my_agent")
    bundle = await export_bundle(hass, db)
    assert bundle["install_specific"]["llm_agent_id"] == "smartchain.my_agent"

    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=False,
    )
    payload = result_of(connection)
    assert "llm_agent_id" not in payload["skipped_install_specific"]
    assert (await db.async_get_settings())["llm_agent_id"] == "smartchain.my_agent"


async def test_existing_install_specific_entities_are_written(db, tmp_path):
    hass = make_hass(tmp_path, {"conversation.local", "weather.home"})
    bundle = await export_bundle(hass, db)
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=False,
    )
    assert result_of(connection)["skipped_install_specific"] == []
    assert (await db.async_get_settings())["llm_agent_id"] == "conversation.local"


@pytest.mark.parametrize("sentinel", ["homeassistant", ""])
async def test_agent_sentinels_skip_the_entity_check(db, tmp_path, sentinel):
    """`homeassistant` and `''` are valid agent ids, not entity ids."""
    hass = make_hass(tmp_path)  # nothing exists in the state machine
    bundle = await export_bundle(hass, db)
    bundle["install_specific"]["llm_agent_id"] = sentinel
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=False,
    )
    payload = result_of(connection)
    assert "llm_agent_id" not in payload["skipped_install_specific"]
    assert (await db.async_get_settings())["llm_agent_id"] == sentinel


async def test_flag_false_preserves_local_values_through_the_handler(db, tmp_path):
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    await db.async_set_setting("llm_agent_id", "conversation.mine")
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=False,
        snapshot=False,
    )
    assert result_of(connection)["skipped_install_specific"] == []
    assert (await db.async_get_settings())["llm_agent_id"] == "conversation.mine"


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


async def test_envelope_only_bundle_is_refused_and_changes_nothing(db, tmp_path):
    """A structurally empty file must be an error, never a silent wipe."""
    hass = make_hass(tmp_path)
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export={
            "format": sommelier_backup.EXPORT_FORMAT,
            "format_version": 1,
        },
        include_history=False,
        include_install_specific=True,
        snapshot=True,
    )
    assert error_of(connection)[0] == "invalid_export"
    assert await db.async_list_beans()


@pytest.mark.parametrize("value", [[1, 2, 3], "nope", 7])
async def test_install_specific_of_the_wrong_type_is_a_clean_error(
    db, tmp_path, value
):
    """It used to raise `AttributeError` out of the handler as unknown_error."""
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    bundle["install_specific"] = value
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=True,
    )
    assert error_of(connection)[0] == "invalid_export"


async def test_snapshot_over_the_row_cap_drops_its_history(db, tmp_path):
    """The undo artifact has to stay restorable, so history goes first."""
    hass = make_hass(tmp_path)
    await db.async_create_session(
        mode="surprise_me",
        preference=None,
        hopper1_bean_id=None,
        hopper2_bean_id=None,
        milk_types=["whole"],
        llm_agent="conversation.local",
        recipes=[
            {
                "name": f"Recipe {index}",
                "description": "a drink",
                "blend": 0,
                "component1": {"aroma": 2},
                "component2": {},
                "cup_type": "cup",
            }
            for index in range(3)
        ],
        profile_id=None,
    )
    config_rows = sommelier_backup.count_bundle_rows(
        await sommelier_backup.async_build_export(db)
    )
    full_rows = sommelier_backup.count_bundle_rows(
        await sommelier_backup.async_build_export(db, include_history=True)
    )
    assert full_rows > config_rows

    with patch.object(sommelier_backup, "MAX_IMPORT_ROWS", config_rows):
        name = await sommelier_api._async_take_snapshot(hass, db)

    written = sommelier_backup.read_snapshot(
        sommelier_api._backups_dir(hass), name
    )
    assert "history" not in written
    assert written["include_history"] is False
    assert not [k for k in written["counts"] if k.startswith("history.")]
    sommelier_backup.validate_bundle(written)  # i.e. a restore would take it


async def test_import_refused_when_its_own_snapshot_could_not_be_restored(
    db, tmp_path
):
    """Config alone over the cap: refuse BEFORE the DB is touched."""
    hass = make_hass(tmp_path)
    config_rows = sommelier_backup.count_bundle_rows(
        await sommelier_backup.async_build_export(db)
    )
    small_bundle = {
        "format": sommelier_backup.EXPORT_FORMAT,
        "format_version": 1,
        "tables": {"flavor_tags": [{"name": "nutty", "created_at": "2026"}]},
    }

    with patch.object(sommelier_backup, "MAX_IMPORT_ROWS", config_rows - 1):
        connection = await call(
            hass,
            db,
            IMPORT_TYPE,
            export=small_bundle,
            include_history=False,
            include_install_specific=True,
            snapshot=True,
        )

    assert error_of(connection)[0] == "import_too_large"
    assert await db.async_list_beans()
    assert sommelier_backup.list_snapshots(sommelier_api._backups_dir(hass)) == []


async def test_restoring_the_oldest_snapshot_does_not_prune_it(db, tmp_path):
    """Using a rollback point must not destroy it."""
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    directory = pathlib.Path(sommelier_api._backups_dir(hass))
    directory.mkdir(parents=True, exist_ok=True)
    names = [f"pre-import-2026010{index}T000000Z.json" for index in range(5)]
    for index, name in enumerate(names):
        (directory / name).write_text(json.dumps(bundle), encoding="utf-8")
        os.utime(directory / name, (1_700_000_000 + index, 1_700_000_000 + index))
    oldest = names[0]

    connection = await call(
        hass, db, RESTORE_TYPE, name=oldest, include_history=True
    )

    result_of(connection)
    assert (directory / oldest).exists()


async def test_hand_dropped_snapshot_name_is_restorable(db, tmp_path):
    """The README's escape hatch: a file copied in by hand, browser name and all."""
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    directory = pathlib.Path(sommelier_api._backups_dir(hass))
    directory.mkdir(parents=True, exist_ok=True)
    name = "melitta-sommelier-2026-09-10 (1).json"
    (directory / name).write_text(json.dumps(bundle), encoding="utf-8")

    listed = await call(hass, db, LIST_TYPE)
    assert name in {e["name"] for e in result_of(listed)["snapshots"]}

    fetched = await call(hass, db, GET_TYPE, name=name)
    assert result_of(fetched)["export"]["format"] == sommelier_backup.EXPORT_FORMAT

    deleted = await call(hass, db, DELETE_TYPE, name=name)
    assert result_of(deleted)["deleted"] is True
    assert not (directory / name).exists()


async def test_snapshots_list_is_empty_before_any_import(db, tmp_path):
    hass = make_hass(tmp_path)
    payload = result_of(await call(hass, db, LIST_TYPE))
    assert payload == {"schema_version": 1, "snapshots": []}


async def test_snapshots_list_after_an_import(db, tmp_path):
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=True,
    )
    payload = result_of(await call(hass, db, LIST_TYPE))
    assert len(payload["snapshots"]) == 1
    entry = payload["snapshots"][0]
    assert entry["source"] == "auto"
    assert entry["size_bytes"] > 0
    assert set(entry) == {"name", "created_at", "size_bytes", "source"}


async def test_snapshot_contains_history_even_when_the_import_did_not(
    db, tmp_path
):
    hass = make_hass(tmp_path)
    bundle = await export_bundle(hass, db)
    connection = await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=True,
    )
    name = result_of(connection)["snapshot"]
    payload = result_of(await call(hass, db, GET_TYPE, name=name))
    assert payload["export"]["include_history"] is True
    assert "history" in payload["export"]


async def test_snapshot_restore_round_trips(db, tmp_path):
    hass = make_hass(tmp_path, {"conversation.local", "weather.home"})
    bundle = await export_bundle(hass, db)
    await call(
        hass,
        db,
        IMPORT_TYPE,
        export=bundle,
        include_history=False,
        include_install_specific=True,
        snapshot=True,
    )
    snapshots = result_of(await call(hass, db, LIST_TYPE))["snapshots"]
    # Wipe the beans, then restore
    for bean in await db.async_list_beans():
        await db.async_delete_bean(bean["id"])
    assert not await db.async_list_beans()

    connection = await call(
        hass, db, RESTORE_TYPE, name=snapshots[0]["name"], include_history=True
    )
    payload = result_of(connection)
    assert payload["snapshot"].startswith("pre-import-")
    assert await db.async_list_beans()


async def test_snapshots_get_refuses_an_oversized_file(db, tmp_path, monkeypatch):
    hass = make_hass(tmp_path)
    directory = tmp_path / sommelier_backup.SNAPSHOT_DIR_NAME
    directory.mkdir(parents=True)
    (directory / "big.json").write_text("{}" + " " * 200, encoding="utf-8")
    monkeypatch.setattr(sommelier_backup, "MAX_SNAPSHOT_FILE_BYTES", 10)
    connection = await call(hass, db, GET_TYPE, name="big.json")
    assert error_of(connection)[0] == "snapshot_file_too_large"


async def test_snapshots_get_missing_file(db, tmp_path):
    hass = make_hass(tmp_path)
    connection = await call(hass, db, GET_TYPE, name="ghost.json")
    assert error_of(connection)[0] == "snapshot_not_found"


@pytest.mark.parametrize(
    "command_type", [GET_TYPE, RESTORE_TYPE, DELETE_TYPE]
)
async def test_traversal_name_refused(db, tmp_path, command_type):
    hass = make_hass(tmp_path)
    connection = await call(hass, db, command_type, name="../secrets.json")
    assert error_of(connection)[0] == "invalid_snapshot_name"


async def test_snapshots_delete_removes_one_file(db, tmp_path):
    hass = make_hass(tmp_path)
    directory = tmp_path / sommelier_backup.SNAPSHOT_DIR_NAME
    directory.mkdir(parents=True)
    (directory / "a.json").write_text("{}", encoding="utf-8")
    (directory / "b.json").write_text("{}", encoding="utf-8")

    payload = result_of(await call(hass, db, DELETE_TYPE, name="a.json"))

    assert payload == {"schema_version": 1, "deleted": True}
    assert {p.name for p in directory.iterdir()} == {"b.json"}


async def test_snapshots_delete_missing_file(db, tmp_path):
    hass = make_hass(tmp_path)
    connection = await call(hass, db, DELETE_TYPE, name="ghost.json")
    assert error_of(connection)[0] == "snapshot_not_found"
