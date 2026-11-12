/**
 * 読み込みに失敗した `<img>` を、間隔を空けて読み直す。
 *
 * サーバーが混んでいて一度だけ失敗した画像(500 など)が、ページを読み直すまで枠だけのままに
 * ならないようにする。回数には上限を付ける(本当に無い画像を取りに行き続けない)。
 * `attempt` を `<img>` の `key` に使うと、読み直しのたびに `<img>` を作り直す(同じ URL でも
 * 取り直す。失敗した応答はブラウザのキャッシュに残らない)。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

/** 読み直すまでの間隔(ms)。この回数だけ読み直す。 */
export const IMAGE_RETRY_DELAYS_MS: readonly number[] = [2_000, 6_000, 20_000, 60_000]

/** `attempt` 回読み直した後の失敗で、次に読み直すまでの間隔(ms)。もう読み直さないなら null。 */
export function imageRetryDelayMs(attempt: number): number | null {
  return attempt >= 0 && attempt < IMAGE_RETRY_DELAYS_MS.length ? IMAGE_RETRY_DELAYS_MS[attempt] : null
}

/**
 * `src` の `<img>` に付ける。`onError` を `<img>` に渡し、`attempt` を `key` にする。`src` が
 * 変わったら数え直す。
 */
export function useImageRetry(src: string | undefined): { attempt: number; onError: () => void } {
  const [state, setState] = useState({ src, attempt: 0 })
  const attempt = state.src === src ? state.attempt : 0
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    // src が変わった・外れたら、待っていた読み直しは取りやめる。
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [src])

  const onError = useCallback(() => {
    const delay = imageRetryDelayMs(attempt)
    if (delay === null || timerRef.current !== null) return
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      setState({ src, attempt: attempt + 1 })
    }, delay)
  }, [src, attempt])

  return { attempt, onError }
}
