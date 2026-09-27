/**
 * 生成フォームの「グループ」(ADR-0022 4章「生成時の指定」)の純粋関数。
 * 選んだグループの存在確認と、`POST /api/runs` の body への `asset_group_id` の付け足しを持つ。
 */

/** `<select>` で「新しいグループ…」を表す特別な値(グループ id は UUID なので衝突しない)。 */
export const NEW_ASSET_GROUP_OPTION = '__new_asset_group__'

/**
 * フォームが覚えているグループ id を、今のグループ一覧に照らして解決する。
 * - null ならそのまま null(グループなし)。
 * - 一覧がまだ無い(読み込み中・取得失敗)なら確かめようがないので、覚えている id をそのまま
 *   返す(送ればサーバーが存在を確かめ、無ければ 404 で知らせる)。
 * - 一覧にあればその id、無ければ(保存後や「同じ設定で」の後に削除された)null。
 */
export function resolveAssetGroupId(
  storedId: string | null,
  groups: ReadonlyArray<{ id: string }> | undefined,
): string | null {
  if (storedId === null) return null
  if (groups === undefined) return storedId
  return groups.some((g) => g.id === storedId) ? storedId : null
}

/**
 * Run 作成の body にグループを付け足す。null(グループなし)のときはキーごと付けない
 * (未指定と同じ扱いにし、グループ機能を知らない送信と同じ body にする)。
 */
export function withAssetGroupId<T extends object>(
  body: T,
  assetGroupId: string | null,
): T & { asset_group_id?: string } {
  return assetGroupId === null ? body : { ...body, asset_group_id: assetGroupId }
}

/**
 * 作成したグループを一覧のキャッシュの先頭に差し込む(一覧は `updated_at` の新しい順なので、
 * 作ったばかりのものは先頭に来る)。再取得を待たずにフォームの選択を解決できるようにするため。
 * 同じ id が既にあれば何もしない。
 */
export function prependAssetGroup<G extends { id: string }>(items: ReadonlyArray<G>, group: G): G[] {
  if (items.some((g) => g.id === group.id)) return [...items]
  return [group, ...items]
}
