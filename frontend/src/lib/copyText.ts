/**
 * 文字列をクリップボードへコピーする。`navigator.clipboard` は安全なコンテキスト(https か
 * localhost)でしか使えないので、LAN の http で開いた場合に備えて `execCommand('copy')` に
 * 退避する。成功したかを返す(失敗時は呼び出し側が手で選択してもらう旨を出す)。
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 下の退避策を試す。
  }
  try {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(textarea)
    return ok
  } catch {
    return false
  }
}
