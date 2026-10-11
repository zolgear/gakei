"""タグ辞書の取り込み(ADR-0041 1章)。プロセス内のスレッドで、行をまとめて INSERT する。

- 辞書の行(`tag_dictionary`)は API が `importing` で作り、ここで中身を入れて `ready` に、
  失敗すれば中身を消して `failed` にする。取り込み中の辞書は候補にも訳にも使わない(前の辞書の
  まま答える)。
- SQLite の書き込みの鍵を長く持たないよう、`BATCH_ROWS` 行ごとにコミットする。
- 止める(`stop`)ときは、次のまとまりの前で抜ける。`importing` のまま残った辞書は、次の起動で
  `recover_interrupted` が `failed` にする。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

from app.domain import tag_dictionaries as dictionaries
from app.domain.models import (
    TagDictionary,
    TagDictionaryAlias,
    TagDictionaryEntry,
    TagDictionaryTranslation,
)

logger = logging.getLogger(__name__)

BATCH_ROWS = 5000


class ImportStoppedError(RuntimeError):
    """プロセスの終了で取り込みを途中でやめた。"""


class TagDictionaryImporter:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self._threads: dict[uuid.UUID, threading.Thread] = {}
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    def submit(self, dictionary_id: uuid.UUID, source: dictionaries.DictionarySource) -> None:
        """取り込みをスレッドで始める。`source.text` はスレッドが終わるまで持つ。"""
        thread = threading.Thread(
            target=self._run,
            args=(dictionary_id, source),
            name=f"tag-dictionary-import-{dictionary_id}",
            daemon=True,
        )
        with self._lock:
            self._threads[dictionary_id] = thread
        thread.start()

    def is_importing(self, dictionary_id: uuid.UUID) -> bool:
        with self._lock:
            thread = self._threads.get(dictionary_id)
        return thread is not None and thread.is_alive()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """すべての取り込みが終わるまで待つ(テスト用)。終われば True。"""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                threads = list(self._threads.values())
            alive = [t for t in threads if t.is_alive()]
            if not alive:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            alive[0].join(timeout=remaining)

    def stop(self, timeout: float = 10.0) -> None:
        self._stopping.set()
        self.wait_idle(timeout)

    # -- スレッドの中 -------------------------------------------------------------

    def _run(self, dictionary_id: uuid.UUID, source: dictionaries.DictionarySource) -> None:
        started = time.monotonic()
        try:
            with self.session_factory() as session:
                if source.kind == dictionaries.KIND_TAGS:
                    count = self._import_tags(session, dictionary_id, source.text)
                else:
                    count = self._import_translations(session, dictionary_id, source.text)
                row = session.get(TagDictionary, dictionary_id)
                if row is None:
                    return
                if count == 0:
                    row.status = dictionaries.STATUS_FAILED
                    row.error = dictionaries.ERROR_NO_ROWS
                else:
                    row.status = dictionaries.STATUS_READY
                    row.error = None
                row.row_count = count
                row.finished_at = datetime.now(UTC)
                session.commit()
            logger.info(
                "tag dictionary %s imported: %d rows in %.1fs",
                dictionary_id,
                count,
                time.monotonic() - started,
            )
        except ImportStoppedError:
            # importing のまま残す(次の起動で failed にする)。
            return
        except Exception:  # noqa: BLE001 - 取り込みの失敗は辞書の状態に残す
            logger.exception("tag dictionary %s import failed", dictionary_id)
            self._mark_failed(dictionary_id)

    def _mark_failed(self, dictionary_id: uuid.UUID) -> None:
        try:
            with self.session_factory() as session:
                dictionaries.delete_contents(session, dictionary_id)
                row = session.get(TagDictionary, dictionary_id)
                if row is not None:
                    row.status = dictionaries.STATUS_FAILED
                    row.error = dictionaries.ERROR_IMPORT_FAILED
                    row.row_count = 0
                    row.finished_at = datetime.now(UTC)
                session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("tag dictionary %s: could not record the failure", dictionary_id)

    def _flush(self, session: Session, table, rows: list[dict]) -> None:  # noqa: ANN001
        if self._stopping.is_set():
            raise ImportStoppedError
        if rows:
            session.execute(insert(table), rows)
            session.commit()
            rows.clear()

    def _import_tags(self, session: Session, dictionary_id: uuid.UUID, text: str) -> int:
        entries: list[dict] = []
        aliases: list[dict] = []
        seen: set[str] = set()
        seen_alias: set[tuple[str, str]] = set()
        count = 0
        for row in dictionaries.iter_tag_rows(text):
            key = dictionaries.make_key(row.name)
            # 同じタグ(正規化して同じ)が重なれば先の行を使う。
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "dictionary_id": dictionary_id,
                    "name_key": key,
                    "name": row.name,
                    "category": row.category,
                    "post_count": row.post_count,
                }
            )
            count += 1
            for alias in row.aliases:
                alias_key = dictionaries.make_key(alias)
                if not alias_key or alias_key == key or (alias_key, key) in seen_alias:
                    continue
                seen_alias.add((alias_key, key))
                aliases.append(
                    {
                        "dictionary_id": dictionary_id,
                        "alias_key": alias_key,
                        "name_key": key,
                        "alias": alias,
                    }
                )
            if len(entries) >= BATCH_ROWS:
                self._flush(session, TagDictionaryEntry, entries)
            if len(aliases) >= BATCH_ROWS:
                self._flush(session, TagDictionaryAlias, aliases)
        self._flush(session, TagDictionaryEntry, entries)
        self._flush(session, TagDictionaryAlias, aliases)
        return count

    def _import_translations(self, session: Session, dictionary_id: uuid.UUID, text: str) -> int:
        rows: list[dict] = []
        seen: set[str] = set()
        count = 0
        for row in dictionaries.iter_translation_rows(text):
            key = dictionaries.make_key(row.name)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "dictionary_id": dictionary_id,
                    "name_key": key,
                    "name": row.name,
                    "translation": row.translation,
                    "translations": row.translations,
                    "search_key": dictionaries.translation_search_key(row.terms),
                }
            )
            count += 1
            if len(rows) >= BATCH_ROWS:
                self._flush(session, TagDictionaryTranslation, rows)
        self._flush(session, TagDictionaryTranslation, rows)
        return count
