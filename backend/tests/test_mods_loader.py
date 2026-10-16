"""ADR-0051 7章: Python の Mod のマニフェストの読み込みと import。フォルダとファイルを読むので
Windows の CI でも回す。"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from app.config import Settings
from app.mods import api as contract
from app.mods import config as mods_config
from app.mods import loader
from app.mods.runtime import ModRuntime

pytestmark = pytest.mark.windows

_GOOD_MANIFEST = """
id = "{mod_id}"
name = "Checker"
version = "1.0.0"
api_version = 1
module = "main"
events = ["ai.before_send", "run.finished"]

[settings.banned]
type = "string"
label = {{ ja = "禁止語", en = "Banned word" }}
default = "secret-project"

[settings.token]
type = "secret"
label = "Token"
"""

_GOOD_MODULE = """
from gakei_mod import Decision, Verdict, VerdictTarget

from . import helper


async def on_ai_before_send(event, ctx):
    texts = " ".join(part.text for part in event.subject.text)
    if ctx.settings["banned"] in texts:
        return Decision.deny(helper.reason(ctx.settings["banned"]), reference="py-1")
    return Decision.allow()


async def on_run_finished(event, ctx):
    return [
        Verdict(
            target=VerdictTarget(type="run", id=event.subject.run_id),
            verdict="ok",
            label="checked",
        )
    ]
"""


def _write_mod(root: Path, folder: str, manifest: str, files: dict[str, str]) -> Path:
    directory = root / folder
    directory.mkdir(parents=True)
    (directory / loader.MANIFEST_NAME).write_text(manifest, encoding="utf-8")
    for name, content in files.items():
        (directory / name).write_text(textwrap.dedent(content), encoding="utf-8")
    return directory


def test_scan_reports_invalid_manifests_without_raising(tmp_path: Path) -> None:
    root = tmp_path / "mods"
    _write_mod(root, "good", _GOOD_MANIFEST.format(mod_id="good"), {"main.py": _GOOD_MODULE})
    _write_mod(root, "broken-toml", "id = ", {})
    _write_mod(
        root,
        "bad-version",
        _GOOD_MANIFEST.format(mod_id="bad-version").replace("api_version = 1", "api_version = 9"),
        {},
    )
    _write_mod(
        root,
        "bad-event",
        _GOOD_MANIFEST.format(mod_id="bad-event").replace("run.finished", "run.started"),
        {},
    )
    (root / "not-a-mod").mkdir()
    found = {m.id: m for m in loader.scan(root)}
    assert set(found) == {"good", "broken-toml", "bad-version", "bad-event"}
    assert found["good"].manifest is not None and found["good"].error is None
    manifest = found["good"].manifest
    assert manifest.events == ("ai.before_send", "run.finished")
    assert [(s.key, s.type, s.default) for s in manifest.settings] == [
        ("banned", "string", "secret-project"),
        ("token", "secret", None),
    ]
    for name in ("broken-toml", "bad-version", "bad-event"):
        assert found[name].manifest is None and found[name].error
    assert "api_version" in (found["bad-version"].error or "")
    # フォルダが無ければ空。
    assert loader.scan(tmp_path / "missing") == []


def _runtime(tmp_path: Path, db_session_factory, local_store) -> ModRuntime:  # noqa: ANN001
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        fake_provider=True,
        mods_dir=tmp_path / "mods",
    )
    runtime = ModRuntime(db_session_factory, local_store, settings)
    runtime.scan()
    return runtime


def _enable(runtime: ModRuntime, mod_id: str, **values: object) -> None:
    with runtime.session_factory() as db:
        mods_config.save_python_config(
            db,
            mods_config.PythonModConfig(
                mod_id=mod_id,
                common=mods_config.CommonConfig(enabled=True),
                values=dict(values),
            ),
        )
        runtime.reload(db)


def _subject(text: str) -> contract.BeforeSendSubject:
    return contract.BeforeSendSubject(
        purpose="image_run",
        target=contract.Target(
            provider="fake",
            model="m",
            host=None,
            locality="unset",
            locality_source="default",
        ),
        text=[contract.TextPart(role="prompt", text=text)],
    )


def test_enabled_python_mod_is_imported_and_called(
    tmp_path: Path,
    db_session_factory,
    local_store,  # noqa: ANN001
) -> None:
    _write_mod(
        tmp_path / "mods",
        "checker",
        _GOOD_MANIFEST.format(mod_id="checker"),
        {
            "main.py": _GOOD_MODULE,
            "helper.py": "def reason(word):\n    return f'contains {word}'\n",
        },
    )
    runtime = _runtime(tmp_path, db_session_factory, local_store)
    with db_session_factory() as db:
        runtime.reload(db)
    # 置いただけでは import しない。
    status = next(s for s in runtime.python_mods(db_session_factory()) if s.folder.id == "checker")
    assert (status.loaded, status.error) == (False, None)
    assert not runtime.wants(contract.AI_BEFORE_SEND)

    _enable(runtime, "checker")
    assert runtime.wants(contract.AI_BEFORE_SEND)
    assert asyncio.run(runtime.before_send(_subject("a cat"), actor=None)) is None
    denial = asyncio.run(runtime.before_send(_subject("about secret-project"), actor=None))
    assert denial is not None
    assert (denial.mod_kind, denial.mod_name) == ("python", "Checker")
    assert denial.reason == "contains secret-project"
    assert denial.reference == "py-1"

    # 設定の値を変えると、その値で呼ばれる。
    _enable(runtime, "checker", banned="dog")
    assert asyncio.run(runtime.before_send(_subject("about secret-project"), actor=None)) is None
    assert asyncio.run(runtime.before_send(_subject("a dog"), actor=None)) is not None


def test_import_failure_is_reported_as_load_error(
    tmp_path: Path,
    db_session_factory,
    local_store,  # noqa: ANN001
) -> None:
    root = tmp_path / "mods"
    _write_mod(
        root, "crash", _GOOD_MANIFEST.format(mod_id="crash"), {"main.py": "raise ValueError('x')"}
    )
    _write_mod(
        root,
        "sync-handler",
        _GOOD_MANIFEST.format(mod_id="sync-handler"),
        {"main.py": "def on_ai_before_send(event, ctx):\n    return None\n"},
    )
    runtime = _runtime(tmp_path, db_session_factory, local_store)
    _enable(runtime, "crash")
    _enable(runtime, "sync-handler")
    statuses = {s.folder.id: s for s in runtime.python_mods(db_session_factory())}
    assert statuses["crash"].loaded is False
    assert "ValueError" in (statuses["crash"].error or "")
    assert "async" in (statuses["sync-handler"].error or "")
    # 読み込めない Mod は購読しない(起動も止めない)。
    assert not runtime.wants(contract.AI_BEFORE_SEND)


def test_python_mod_exception_follows_on_failure(
    tmp_path: Path,
    db_session_factory,
    local_store,  # noqa: ANN001
) -> None:
    _write_mod(
        tmp_path / "mods",
        "raiser",
        _GOOD_MANIFEST.format(mod_id="raiser"),
        {
            "main.py": (
                "async def on_ai_before_send(event, ctx):\n"
                "    raise RuntimeError('boom')\n"
                "async def on_run_finished(event, ctx):\n"
                "    return None\n"
            )
        },
    )
    runtime = _runtime(tmp_path, db_session_factory, local_store)
    _enable(runtime, "raiser")
    denial = asyncio.run(runtime.before_send(_subject("x"), actor=None))
    assert denial is not None and denial.reason == "送る前のチェックに失敗しました"


def test_python_mod_api(client, data_dir: Path) -> None:  # noqa: ANN001
    _write_mod(
        data_dir / "mods",
        "checker",
        _GOOD_MANIFEST.format(mod_id="checker"),
        {
            "main.py": _GOOD_MODULE,
            "helper.py": "def reason(word):\n    return f'contains {word}'\n",
        },
    )
    _write_mod(data_dir / "mods", "broken", "id = ", {})
    client.app.state.mods.scan()
    body = client.get("/api/settings/mods").json()
    mods = {m["id"]: m for m in body["python_mods"]}
    assert mods["broken"]["error"] and mods["broken"]["name"] is None
    checker = mods["checker"]
    assert (checker["enabled"], checker["loaded"], checker["error"]) == (False, False, None)
    assert checker["available_events"] == ["ai.before_send", "run.finished"]
    assert [(s["key"], s["label"], s["value"]) for s in checker["settings"]] == [
        ("banned", "禁止語", "secret-project"),
        ("token", "Token", None),
    ]
    # 有効にする前は呼ばない(import もしない)。
    assert client.post("/api/settings/mods/python/checker/test", json={}).status_code == 409

    response = client.patch(
        "/api/settings/mods/python/checker",
        json={"enabled": True, "values": {"banned": "GAKEI"}, "secrets": {"token": "tok-123"}},
    )
    assert response.status_code == 200, response.text
    assert "tok-123" not in response.text
    checker = {m["id"]: m for m in response.json()["python_mods"]}["checker"]
    assert checker["loaded"] is True and checker["order"] is not None
    token = next(s for s in checker["settings"] if s["key"] == "token")
    assert token["secret_set"] is True and token["value"] is None

    result = client.post("/api/settings/mods/python/checker/test", json={})
    assert result.status_code == 200, result.text
    # ダミーのイベントの文に "GAKEI" が入っているので拒否する。
    assert result.json()["outcome"] == "deny"
    finished = client.post(
        "/api/settings/mods/python/checker/test", json={"event": "run.finished"}
    ).json()
    assert finished["outcome"] == "ok"
    assert finished["response"][0]["verdict"] == "ok"

    for bad in (
        {"values": {"unknown": 1}},
        {"values": {"banned": 3}},
        {"values": {"token": "x"}},
        {"secrets": {"banned": "x"}},
        {"events": ["asset.ingested"]},
    ):
        assert client.patch("/api/settings/mods/python/checker", json=bad).status_code == 422, bad


# -- 判定を返さない Mod(`returns_verdicts = false`) ------------------------------------------

_NO_VERDICTS_MANIFEST = _GOOD_MANIFEST.replace(
    'events = ["ai.before_send", "run.finished"]',
    'events = ["ai.before_send", "run.finished"]\nreturns_verdicts = false',
)


def test_manifest_returns_verdicts() -> None:
    base = {
        "id": "m",
        "name": "M",
        "version": "1",
        "api_version": 1,
        "module": "main",
        "events": ["run.finished"],
    }
    # 書いていなければ判定を返す(今までの動作のまま)。
    assert loader.parse_manifest(base).returns_verdicts is True
    assert loader.parse_manifest({**base, "returns_verdicts": False}).returns_verdicts is False
    with pytest.raises(loader.ModLoadError):
        loader.parse_manifest({**base, "returns_verdicts": "no"})


def test_mod_without_verdicts_cannot_wait_and_its_verdicts_are_dropped(
    client,  # noqa: ANN001
    data_dir: Path,
) -> None:
    _write_mod(
        data_dir / "mods",
        "writer",
        _NO_VERDICTS_MANIFEST.format(mod_id="writer"),
        {
            "main.py": _GOOD_MODULE,
            "helper.py": "def reason(word):\n    return f'contains {word}'\n",
        },
    )
    runtime: ModRuntime = client.app.state.mods
    runtime.scan()
    mod = {m["id"]: m for m in client.get("/api/settings/mods").json()["python_mods"]}["writer"]
    assert (mod["returns_verdicts"], mod["wait_for_verdict"]) == (False, False)

    # 判定を待つ設定にはできない(422)。false と、ほかの項目は変えられる。
    response = client.patch(
        "/api/settings/mods/python/writer", json={"enabled": True, "wait_for_verdict": True}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "この Mod は判定を返さないため、判定を待つ設定にできません"
    response = client.patch(
        "/api/settings/mods/python/writer", json={"enabled": True, "wait_for_verdict": False}
    )
    assert response.status_code == 200, response.text

    # 保存済みの true(マニフェストを後から変えたとき)は、実行時に無視して非同期にする。
    with runtime.session_factory() as db:
        current = mods_config.python_config_or_default(
            mods_config.load_python_configs(db), "writer"
        )
        mods_config.save_python_config(
            db,
            mods_config.PythonModConfig(
                mod_id="writer",
                common=mods_config.CommonConfig(
                    enabled=True, wait_for_verdict=True, order=current.common.order
                ),
            ),
        )
        runtime.reload(db)
    assert runtime.waiting_subscribers(contract.RUN_FINISHED) == []
    sub = runtime.find_subscriber("python", "writer")
    assert sub is not None and sub.wait_for_verdict is False and sub.returns_verdicts is False
    mod = {m["id"]: m for m in client.get("/api/settings/mods").json()["python_mods"]}["writer"]
    assert (mod["returns_verdicts"], mod["wait_for_verdict"]) == (False, False)

    # 返してきた判定は捨てる(テスト送信でも出さない)。
    finished = client.post(
        "/api/settings/mods/python/writer/test", json={"event": "run.finished"}
    ).json()
    assert (finished["outcome"], finished["response"]) == ("ok", [])
    # 後から送る判定も捨てる。
    submitted = asyncio.run(
        runtime._verdict_submitter(sub)(
            [
                contract.Verdict(
                    target=contract.VerdictTarget(
                        type="run", id="00000000-0000-0000-0000-000000000001"
                    ),
                    verdict="deny",
                )
            ]
        )
    )
    assert submitted == 0
