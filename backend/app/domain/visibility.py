"""可視性(誰に何を見せるか)の判定を1か所にまとめる(ADR-0025)。

- 個人モード(`AUTH_MODE=none`)は `LOCAL_ADMIN`(id を持たない管理者)として動き、全件が見える。
- 認証モード(`AUTH_MODE=oidc`)は本人が作ったものだけが見える。管理者も同じ。ただし所有者が
  記録されていない(`created_by_user_id` が NULL。個人モードの頃のデータ)ものは管理者だけが見える。

所有者:
- Run: `run.created_by_user_id`
- 生成された Asset(`produced_by_run_id` あり): それを作った Run の `created_by_user_id`
  (`asset.created_by_user_id` ではなく Run の側を正とする)
- アップロード・マスク・スケッチの Asset: `asset.created_by_user_id`
- グループ: `asset_group.created_by_user_id`
- プロンプトセット: `prompt_set.created_by_user_id`
- パラメーターセット(ADR-0040): `parameter_set.created_by_user_id`
- 共有リンク(ADR-0029): `share.created_by_user_id`(一覧・取り消しは本人のものだけ)

一覧・検索は `*_visible(user)` が返す SQLAlchemy の条件を `where()` に足し、詳細・操作は
`get_visible_*()`(見えなければ None。呼び出し側は「存在しない」と同じ 404 にする)を使う。
取り出し済みの行を判定するときは `VisibilityChecker`(系列グラフなどで Run の所有者を
キャッシュする)を使う。

ログイン不要の共有リンク(ADR-0029)で外に見せる範囲は、ここではなく `app/domain/shares.py`
の `resolve_public_share` だけで判定する(可視性の判定とは別の関数にする。ADR-0029 6章)。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, and_, false, or_, select, true
from sqlalchemy.orm import Session, aliased

from app.auth.identity import CurrentUser
from app.domain.models import Asset, AssetGroup, ParameterSet, PromptSet, Run, Share


def sees_everything(user: CurrentUser) -> bool:
    """個人モードの `LOCAL_ADMIN`(id を持たない管理者)だけが全件を見る。

    認証モードの利用者は必ず `app_user.id` を持つ。id が無いのに管理者でない、という
    ありえない組み合わせは何も見えない側に倒す(`_owner_condition` と `owner_matches`)。
    """
    return user.id is None and user.is_admin


def _owner_condition(column: Any, user: CurrentUser) -> ColumnElement[bool]:
    """所有者の列 `column` が `user` に見える条件(`sees_everything` は呼び出し側で済ませる)。"""
    if user.id is None:
        # 認証モードで id の無い利用者は想定しない。何も見せない。
        return false()
    if user.is_admin:
        return or_(column == user.id, column.is_(None))
    return column == user.id


def owner_matches(user: CurrentUser, owner_id: uuid.UUID | None) -> bool:
    """所有者 `owner_id` のものが `user` に見えるか(取り出し済みの行の判定)。"""
    if sees_everything(user):
        return True
    if user.id is None:
        return False
    if owner_id is None:
        return user.is_admin
    return owner_id == user.id


# -- 一覧・検索用の条件 ------------------------------------------------------


def run_visible(user: CurrentUser) -> ColumnElement[bool]:
    if sees_everything(user):
        return true()
    return _owner_condition(Run.created_by_user_id, user)


def asset_visible(user: CurrentUser) -> ColumnElement[bool]:
    """Asset が見える条件。生成された Asset は、それを作った Run の実行者で判定する。

    Run 側はエイリアスを使ったサブクエリにする(外側のクエリが Run と結合していても、
    相関して意味が変わらないように)。
    """
    if sees_everything(user):
        return true()
    owner_run = aliased(Run)
    visible_run_ids = (
        select(owner_run.id)
        .where(_owner_condition(owner_run.created_by_user_id, user))
        .correlate(None)
    )
    return or_(
        and_(
            Asset.produced_by_run_id.is_(None),
            _owner_condition(Asset.created_by_user_id, user),
        ),
        and_(
            Asset.produced_by_run_id.is_not(None),
            Asset.produced_by_run_id.in_(visible_run_ids),
        ),
    )


def group_visible(user: CurrentUser) -> ColumnElement[bool]:
    if sees_everything(user):
        return true()
    return _owner_condition(AssetGroup.created_by_user_id, user)


def prompt_set_visible(user: CurrentUser) -> ColumnElement[bool]:
    if sees_everything(user):
        return true()
    return _owner_condition(PromptSet.created_by_user_id, user)


def parameter_set_visible(user: CurrentUser) -> ColumnElement[bool]:
    if sees_everything(user):
        return true()
    return _owner_condition(ParameterSet.created_by_user_id, user)


def share_visible(user: CurrentUser) -> ColumnElement[bool]:
    """自分が作った共有リンク(ADR-0029 7章)。管理者も他人のものは見えない(ADR-0025 5章)。"""
    if sees_everything(user):
        return true()
    return _owner_condition(Share.created_by_user_id, user)


# -- 取り出し済みの行の判定 --------------------------------------------------


def can_see_run(user: CurrentUser, run: Run) -> bool:
    return owner_matches(user, run.created_by_user_id)


def can_see_asset(db: Session, user: CurrentUser, asset: Asset) -> bool:
    if sees_everything(user):
        return True
    if asset.produced_by_run_id is not None:
        run = db.get(Run, asset.produced_by_run_id)
        if run is None:
            return False
        return can_see_run(user, run)
    return owner_matches(user, asset.created_by_user_id)


def can_see_group(user: CurrentUser, group: AssetGroup) -> bool:
    return owner_matches(user, group.created_by_user_id)


def can_see_prompt_set(user: CurrentUser, prompt_set: PromptSet) -> bool:
    return owner_matches(user, prompt_set.created_by_user_id)


def can_see_parameter_set(user: CurrentUser, parameter_set: ParameterSet) -> bool:
    return owner_matches(user, parameter_set.created_by_user_id)


def can_see_share(user: CurrentUser, share: Share) -> bool:
    return owner_matches(user, share.created_by_user_id)


def get_visible_run(db: Session, user: CurrentUser, run_id: uuid.UUID) -> Run | None:
    """見える Run(削除済みも含む)。存在しない・見えないときは None。"""
    run = db.get(Run, run_id)
    if run is None or not can_see_run(user, run):
        return None
    return run


def get_visible_asset(db: Session, user: CurrentUser, asset_id: uuid.UUID) -> Asset | None:
    """見える Asset(削除済みも含む)。存在しない・見えないときは None。"""
    asset = db.get(Asset, asset_id)
    if asset is None or not can_see_asset(db, user, asset):
        return None
    return asset


def get_visible_group(db: Session, user: CurrentUser, group_id: uuid.UUID) -> AssetGroup | None:
    """見える、削除されていないグループ。存在しない・削除済み・見えないときは None。"""
    group = db.get(AssetGroup, group_id)
    if group is None or group.deleted_at is not None or not can_see_group(user, group):
        return None
    return group


def get_visible_prompt_set(
    db: Session, user: CurrentUser, prompt_set_id: uuid.UUID
) -> PromptSet | None:
    """見える、削除されていないプロンプトセット。それ以外は None。"""
    prompt_set = db.get(PromptSet, prompt_set_id)
    if (
        prompt_set is None
        or prompt_set.deleted_at is not None
        or not can_see_prompt_set(user, prompt_set)
    ):
        return None
    return prompt_set


def visible_asset_ids(db: Session, user: CurrentUser, asset_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """`asset_ids` のうち見えるもの(削除済みも含む)の集合を1回のクエリで求める。"""
    if not asset_ids:
        return set()
    return set(
        db.execute(select(Asset.id).where(Asset.id.in_(asset_ids), asset_visible(user)))
        .scalars()
        .all()
    )


class VisibilityChecker:
    """取り出し済みの Asset / Run を何度も判定する箇所(系列グラフ、埋め込みメタ情報の組み立て)
    向け。Run の所有者を id ごとにキャッシュする。
    """

    def __init__(self, db: Session, user: CurrentUser) -> None:
        self._db = db
        self._user = user
        self._all = sees_everything(user)
        self._run_owner: dict[uuid.UUID, tuple[bool, uuid.UUID | None]] = {}

    @property
    def user(self) -> CurrentUser:
        return self._user

    def run(self, run: Run) -> bool:
        if self._all:
            return True
        return can_see_run(self._user, run)

    def _run_id_visible(self, run_id: uuid.UUID) -> bool:
        cached = self._run_owner.get(run_id)
        if cached is None:
            owner = self._db.execute(
                select(Run.id, Run.created_by_user_id).where(Run.id == run_id)
            ).first()
            cached = (False, None) if owner is None else (True, owner[1])
            self._run_owner[run_id] = cached
        exists, owner_id = cached
        return exists and owner_matches(self._user, owner_id)

    def asset(self, asset: Asset) -> bool:
        if self._all:
            return True
        if asset.produced_by_run_id is not None:
            return self._run_id_visible(asset.produced_by_run_id)
        return owner_matches(self._user, asset.created_by_user_id)

    def asset_id(self, asset_id: uuid.UUID) -> bool:
        if self._all:
            return True
        asset = self._db.get(Asset, asset_id)
        return asset is not None and self.asset(asset)


def get_visible_parameter_set(
    db: Session, user: CurrentUser, parameter_set_id: uuid.UUID
) -> ParameterSet | None:
    """見える、削除されていないパラメーターセット(ADR-0040)。それ以外は None。"""
    parameter_set = db.get(ParameterSet, parameter_set_id)
    if (
        parameter_set is None
        or parameter_set.deleted_at is not None
        or not can_see_parameter_set(user, parameter_set)
    ):
        return None
    return parameter_set
