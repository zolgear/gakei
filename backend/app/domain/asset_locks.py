"""ロック(ADR-0060)。納品・共有・提出した画像に付け、その画像と祖先を消せないようにする。

- 守る範囲(ロックした画像と祖先)は、付けたときに `asset_lock_scope` に書く。来歴は追記のみなので
  (ADR-0003)、ある Asset の祖先は後から変わらない。読み出しのたびに系列をたどらない。
- 祖先の辺は、Asset を作った Run の入力(マスクを含む全部)、スケッチと領域の下地
  (`source_asset_id`)、配置の素材(`asset_composition_source`)。見える範囲(ADR-0025)に
  関わらずたどる(守る範囲は見る人によらない)。
- 外すときは行を消さず、外した人と時刻を書く。外したロックの `asset_lock_scope` の行は残るが、
  守る判定では外していないロックだけを見る。
- 外せるのは、付けた人と管理者(ADR-0060 2章)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, Select, and_, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.models import (
    LOCK_KINDS,
    AppUser,
    Asset,
    AssetCompositionSource,
    AssetLock,
    AssetLockScope,
    Run,
    RunInput,
)

NOTE_MAX_LENGTH = 2000


class AlreadyLockedError(Exception):
    """外していないロックが既にある。"""


class NotLockedError(Exception):
    """外していないロックが無い。"""


class UnlockNotAllowedError(Exception):
    """付けた人でも管理者でもない。"""


@dataclass(frozen=True)
class LockState:
    """一覧に載せるロックの状態。`locked` はその画像に付いている、`protected` はその画像か、
    その画像を元にした画像にロックが付いていて、消せない。"""

    locked: bool
    protected: bool


def _utcnow() -> datetime:
    return datetime.now(UTC)


def ancestor_asset_ids(db: Session, asset_id: uuid.UUID) -> set[uuid.UUID]:
    """`asset_id` とその祖先の Asset の id(1階層ずつ SELECT する。系列グラフと同じ方針)。"""
    found: set[uuid.UUID] = {asset_id}
    frontier: set[uuid.UUID] = {asset_id}
    while frontier:
        rows = db.execute(
            select(Asset.produced_by_run_id, Asset.source_asset_id).where(Asset.id.in_(frontier))
        ).all()
        parents: set[uuid.UUID] = set()
        run_ids = {run_id for run_id, _ in rows if run_id is not None}
        parents.update(source for _, source in rows if source is not None)
        if run_ids:
            parents.update(
                db.execute(select(RunInput.asset_id).where(RunInput.run_id.in_(run_ids)))
                .scalars()
                .all()
            )
        parents.update(
            db.execute(
                select(AssetCompositionSource.source_asset_id).where(
                    AssetCompositionSource.asset_id.in_(frontier)
                )
            )
            .scalars()
            .all()
        )
        frontier = parents - found
        found |= frontier
    return found


def protected_asset_ids() -> Select[tuple]:
    """外していないロックが守る Asset の id の副問い合わせ。"""
    return (
        select(AssetLockScope.asset_id)
        .join(AssetLock, AssetLock.id == AssetLockScope.lock_id)
        .where(AssetLock.unlocked_at.is_(None))
    )


def locked_asset_ids() -> Select[tuple]:
    """外していないロックが付いている Asset の id の副問い合わせ(絞り込み用)。"""
    return select(AssetLock.asset_id).where(AssetLock.unlocked_at.is_(None))


def run_has_locked_output() -> ColumnElement[bool]:
    """Run が、ロックの付いた出力(削除されていない)を1つ以上持つ条件(履歴の絞り込み)。"""
    runs = select(Asset.produced_by_run_id).where(
        Asset.id.in_(locked_asset_ids()),
        Asset.produced_by_run_id.is_not(None),
        Asset.deleted_at.is_(None),
    )
    return Run.id.in_(runs)


def is_protected(db: Session, asset_id: uuid.UUID) -> bool:
    return bool(
        db.execute(
            select(
                exists()
                .where(AssetLockScope.asset_id == asset_id)
                .where(
                    AssetLockScope.lock_id.in_(
                        select(AssetLock.id).where(AssetLock.unlocked_at.is_(None))
                    )
                )
            )
        ).scalar()
    )


def run_is_protected(db: Session, run_id: uuid.UUID) -> bool:
    """Run の出力のどれかが守られているか(Run を消すと出力も消えるため)。"""
    return bool(
        db.execute(
            select(
                exists().where(
                    and_(
                        Asset.produced_by_run_id == run_id,
                        Asset.id.in_(protected_asset_ids()),
                    )
                )
            )
        ).scalar()
    )


def bulk_lock_states(db: Session, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, LockState]:
    """`asset_ids` のうち、ロックが付いているか守られているもの(1回ずつの問い合わせ)。"""
    if not asset_ids:
        return {}
    locked = set(
        db.execute(
            select(AssetLock.asset_id).where(
                AssetLock.asset_id.in_(asset_ids), AssetLock.unlocked_at.is_(None)
            )
        )
        .scalars()
        .all()
    )
    protected = set(
        db.execute(
            select(AssetLockScope.asset_id)
            .join(AssetLock, AssetLock.id == AssetLockScope.lock_id)
            .where(AssetLockScope.asset_id.in_(asset_ids), AssetLock.unlocked_at.is_(None))
        )
        .scalars()
        .all()
    )
    return {
        asset_id: LockState(locked=asset_id in locked, protected=True)
        for asset_id in protected | locked
    }


def active_lock(db: Session, asset_id: uuid.UUID) -> AssetLock | None:
    return db.execute(
        select(AssetLock).where(AssetLock.asset_id == asset_id, AssetLock.unlocked_at.is_(None))
    ).scalar_one_or_none()


def locker_name(db: Session, lock: AssetLock) -> str | None:
    if lock.locked_by_user_id is None:
        return None
    user = db.get(AppUser, lock.locked_by_user_id)
    if user is None:
        return None
    return user.name or user.email


def can_unlock(viewer: CurrentUser, lock: AssetLock) -> bool:
    if viewer.is_admin:
        return True
    return viewer.id is not None and viewer.id == lock.locked_by_user_id


def normalize_note(note: str | None) -> str | None:
    if note is None:
        return None
    note = note.strip()
    return note[:NOTE_MAX_LENGTH] or None


def lock_asset(
    db: Session, viewer: CurrentUser, asset: Asset, kind: str, note: str | None
) -> AssetLock:
    """ロックを付け、守る範囲を書く。見えるか・削除済みでないかは呼ぶ側が確かめる。"""
    if kind not in LOCK_KINDS:
        raise ValueError(kind)
    if active_lock(db, asset.id) is not None:
        raise AlreadyLockedError
    lock = AssetLock(
        asset_id=asset.id,
        kind=kind,
        note=normalize_note(note),
        locked_by_user_id=viewer.id,
        locked_at=_utcnow(),
    )
    db.add(lock)
    try:
        db.flush()
    except IntegrityError as exc:
        # 同時に付けた別の要求が先に入れた(部分一意索引)。
        db.rollback()
        raise AlreadyLockedError from exc
    db.add_all(
        AssetLockScope(lock_id=lock.id, asset_id=ancestor)
        for ancestor in ancestor_asset_ids(db, asset.id)
    )
    db.commit()
    return lock


def unlock_asset(db: Session, viewer: CurrentUser, asset: Asset) -> AssetLock:
    lock = active_lock(db, asset.id)
    if lock is None:
        raise NotLockedError
    if not can_unlock(viewer, lock):
        raise UnlockNotAllowedError
    lock.unlocked_by_user_id = viewer.id
    lock.unlocked_at = _utcnow()
    db.commit()
    return lock
