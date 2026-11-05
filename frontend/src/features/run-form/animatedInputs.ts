/**
 * アニメーション GIF は Run の入力に使えない(ADR-0061 6章。1コマ目だけが送られ、見えている画像と
 * 食い違うため)。サーバーも 422 にするが、送る前に止め、入力に使うボタンも無効にする。
 */
export function isAnimatedAsset(asset: { mime?: string | null } | null | undefined): boolean {
  return asset?.mime === 'image/gif'
}
