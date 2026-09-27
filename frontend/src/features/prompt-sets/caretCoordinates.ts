/**
 * textarea 内の任意の文字位置(`position`)の画面座標(viewport 基準。`getBoundingClientRect`
 * や `position: fixed` と同じ座標系)を求める。
 *
 * textarea 自体は「その文字がどこに描画されているか」を教えてくれないため、ミラー方式を使う:
 * textarea と同じ折り返し・フォント・余白・境界線を再現した不可視の div を body 直下に作り、
 * `position` までのテキストと目印の span を入れて、その span の位置を測る。div は測定後すぐに
 * 取り除く(DOM には残さない)。textarea 自身のスクロール(`scrollTop`/`scrollLeft`)も考慮する。
 *
 * 純粋関数ではない(DOM に触れる)。上下どちらに開くか・高さ・左右位置の計算は
 * `mentionPlacement.ts` 側の純粋関数に分離してあり、そちらを vitest でテストする。
 */

export interface CaretRect {
  top: number
  left: number
  bottom: number
  height: number
}

// textarea の折り返し・字詰めに影響する CSS プロパティ。ミラー div にそのままコピーする。
const MIRRORED_PROPERTIES = [
  'boxSizing',
  'width',
  'paddingTop',
  'paddingRight',
  'paddingBottom',
  'paddingLeft',
  'borderTopWidth',
  'borderRightWidth',
  'borderBottomWidth',
  'borderLeftWidth',
  'borderTopStyle',
  'borderRightStyle',
  'borderBottomStyle',
  'borderLeftStyle',
  'fontFamily',
  'fontSize',
  'fontWeight',
  'fontStyle',
  'fontVariant',
  'letterSpacing',
  'lineHeight',
  'textIndent',
  'tabSize',
  'whiteSpace',
  'overflowWrap',
  'wordBreak',
] as const

export function getCaretClientRect(textarea: HTMLTextAreaElement, position: number): CaretRect {
  const computed = window.getComputedStyle(textarea)

  const mirror = document.createElement('div')
  mirror.style.position = 'absolute'
  mirror.style.visibility = 'hidden'
  mirror.style.top = '0'
  mirror.style.left = '0'
  // pre-wrap/break-word は textarea の描画と同じにする(computed 側の値を優先してコピーするが、
  // 何らかの理由で取れなかった場合のフォールバック)。
  mirror.style.whiteSpace = 'pre-wrap'
  mirror.style.overflowWrap = 'break-word'
  mirror.style.wordBreak = 'break-word'

  for (const prop of MIRRORED_PROPERTIES) {
    const value = computed.getPropertyValue(cssPropertyName(prop))
    if (value) mirror.style.setProperty(cssPropertyName(prop), value)
  }

  document.body.appendChild(mirror)

  const before = textarea.value.slice(0, Math.max(0, Math.min(position, textarea.value.length)))
  mirror.textContent = before
  const marker = document.createElement('span')
  // 空だと一部ブラウザで矩形が潰れる(幅0扱いになる)ことがあるため、ゼロ幅スペースを入れる。
  marker.textContent = '​'
  mirror.appendChild(marker)

  const mirrorRect = mirror.getBoundingClientRect()
  const markerRect = marker.getBoundingClientRect()

  document.body.removeChild(mirror)

  // markerRect / mirrorRect は同じ viewport 座標系なので、mirror の外枠(= textarea の外枠と
  // 同じ padding/border)からの相対位置がそのまま textarea 内でのオフセットになる。
  const offsetTop = markerRect.top - mirrorRect.top
  const offsetLeft = markerRect.left - mirrorRect.left
  const height = markerRect.height || parseFloat(computed.lineHeight) || textarea.clientHeight

  const textareaRect = textarea.getBoundingClientRect()
  const top = textareaRect.top + offsetTop - textarea.scrollTop
  const left = textareaRect.left + offsetLeft - textarea.scrollLeft

  return { top, left, bottom: top + height, height }
}

function cssPropertyName(camelCase: string): string {
  return camelCase.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`)
}
