/**
 * このページの中で始まったドラッグかを覚える。
 *
 * ページの `<img>`(履歴やマップの画像など)をドラッグすると、ブラウザは画像そのものを File として
 * 添える(`dataTransfer.types` に 'Files' が入る)。外から持ち込んだファイルと見分けられないので、
 * ドラッグの始まりを document で受けて覚えておく。外からのドラッグでは dragstart が起きない。
 */
let fromPage = false
let installed = false

/**
 * 文字の入力欄へ落とすと URL が文字として入ってしまうものか(ページの中から運んだリンク・画像・
 * アプリの Asset)。入力欄の中の文字を選んで動かすドラッグ(text/plain だけ)は対象にしない。
 */
export function insertsUrlIntoTextField(types: readonly string[]): boolean {
  return types.some((type) => type === 'text/uri-list' || type === 'Files' || type.startsWith('application/x-gakei'))
}

function isTextField(target: EventTarget | null): boolean {
  if (typeof HTMLElement === 'undefined' || !(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  if (target instanceof HTMLTextAreaElement) return true
  return target instanceof HTMLInputElement && !['file', 'checkbox', 'radio', 'range', 'color'].includes(target.type)
}

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
  // ページの中の画像やリンクを文字の入力欄(検索の欄など)へ落とすと、ブラウザが URL を文字として
  // 入れてしまう。入力欄では受けない(枠の側で画像として受ける処理は、伝播を止めないので動く)。
  target.addEventListener('dragover', (e) => {
    const event = e as DragEvent
    if (fromPage && isTextField(event.target) && insertsUrlIntoTextField(Array.from(event.dataTransfer?.types ?? []))) {
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'none'
    }
  }, true)
  target.addEventListener('drop', (e) => {
    const event = e as DragEvent
    if (fromPage && isTextField(event.target) && insertsUrlIntoTextField(Array.from(event.dataTransfer?.types ?? []))) {
      event.preventDefault()
    }

    // drop の処理が読み終わってから下ろす(同じイベントの中で判定するため)。
    setTimeout(clear, 0)
  }, true)
}

export function isDragFromThisPage(): boolean {
  return fromPage
}
