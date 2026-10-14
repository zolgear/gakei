/**
 * マスクを一時的に外した画像(ADR-0049 5章「一時的に見る」)。Asset の id の集合をメモリにだけ置き、
 * サーバーにもブラウザ(localStorage)にも覚えない。ページを読み直すと戻る。
 * 同じ画像は、外した場所に関わらず(履歴で外せばビューアでも)外れたままにする。
 */
import { useCallback, useSyncExternalStore } from 'react'

let revealed: ReadonlySet<string> = new Set()
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

export function isRevealed(assetId: string): boolean {
  return revealed.has(assetId)
}

export function revealAsset(assetId: string): void {
  if (revealed.has(assetId)) return
  const next = new Set(revealed)
  next.add(assetId)
  revealed = next
  emit()
}

/** テスト用。すべてのマスクを戻す。 */
export function resetRevealed(): void {
  if (revealed.size === 0) return
  revealed = new Set()
  emit()
}

export function subscribeRevealed(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** この画像のマスクを外したか。id が無ければ false。 */
export function useRevealed(assetId: string | null | undefined): boolean {
  const getSnapshot = useCallback(() => (assetId ? revealed.has(assetId) : false), [assetId])
  return useSyncExternalStore(subscribeRevealed, getSnapshot, getSnapshot)
}
