/**
 * 削除確認ダイアログの警告文。この実行の出力が他の実行の入力(親)として使われている場合、
 * 削除すると系列上でその画像が「削除済み」になることを伝える(HistoryCard から使う)。
 */
import { fmt, msg } from '../../i18n'

export function buildRunDeleteWarning(count: number): string | undefined {
  if (count <= 0) return undefined
  return fmt(msg().history.card.deleteWarning, { count })
}
