/**
 * ストックのタグの絞り込み(ADR-0024 5章)。ストックパネルの外(ビューアのタグのチップ)からも
 * 「このタグで絞り込む」を指示できるよう、パネルのローカル state ではなく小さな外部ストアに置く
 * (`useSyncExternalStore`)。ページを開き直すと解除される(localStorage には保存しない)。
 */
import { useSyncExternalStore } from 'react'

let current: string | null = null
const listeners = new Set<() => void>()

export function getStockTagFilter(): string | null {
  return current
}

/** 空文字は解除(null)として扱う。 */
export function setStockTagFilter(tag: string | null): void {
  const next = tag ? tag : null
  if (next === current) return
  current = next
  for (const listener of listeners) listener()
}

export function subscribeStockTagFilter(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useStockTagFilter(): string | null {
  return useSyncExternalStore(subscribeStockTagFilter, getStockTagFilter, getStockTagFilter)
}
