/**
 * Safari は `<link rel="icon" type="image/svg+xml">` を表示しない(タブの favicon が更新
 * されない)ため、Safari のときだけ canvas で 64px の PNG に変換して差し替える
 * (`faviconController.ts` が使う)。Chrome/Edge/Firefox 系(iOS/Android を含む)は false。
 */
export function shouldUsePngFavicon(userAgent: string): boolean {
  // iOS の Chrome(CriOS)/Firefox(FxiOS)/Edge(EdgiOS)/Opera(OPiOS) は WebKit を使うため
  // UA に "Safari" を含むが、実体は Safari ではないので先に弾く。
  const isOtherBrowserOnWebKit = /CriOS\/|FxiOS\/|EdgiOS\/|OPiOS\//.test(userAgent)
  if (isOtherBrowserOnWebKit) return false

  // デスクトップ/Android の Chrome・Edge・Opera・Firefox。
  const isChromiumOrFirefox = /Chrome\/|Chromium\/|Edg\/|OPR\/|Firefox\//.test(userAgent)
  if (isChromiumOrFirefox) return false

  // 残りのうち、Mac / iOS の Safari だけ true。
  const isAppleDevice = /Macintosh|iPhone|iPad|iPod/.test(userAgent)
  return isAppleDevice && /Safari\//.test(userAgent)
}
