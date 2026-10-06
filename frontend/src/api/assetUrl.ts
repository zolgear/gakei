/**
 * Asset の配信 URL 組み立てを1関数に閉じる(ADR-0004)。
 * base64 や blob URL は使わず、常にこの URL を <img src> に渡す。
 */
export type AssetVariant = 'thumb' | 'preview' | 'original'

/**
 * 派生画像(thumb / preview)の作り方の版(ADR-0036 3章)。バックエンドの
 * `app/domain/derivatives.py` の `DERIVED_VERSION` と同じ値にする(バックエンドのテストで
 * 一致を確かめる)。版が変わると URL が変わり、ブラウザの `immutable` のキャッシュを使わなくなる。
 */
export const DERIVED_VERSION = 1

/** 配信 URL の問い合わせ文字列。派生にだけ版(`dv`)を付ける。サーバーは `dv` を見ない。 */
function contentQuery(variant: AssetVariant, opts: { download?: boolean }): string {
  const qs = new URLSearchParams({ variant })
  if (variant !== 'original') qs.set('dv', String(DERIVED_VERSION))
  if (opts.download) qs.set('download', '1')
  return qs.toString()
}

export function assetUrl(
  assetId: string,
  variant: AssetVariant,
  opts: { download?: boolean } = {},
): string {
  return `/api/assets/${assetId}/content?${contentQuery(variant, opts)}`
}

/** 途中経過画像(Asset にしない)の URL。 */
export function runPartialUrl(runId: string, index: number): string {
  return `/api/runs/${runId}/partials/${index}`
}

/**
 * ログイン不要の共有リンクの画像の URL(ADR-0029)。本人向けの `assetUrl` とは別のパス
 * (`/api/public/`)で、トークン自体が認可になる。
 */
export function publicShareAssetUrl(
  token: string,
  assetId: string,
  variant: AssetVariant,
  opts: { download?: boolean } = {},
): string {
  return `/api/public/shares/${encodeURIComponent(token)}/assets/${assetId}/content?${contentQuery(variant, opts)}`
}
