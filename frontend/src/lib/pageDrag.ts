/**
 * このページの中で始まったドラッグかを覚える。
 *
 * ページの `<img>`(履歴やマップの画像など)をドラッグすると、ブラウザは画像そのものを File として
 * 添える(`dataTransfer.types` に 'Files' が入る)。外から持ち込んだファイルと見分けられないので、
 * ドラッグの始まりを document で受けて覚えておく。外からのドラッグでは dragstart が起きない。
 */
let fromPage = false
let installed = false

export function installPageDragTracking(target: Document = document): void {
  if (installed) return
  installed = true
  // capture で受け、子が stopPropagation しても取りこぼさない。
  target.addEventListener('dragstart', () => {
    fromPage = true
  }, true)
  const clear = () => {
    fromPage = false
  }
  target.addEventListener('dragend', clear, true)
  target.addEventListener('drop', () => {
    // drop の処理が読み終わってから下ろす(同じイベントの中で判定するため)。
    setTimeout(clear, 0)
  }, true)
}

export function isDragFromThisPage(): boolean {
  return fromPage
}
