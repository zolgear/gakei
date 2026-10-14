/**
 * 「マスクに戻す」の操作(ADR-0049 5章。2026-10-15)。サムネイル(`AssetThumb`)の隅のボタンが使う。
 * 見た目を持たない処理だけをここに置く(画面のテストの仕組みが無いので、ここを単体で確かめる)。
 */
import { hideAsset } from './revealStore'

interface StoppableEvent {
  preventDefault(): void
  stopPropagation(): void
}

/**
 * その画像をマスクに戻す。下のカードのリンクやボタンは動かさない(`preventDefault` と
 * `stopPropagation`。マスクをクリックして外すときと同じ)。
 */
export function remaskFromEvent(e: StoppableEvent, assetId: string): void {
  e.preventDefault()
  e.stopPropagation()
  hideAsset(assetId)
}

/** キーボードで押したとみなすキー(`role="button"` の要素で Enter と Space を受ける)。 */
export function isActivationKey(key: string): boolean {
  return key === 'Enter' || key === ' '
}
