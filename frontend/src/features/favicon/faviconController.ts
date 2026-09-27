/**
 * favicon の DOM 操作(ADR-0009 8章)。`<link rel="icon">` を1つに差し替えて、状態が変わる
 * たびに `data:` URL を書き換える。React の外側で動くので DOM/タイマー操作が中心になり、
 * 自動テストの対象外(ロジックは `deriveFaviconState.ts` / `iconShape.ts` に切り出し済み)。
 *
 * - spin と fill は 500ms 間隔のコマ送り(参照実装の 250ms より遅くする。非表示タブでは
 *   ブラウザがタイマーを 1 秒に間引くため、コマが飛んでも読める前提)。fill の tiles が
 *   変わってもコマ送りは止めず、コマ番号を引き継ぐ(色の流れと明滅が途切れないように)。
 * - done は sealed(満杯+蓋)を 700ms 表示 → タブが非表示なら通知ドット付きの idle にし、
 *   表示に戻ったら(`visibilitychange`)idle に戻す。表示中ならそのまま idle。
 * - error はそのまま表示し、非表示から表示に戻ったときだけ idle に戻す
 *   (参照実装には無い挙動なのでここで足す)。
 * - 同じ FaviconState(spin→spin、done→done など)が続けて来ても、タイマーや演出の途中で
 *   壊さないよう張り直さない。状態が変わったとき(fill の tiles が変わった場合を含む)だけ切り替える。
 * - Safari は SVG の favicon を反映しないため、`shouldUsePngFavicon` で判定して canvas で
 *   64px の PNG に変換する。PNG のときは `prefers-color-scheme` の変化も追って描き直す。
 * - `setFaviconEnabled(false)`(設定の「タブのアイコンで進捗を示す」オフ、2026-09-26 追加)は
 *   差し替えていた `<link>` を外して元の `<link>` に戻す(`dispose` と同じ処理を link にだけ
 *   行う)。以後 `applyIcon` は DOM を書き換えないが、IconState の更新と購読者への通知は続ける
 *   (App バーのロゴは設定に関係なく動き続けるため)。`true` に戻すと次の描画から再び差し替え、
 *   現在の IconState をすぐ描き直す。`dispose` 自体の挙動は変えない。
 */
import type { FaviconState } from './deriveFaviconState'
import type { IconState } from './iconShape'
import { renderFaviconSvg, svgToDataUrl } from './faviconSvg'
import { shouldUsePngFavicon } from './browserSupport'

const SPIN_INTERVAL_MS = 500
const SEAL_MS = 700
const PNG_SIZE = 64

export interface FaviconController {
  setState(state: FaviconState): void
  /** タブの favicon への反映そのものの有効/無効(設定「タブのアイコンで進捗を示す」)。 */
  setFaviconEnabled(enabled: boolean): void
  /** 元の `<link rel="icon">` に戻す。 */
  dispose(): void
  /** 現在描画中の IconState(App バーのロゴ用)。 */
  getIconState(): IconState
  subscribe(listener: () => void): () => void
}

function svgToPngDataUrl(svg: string, size: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = size
      canvas.height = size
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('2d context is not available'))
        return
      }
      ctx.drawImage(img, 0, 0, size, size)
      resolve(canvas.toDataURL('image/png'))
    }
    img.onerror = () => reject(new Error('failed to rasterize favicon svg'))
    img.src = svgToDataUrl(svg)
  })
}

/** 同じ「意味」の状態が続けて来たかどうか(fill は tiles の値まで比べる)。 */
function isSameFaviconState(a: FaviconState | null, b: FaviconState): boolean {
  if (a === null) return false
  if (a.kind !== b.kind) return false
  if (a.kind === 'fill' && b.kind === 'fill') return a.tiles === b.tiles
  return true
}

export function createFaviconController(options: { userAgent?: string } = {}): FaviconController {
  const userAgent = options.userAgent ?? (typeof navigator !== 'undefined' ? navigator.userAgent : '')
  const usePng = shouldUsePngFavicon(userAgent)

  let link: HTMLLinkElement | null = null
  let savedLinks: HTMLLinkElement[] = []
  let faviconEnabled = true
  let lastFaviconState: FaviconState | null = null
  let currentIconState: IconState = { kind: 'idle' }
  let animFrame = 0
  let renderFrame: ((frame: number) => IconState) | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let onVisibilityChange: (() => void) | null = null
  let mediaQuery: MediaQueryList | null = null
  let onMediaChange: (() => void) | null = null
  let applySeq = 0
  const listeners = new Set<() => void>()

  function notify(): void {
    for (const listener of listeners) listener()
  }

  function ensureLink(): HTMLLinkElement {
    if (!link) {
      savedLinks = Array.from(document.querySelectorAll('link[rel~="icon"]'))
      savedLinks.forEach((l) => l.remove())
      link = document.createElement('link')
      link.rel = 'icon'
      link.type = usePng ? 'image/png' : 'image/svg+xml'
      document.head.appendChild(link)
    }
    return link
  }

  function currentTheme(): 'dark' | 'light' {
    return mediaQuery?.matches ? 'dark' : 'light'
  }

  /** 差し替えていた `<link>` があれば外し、`ensureLink` が退避した元の `<link>` を戻す。 */
  function restoreOriginalLink(): void {
    if (link) {
      link.remove()
      savedLinks.forEach((l) => document.head.appendChild(l))
      link = null
      savedLinks = []
    }
  }

  function applyIcon(next: IconState): void {
    currentIconState = next
    notify()
    if (!faviconEnabled) return
    const activeLink = ensureLink()
    const mySeq = ++applySeq
    const svg = renderFaviconSvg(next, usePng ? currentTheme() : 'auto')
    if (usePng) {
      svgToPngDataUrl(svg, PNG_SIZE)
        .then((href) => {
          if (mySeq !== applySeq) return
          activeLink.href = href
        })
        .catch(() => {
          // ラスタライズに失敗しても favicon が消えるだけなので、握りつぶして継続する。
        })
    } else {
      activeLink.href = svgToDataUrl(svg)
    }
  }

  function stopTimer(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function stopVisibilityWatch(): void {
    if (onVisibilityChange) {
      document.removeEventListener('visibilitychange', onVisibilityChange)
      onVisibilityChange = null
    }
  }

  function stopAnimation(): void {
    stopTimer()
    stopVisibilityWatch()
    renderFrame = null
  }

  /**
   * コマ送りのアニメーションを流す。すでに流れているなら(fill の tiles が変わったときなど)
   * タイマーとコマ番号はそのままに、描く内容だけ差し替える。
   */
  function runAnimation(render: (frame: number) => IconState): void {
    const running = renderFrame !== null && timer !== null
    renderFrame = render
    if (!running) animFrame = 0
    applyIcon(render(animFrame))
    if (running) return
    const tick = () => {
      animFrame += 1
      if (renderFrame) applyIcon(renderFrame(animFrame))
      timer = setTimeout(tick, SPIN_INTERVAL_MS)
    }
    timer = setTimeout(tick, SPIN_INTERVAL_MS)
  }

  function watchVisibilityThenIdle(): void {
    onVisibilityChange = () => {
      if (!document.hidden) {
        stopVisibilityWatch()
        applyIcon({ kind: 'idle' })
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
  }

  function startDone(): void {
    applyIcon({ kind: 'sealed' })
    timer = setTimeout(() => {
      timer = null
      if (typeof document !== 'undefined' && document.hidden) {
        applyIcon({ kind: 'done', notify: true })
        watchVisibilityThenIdle()
      } else {
        applyIcon({ kind: 'idle' })
      }
    }, SEAL_MS)
  }

  function startError(): void {
    applyIcon({ kind: 'error' })
    if (typeof document !== 'undefined' && document.hidden) {
      watchVisibilityThenIdle()
    }
  }

  // PNG のときだけ配色の変化(ダーク/ライト切替)を追う。SVG は <style> の
  // prefers-color-scheme に任せられるので不要。
  if (usePng && typeof matchMedia === 'function') {
    mediaQuery = matchMedia('(prefers-color-scheme: dark)')
    onMediaChange = () => applyIcon(currentIconState)
    mediaQuery.addEventListener('change', onMediaChange)
  }

  function setState(state: FaviconState): void {
    if (isSameFaviconState(lastFaviconState, state)) return
    const continuesAnimation = lastFaviconState?.kind === state.kind && (state.kind === 'fill' || state.kind === 'spin')
    lastFaviconState = state
    if (!continuesAnimation) stopAnimation()
    switch (state.kind) {
      case 'idle':
        applyIcon({ kind: 'idle' })
        break
      case 'queued':
        applyIcon({ kind: 'queued' })
        break
      case 'fill': {
        const tiles = state.tiles
        runAnimation((frame) => ({ kind: 'fill', tiles, frame }))
        break
      }
      case 'spin':
        runAnimation((frame) => ({ kind: 'spin', frame }))
        break
      case 'done':
        startDone()
        break
      case 'error':
        startError()
        break
    }
  }

  function setFaviconEnabled(enabled: boolean): void {
    if (faviconEnabled === enabled) return
    faviconEnabled = enabled
    if (!faviconEnabled) {
      restoreOriginalLink()
    } else {
      applyIcon(currentIconState)
    }
  }

  function dispose(): void {
    stopAnimation()
    if (mediaQuery && onMediaChange) mediaQuery.removeEventListener('change', onMediaChange)
    mediaQuery = null
    onMediaChange = null
    restoreOriginalLink()
  }

  return {
    setState,
    setFaviconEnabled,
    dispose,
    getIconState: () => currentIconState,
    subscribe(listener: () => void) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}
