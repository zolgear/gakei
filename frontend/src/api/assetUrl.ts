/**
 * Asset の配信 URL 組み立てを1関数に閉じる(ADR-0004)。
 * base64 や blob URL は使わず、常にこの URL を <img src> に渡す。
 */
export type AssetVariant = 'thumb' | 'preview' | 'original'

export function assetUrl(
  assetId: string,
  variant: AssetVariant,
  opts: { download?: boolean } = {},
): string {
  const qs = new URLSearchParams({ variant })
  if (opts.download) qs.set('download', '1')
  return `/api/assets/${assetId}/content?${qs.toString()}`
}

/** 途中経過画像(Asset にしない)の URL。 */
export function runPartialUrl(runId: string, index: number): string {
  return `/api/runs/${runId}/partials/${index}`
}
