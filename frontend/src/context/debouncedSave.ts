/**
 * フォームの保存のデバウンス(RunFormContext が使う)。`flush` は待っている保存があるときだけ前倒し
 * する。待っていなければ何も書かない: 保存先(localStorage)はこのタブの最新の値を既に持っているか、
 * 別のタブが後から書いた値を持っている。後者を、このタブの古い値で上書きしないため。
 */
export interface DebouncedSave<T> {
  /** 値を覚え、`delayMs` 後に保存する(その前に呼ばれたら最後の値だけを保存する)。 */
  schedule: (value: T) => void
  /** 待っている保存があれば、すぐに保存する。 */
  flush: () => void
  /** 待っている保存を捨てる。 */
  cancel: () => void
}

export function createDebouncedSave<T>(save: (value: T) => void, delayMs: number): DebouncedSave<T> {
  let timer: ReturnType<typeof setTimeout> | null = null
  let pending: { value: T } | null = null

  function cancel() {
    if (timer !== null) clearTimeout(timer)
    timer = null
    pending = null
  }

  function flush() {
    if (pending === null) return
    const { value } = pending
    cancel()
    save(value)
  }

  function schedule(value: T) {
    if (timer !== null) clearTimeout(timer)
    pending = { value }
    timer = setTimeout(flush, delayMs)
  }

  return { schedule, flush, cancel }
}
