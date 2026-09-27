/**
 * Asset の「復元」ボタンを出すかどうかの判定。純粋関数(副作用なし)。
 * 削除されていて(`deleted_at` あり)、かつサーバーが `restorable`(生んだ Run が削除されて
 * いない)と言っているときだけ復元できる。
 */
export function shouldShowRestoreButton(
  deletedAt: string | null | undefined,
  restorable: boolean | undefined,
): boolean {
  return Boolean(deletedAt) && restorable === true
}

/**
 * 削除されているが復元できない(`restorable` が false)ときに、その理由を小さく示すための
 * 判定。`shouldShowRestoreButton` が false でも、削除されてすらいなければ理由表示も不要。
 */
export function shouldShowNotRestorableNote(
  deletedAt: string | null | undefined,
  restorable: boolean | undefined,
): boolean {
  return Boolean(deletedAt) && restorable !== true
}
