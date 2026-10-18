/**
 * Asset の配信 URL を出す素の `<img>` に、「画像なし(拒否)」の安全網を付けたもの(ADR-0051 4章)。
 *
 * - `withheld` が分かっていれば `<img>` を読みに行かず、置き換えの表示を描く。
 * - 分かっていなくても、読み込みに失敗して 410 だったら置き換えの表示に切り替える
 *   (`withheldFallback`)。応答に `content_withheld` の項目が無い場所(アシスタントの添付、
 *   マスクのサムネイルなど)はこれを使う。
 * - 置き換えの表示は `<img>` の className と style をそのまま受け取る(寸法の CSS を崩さない)。
 */
import type { ImgHTMLAttributes, SyntheticEvent } from 'react'
import { WithheldPlaceholder } from './WithheldPlaceholder'
import { useWithheldFallback } from './withheldFallback'

interface AssetImageProps extends ImgHTMLAttributes<HTMLImageElement> {
  src: string
  /** 画像を残さなかったと分かっている。 */
  withheld?: boolean
  /** 置き換えの表示に添える判定の label。 */
  withheldLabel?: string | null
}

export function AssetImage({ withheld = false, withheldLabel, onError, ...imgProps }: AssetImageProps) {
  const fallback = useWithheldFallback(imgProps.src)
  if (withheld || fallback.withheld) {
    return <WithheldPlaceholder className={imgProps.className} style={imgProps.style} label={withheldLabel} />
  }
  function handleError(e: SyntheticEvent<HTMLImageElement>) {
    onError?.(e)
    fallback.onError()
  }
  return <img {...imgProps} onError={handleError} />
}
