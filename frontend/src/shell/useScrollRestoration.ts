/**
 * スクロールコンテナ(window ではなく、シェル内の要素)に対する汎用のスクロール復元フック。
 * `location.key` ごとに scrollTop を保存し、戻る/進む(POP)で入ってきたときだけ復元する
 * (タブをタップして開いた PUSH では先頭から)。
 *
 * 復元はコンテンツの高さがまだ足りない(データ取得中)場合に備えて、数フレームにわたって
 * 再試行する。保存は scroll イベントを rAF で間引きしつつ、離脱時にも確実に行う。
 */
import { useLayoutEffect, useRef, type RefObject } from 'react'
import { useLocation, useNavigationType } from 'react-router'
import {
  getScrollPosition,
  saveScrollPosition,
  scrollPositionStore,
  scrollStoreKey,
  shouldRestoreScroll,
} from './scrollRestoration'

const MAX_RESTORE_ATTEMPTS = 30
const RESTORE_TOLERANCE_PX = 2

export function useScrollRestoration(
  containerRef: RefObject<HTMLElement | null>,
  namespace: string,
) {
  const location = useLocation()
  const navigationType = useNavigationType()
  const keyRef = useRef(location.key)

  useLayoutEffect(() => {
    keyRef.current = location.key
  }, [location.key])

  // 保存: scroll イベント(rAFで間引き)+ 離脱時(このeffectのクリーンアップ)。
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return

    let frame: number | null = null
    function handleScroll() {
      if (frame !== null) return
      frame = requestAnimationFrame(() => {
        frame = null
        const key = scrollStoreKey(namespace, keyRef.current)
        saveScrollPosition(scrollPositionStore, key, el!.scrollTop)
      })
    }

    el.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      el.removeEventListener('scroll', handleScroll)
      if (frame !== null) cancelAnimationFrame(frame)
      // 離脱(location.key の変化 or アンマウント)の直前の位置を確実に保存する。
      const key = scrollStoreKey(namespace, keyRef.current)
      saveScrollPosition(scrollPositionStore, key, el.scrollTop)
    }
    // location.key が変わるたびに保存し直す(新しいキーに向けて保存を始める)。
  }, [containerRef, namespace, location.key])

  // 復元: POP のときだけ。コンテンツが伸びるのを待つため、数フレーム試す。
  useLayoutEffect(() => {
    if (!shouldRestoreScroll(navigationType)) return
    const key = scrollStoreKey(namespace, location.key)
    const target = getScrollPosition(scrollPositionStore, key)
    if (target === undefined) return

    let cancelled = false
    let attempts = 0

    function tryRestore() {
      if (cancelled) return
      const el = containerRef.current
      if (!el) return
      el.scrollTop = target as number
      attempts += 1
      const reached = Math.abs(el.scrollTop - (target as number)) <= RESTORE_TOLERANCE_PX
      if (!reached && attempts < MAX_RESTORE_ATTEMPTS) {
        requestAnimationFrame(tryRestore)
      }
    }
    tryRestore()

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key, navigationType, namespace])
}
