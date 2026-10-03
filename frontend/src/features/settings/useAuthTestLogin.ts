/**
 * 認証のテストログイン(ADR-0034 2章の3)をポップアップで行うフック。
 * - `start()` はクリックの中で呼ぶ(`window.open` がブロックされないように)。開けなければ `blocked`。
 * - 結果はポップアップの `postMessage`(`parseAuthTestMessage` が受理したものだけ)で受け取る。
 * - ポップアップが閉じたら(`popup.closed` を一定間隔で見る)、結果が届かなくても必ず `onSettled` を呼ぶ
 *   (`PUBLIC_BASE_URL` のオリジンが違うと `postMessage` が届かないため、状態を読み直す保険)。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { parseAuthTestMessage, type AuthTestError } from './authSettings'

export type AuthTestState =
  | { status: 'idle' }
  | { status: 'waiting' }
  | { status: 'blocked' }
  | { status: 'failed'; error: AuthTestError | 'unknown' }

const POPUP_NAME = 'gakei-auth-test'
const POPUP_FEATURES = 'popup,width=520,height=720'
const CLOSED_CHECK_INTERVAL_MS = 500

interface Options {
  /** 成功の知らせが届いたとき。 */
  onSuccess: () => void
  /** 結果が届いたとき・ポップアップが閉じたとき(設定を取り直す)。 */
  onSettled: () => void
}

export function useAuthTestLogin({ onSuccess, onSettled }: Options) {
  const [state, setState] = useState<AuthTestState>({ status: 'idle' })
  const popupRef = useRef<Window | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const callbacksRef = useRef({ onSuccess, onSettled })
  useEffect(() => {
    callbacksRef.current = { onSuccess, onSettled }
  })

  const stopWatching = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      const result = parseAuthTestMessage(event, window.location.origin)
      if (!result) return
      // 自分の開いたポップアップからのものだけ(別のタブのテストの結果を取り違えない)。
      if (popupRef.current && event.source !== popupRef.current) return
      if (result.ok) {
        setState({ status: 'idle' })
        callbacksRef.current.onSuccess()
      } else {
        setState({ status: 'failed', error: result.error ?? 'unknown' })
      }
      callbacksRef.current.onSettled()
    }
    window.addEventListener('message', handleMessage)
    return () => {
      window.removeEventListener('message', handleMessage)
      stopWatching()
    }
  }, [stopWatching])

  const start = useCallback(() => {
    stopWatching()
    const popup = window.open('/api/auth/test-login', POPUP_NAME, POPUP_FEATURES)
    if (!popup) {
      popupRef.current = null
      setState({ status: 'blocked' })
      return
    }
    popupRef.current = popup
    setState({ status: 'waiting' })
    timerRef.current = setInterval(() => {
      if (!popup.closed) return
      stopWatching()
      // 結果が届かないまま閉じた(閉じられた・オリジンが違う)。待ちを解いて読み直す。
      setState((prev) => (prev.status === 'waiting' ? { status: 'idle' } : prev))
      callbacksRef.current.onSettled()
    }, CLOSED_CHECK_INTERVAL_MS)
  }, [stopWatching])

  return { state, start }
}
