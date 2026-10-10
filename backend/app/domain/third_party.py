"""同梱する第三者ライブラリのライセンス表記(ADR-0021 4章、ADR-0011 Action Item 4)。

Python 側は起動している環境の配布物(`importlib.metadata`)から集める。フロント側は
`npm run build` の最後に `frontend/scripts/third-party-notices.mjs` が作った
`frontend/dist/third-party-notices.txt` をそのまま末尾に連結する(フロントのバンドルには
ライブラリのライセンス文書が入らないため、生成は2段に分ける)。

この生成物(名前・ライセンス種別・URL・本文)は翻訳対象の UI 文言ではない
(ADR-0015 の対象外)。すべて英語で書く。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import Distribution, distributions
from pathlib import Path

# `[tool.uv] package = false` なので通常は distributions() に出てこないが、念のため
# 名前で弾く(pyproject.toml の name と合わせる)。
_SELF_DISTRIBUTION_NAME = "gakei-backend"

_LICENSE_FILE_PREFIXES = ("license", "licence", "copying", "notice")

# `License` メタデータがライセンス文の全文をまるごと持っている(改行を含む、または
# 極端に長い)場合は、1行の名称として使えないので無視する。
_MAX_INLINE_LICENSE_LENGTH = 100


@dataclass(frozen=True)
class ThirdPartyEntry:
    """1つの配布物のライセンス表記。"""

    name: str
    version: str
    license: str
    url: str | None
    texts: list[str] = field(default_factory=list)


def _license_from_classifiers(dist: Distribution) -> str | None:
    """`Classifier: License :: ...` の末尾の要素(例: "MIT License")を返す。"""
    for classifier in dist.metadata.get_all("Classifier") or []:
        if not classifier.startswith("License :: "):
            continue
        return classifier.rsplit(" :: ", 1)[-1].strip()
    return None


def _license_of(dist: Distribution) -> str:
    """優先順: `License-Expression` → `License`(全文が入っていそうなものは無視) →
    `Classifier: License ::` → `"UNKNOWN"`(この生成物は英語のみ)。
    """
    expression = dist.metadata.get("License-Expression")
    if expression and expression.strip():
        return expression.strip()

    raw_license = dist.metadata.get("License")
    if (
        raw_license
        and "\n" not in raw_license
        and len(raw_license) <= _MAX_INLINE_LICENSE_LENGTH
        and raw_license.strip()
    ):
        return raw_license.strip()

    from_classifier = _license_from_classifiers(dist)
    if from_classifier:
        return from_classifier

    return "UNKNOWN"


def _url_of(dist: Distribution) -> str | None:
    """優先順: `Home-page` → `Project-URL` の最初のもの。"""
    home_page = dist.metadata.get("Home-page")
    if home_page and home_page.strip():
        return home_page.strip()

    for project_url in dist.metadata.get_all("Project-URL") or []:
        # PEP 621 の形式は "ラベル, URL"。ラベルが無く URL だけのこともある。
        _label, _sep, url = project_url.partition(",")
        candidate = (url or project_url).strip()
        if candidate:
            return candidate
    return None


def _license_file_texts(dist: Distribution) -> list[str]:
    """`License-File` メタデータで列挙されたファイルを読む。無ければ `dist.files` から
    `LICENSE*` / `LICENCE*` / `COPYING*` / `NOTICE*`(大文字小文字を無視)を探す。
    """
    license_files = dist.metadata.get_all("License-File") or []
    texts: list[str] = []
    if license_files:
        for name in license_files:
            try:
                text = dist.read_text(name)
            except OSError:
                continue
            if text:
                texts.append(text)
        return texts

    for file in dist.files or []:
        if not file.name.lower().startswith(_LICENSE_FILE_PREFIXES):
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if text:
            texts.append(text)
    return texts


def python_notices() -> list[ThirdPartyEntry]:
    """起動している Python 環境の配布物一覧(名前順、大文字小文字を無視)。"""
    entries: dict[str, ThirdPartyEntry] = {}
    for dist in distributions():
        name = dist.metadata.get("Name")
        if not name or name == _SELF_DISTRIBUTION_NAME or name in entries:
            continue
        entries[name] = ThirdPartyEntry(
            name=name,
            version=dist.version,
            license=_license_of(dist),
            url=_url_of(dist),
            texts=_license_file_texts(dist),
        )
    return [entries[name] for name in sorted(entries, key=str.lower)]


def bundled_data_notices() -> list[ThirdPartyEntry]:
    """Python の配布物ではないが、GAKEI に同梱しているデータ(ADR-0043 の顔検出のカスケード)。"""
    from app.focal import detect

    try:
        text = detect.CASCADE_LICENSE_PATH.read_text(encoding="utf-8")
    except OSError:
        text = ""
    return [
        ThirdPartyEntry(
            name="lbpcascade_animeface",
            version=detect.CASCADE_COMMIT[:12],
            license="MIT",
            url=detect.CASCADE_SOURCE_URL,
            texts=[text] if text else [],
        )
    ]


def _render_entry(entry: ThirdPartyEntry) -> str:
    header = f"{entry.name} {entry.version} — {entry.license} — {entry.url or 'n/a'}"
    if not entry.texts:
        return header
    return header + "\n\n" + "\n\n".join(entry.texts)


def render_notices(frontend_dist: Path) -> str:
    """GAKEI 全体(Python + frontend)の第三者ライセンス表記を1つのテキストにまとめる。

    生成日時は入れない(同じ依存構成なら常に同じ出力になる、再現性のため)。
    """
    sections = ["GAKEI third-party notices", "", "Python packages", ""]
    entries = python_notices()
    sections.append("\n----\n".join(_render_entry(entry) for entry in entries))
    sections.append("")
    sections.append("Bundled data files")
    sections.append("")
    sections.append("\n----\n".join(_render_entry(entry) for entry in bundled_data_notices()))
    sections.append("")
    sections.append("Frontend packages")
    sections.append("")

    frontend_notices_path = frontend_dist / "third-party-notices.txt"
    if frontend_notices_path.is_file():
        sections.append(frontend_notices_path.read_text(encoding="utf-8").rstrip("\n"))
    else:
        sections.append(
            "(frontend/dist/third-party-notices.txt was not found; "
            "run `npm run build` in frontend/)"
        )

    return "\n".join(sections) + "\n"
