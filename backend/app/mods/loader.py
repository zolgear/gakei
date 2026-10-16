"""Python の Mod のマニフェストの読み込みと import(ADR-0051 7章)。

`MODS_DIR`(既定は `DATA_DIR/mods`)の下に、Mod ごとのフォルダを置く。フォルダにはマニフェスト
`gakei-mod.toml` と Python のモジュールを置く。

    id = "local-archive"
    name = "ローカルに保存"
    version = "1.0.0"
    api_version = 1
    module = "main"            # フォルダの中の main.py(または main/__init__.py)
    events = ["run.finished"]

    [settings.folder]
    type = "path"              # string / number / bool / path / secret
    label = "保存先"           # 文字列、または { ja = "...", en = "..." }
    default = ""

- マニフェストは起動のときに読む。置いただけでは動かず、管理者が有効にしたものだけ import する。
- マニフェストの不正と import の失敗は、その Mod を「読み込みエラー」として一覧に出すだけで、
  起動は止めない。
- import は Mod のフォルダを1つのパッケージ(`_gakei_mods.<id>`)として行う。フォルダの中の
  ほかのモジュールは相対 import(`from . import util`)で読める。`sys.path` には足さない。
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import sys
import tomllib
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import app.mods  # noqa: F401 - `gakei_mod` を import できるようにする
from app.mods import api as contract
from app.mods.config import is_valid_mod_id

logger = logging.getLogger(__name__)

MANIFEST_NAME = "gakei-mod.toml"
SETTING_TYPES: tuple[str, ...] = ("string", "number", "bool", "path", "secret")
_PACKAGE_ROOT = "_gakei_mods"
_MESSAGE_MAX = 500


class ModLoadError(Exception):
    """マニフェストが不正、または import できなかった(文言は画面に出す)。"""


@dataclass(frozen=True)
class SettingField:
    key: str
    type: str
    # 画面に出す名前。言語ごとの dict か文字列。
    label: str | dict[str, str]
    default: Any = None

    @property
    def secret(self) -> bool:
        return self.type == "secret"


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    api_version: int
    module: str
    events: tuple[str, ...]
    settings: tuple[SettingField, ...] = ()


@dataclass
class ModFolder:
    """`MODS_DIR` の下のフォルダ1つ。マニフェストが読めなければ `manifest` が None で `error` に
    理由が入る。`id` はマニフェストの id(読めなければフォルダの名前)。"""

    id: str
    folder: Path
    manifest: Manifest | None = None
    error: str | None = None


@dataclass
class LoadedMod:
    """import した Mod。`handlers` はイベントごとの関数。"""

    manifest: Manifest
    module: types.ModuleType
    handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)


def _short(message: str) -> str:
    message = " ".join(message.split())
    return message[:_MESSAGE_MAX]


def _parse_label(value: object, key: str) -> str | dict[str, str]:
    if value is None:
        return key
    if isinstance(value, str) and value.strip():
        return value.strip()
    if (
        isinstance(value, dict)
        and value
        and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())
    ):
        return dict(value)
    raise ModLoadError(f"settings.{key}.label must be a string or a table of strings")


def _check_default(type_name: str, value: object, key: str) -> Any:
    if value is None:
        return None
    if type_name in ("string", "path") and isinstance(value, str):
        return value
    if type_name == "number" and isinstance(value, int | float) and not isinstance(value, bool):
        return value
    if type_name == "bool" and isinstance(value, bool):
        return value
    if type_name == "secret":
        raise ModLoadError(f"settings.{key}: a secret cannot have a default")
    raise ModLoadError(f"settings.{key}.default does not match the type {type_name}")


def parse_manifest(raw: dict[str, Any]) -> Manifest:
    """マニフェストの中身を検証する。不正なら ModLoadError。"""
    mod_id = raw.get("id")
    if not is_valid_mod_id(mod_id):
        raise ModLoadError("id must match [a-z0-9][a-z0-9_.-]{0,63}")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ModLoadError("name is required (up to 100 characters)")
    version = raw.get("version")
    if not isinstance(version, str) or not version.strip() or len(version) > 50:
        raise ModLoadError("version is required")
    api_version = raw.get("api_version")
    if isinstance(api_version, bool) or not isinstance(api_version, int):
        raise ModLoadError("api_version must be an integer")
    if api_version != contract.API_VERSION:
        raise ModLoadError(
            f"api_version {api_version} is not supported (this GAKEI supports "
            f"{contract.API_VERSION})"
        )
    module = raw.get("module")
    if (
        not isinstance(module, str)
        or not module
        or not all(part.isidentifier() for part in module.split("."))
    ):
        raise ModLoadError("module must be a Python module name")
    events_raw = raw.get("events")
    if not isinstance(events_raw, list) or not events_raw:
        raise ModLoadError("events must be a non-empty list")
    events: list[str] = []
    for event in events_raw:
        if event not in contract.EVENTS:
            raise ModLoadError(f"unknown event: {event}")
        if event not in events:
            events.append(event)
    settings_raw = raw.get("settings", {})
    if not isinstance(settings_raw, dict):
        raise ModLoadError("settings must be a table")
    settings: list[SettingField] = []
    for key, spec in settings_raw.items():
        if not key.isidentifier() or len(key) > 64:
            raise ModLoadError(f"settings.{key}: the key must be an identifier")
        if not isinstance(spec, dict):
            raise ModLoadError(f"settings.{key} must be a table")
        type_name = spec.get("type")
        if type_name not in SETTING_TYPES:
            raise ModLoadError(f"settings.{key}.type must be one of {', '.join(SETTING_TYPES)}")
        settings.append(
            SettingField(
                key=key,
                type=type_name,
                label=_parse_label(spec.get("label"), key),
                default=_check_default(type_name, spec.get("default"), key),
            )
        )
    return Manifest(
        id=mod_id,  # type: ignore[arg-type]
        name=name.strip(),
        version=version.strip(),
        api_version=api_version,
        module=module,
        events=tuple(events),
        settings=tuple(settings),
    )


def scan(mods_dir: Path) -> list[ModFolder]:
    """`mods_dir` の下のフォルダのマニフェストを読む。フォルダが無ければ空。並びは id の順。

    同じ id のマニフェストが2つあれば、後のもの(フォルダの名前の順)を読み込みエラーにする。
    """
    if not mods_dir.is_dir():
        return []
    found: list[ModFolder] = []
    seen: set[str] = set()
    for folder in sorted(p for p in mods_dir.iterdir() if p.is_dir()):
        if folder.name.startswith((".", "_")):
            continue
        manifest_path = folder / MANIFEST_NAME
        if not manifest_path.is_file():
            continue
        try:
            with manifest_path.open("rb") as f:
                raw = tomllib.load(f)
            manifest = parse_manifest(raw)
        except (OSError, tomllib.TOMLDecodeError, ModLoadError) as e:
            found.append(ModFolder(id=folder.name, folder=folder, error=_short(str(e))))
            continue
        if manifest.id in seen:
            found.append(
                ModFolder(
                    id=folder.name,
                    folder=folder,
                    error=_short(f"duplicate id: {manifest.id}"),
                )
            )
            continue
        seen.add(manifest.id)
        found.append(ModFolder(id=manifest.id, folder=folder, manifest=manifest))
    return sorted(found, key=lambda m: m.id)


def _package_name(mod_id: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in mod_id)
    return f"{_PACKAGE_ROOT}.{safe}"


def _new_package(name: str, path: Path | None) -> None:
    package = types.ModuleType(name)
    package.__path__ = [str(path)] if path is not None else []  # type: ignore[attr-defined]
    package.__package__ = name
    sys.modules[name] = package


def _purge(package: str) -> None:
    """前に import した同じ Mod(とその中のモジュール)を `sys.modules` から外す(読み直すため)。"""
    for name in [n for n in sys.modules if n == package or n.startswith(package + ".")]:
        del sys.modules[name]


def import_mod(entry: ModFolder) -> LoadedMod:
    """Mod を import して、購読するイベントの関数を集める。失敗したら ModLoadError。"""
    manifest = entry.manifest
    if manifest is None:
        raise ModLoadError(entry.error or "invalid manifest")
    package = _package_name(manifest.id)
    if _PACKAGE_ROOT not in sys.modules:
        _new_package(_PACKAGE_ROOT, None)
    _purge(package)
    importlib.invalidate_caches()
    _new_package(package, entry.folder)
    module_name = f"{package}.{manifest.module}"
    try:
        module = importlib.import_module(module_name)
    except Exception as e:  # noqa: BLE001 - Mod の import の失敗は、その Mod のエラーにする
        logger.warning("Mod %s を import できませんでした", manifest.id, exc_info=True)
        raise ModLoadError(_short(f"{e.__class__.__name__}: {e}")) from e
    handlers: dict[str, Callable[..., Any]] = {}
    for event in manifest.events:
        handler_name = contract.HANDLER_NAMES[event]
        handler = getattr(module, handler_name, None)
        if handler is None or not inspect.iscoroutinefunction(handler):
            raise ModLoadError(f"{handler_name} must be an async function")
        handlers[event] = handler
    return LoadedMod(manifest=manifest, module=module, handlers=handlers)
