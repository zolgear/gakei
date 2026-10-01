/**
 * 設定ページの下書き(ADR-0031 2・3章)の純粋関数。
 * 下書きは「保存済みの値」と「利用者が変えた値(上書き)」の2つで持つ。下書きの値は
 * 保存済みの値に上書きを重ねたもの。保存済みと同じ値に戻した欄は上書きから外すので、
 * 「変えた欄」は上書きに残っているキーと一致する。保存で送るのも、このキーだけ
 * (今の自動タイトル・タグの `diffAnnotationForm` と同じく、変えたキーだけを1つの PATCH で送る)。
 */

/** 2つの値が同じか。設定の値は文字列・数値・真偽値・null のほか、配列やオブジェクトもありうる。 */
export function isSameDraftValue(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false
  return JSON.stringify(a) === JSON.stringify(b)
}

/** 保存済みの値に上書きを重ねた、画面に出す値。 */
export function draftValues<T extends object>(saved: T, overrides: Partial<T>): T {
  return { ...saved, ...overrides }
}

/**
 * 1つの欄を変えたあとの上書き。保存済みと同じ値に戻したら、その欄は上書きから外す
 * (変更の件数と「変えた欄」の印を正しく保つため)。
 */
export function setDraftOverride<T extends object, K extends keyof T>(
  saved: T,
  overrides: Partial<T>,
  key: K,
  value: T[K],
): Partial<T> {
  const next: Partial<T> = { ...overrides }
  if (isSameDraftValue(saved[key], value)) {
    delete next[key]
  } else {
    next[key] = value
  }
  return next
}

/** 保存済みと違う値になっている欄のキー(保存済みの値が後から変わった場合も、ここで比べ直す)。 */
export function changedDraftKeys<T extends object>(saved: T, overrides: Partial<T>): (keyof T)[] {
  return (Object.keys(overrides) as (keyof T)[]).filter((key) => !isSameDraftValue(saved[key], overrides[key]))
}

/** 保存で送る差分(変えたキーだけ)。 */
export function draftPatch<T extends object>(saved: T, overrides: Partial<T>): Partial<T> {
  const patch: Partial<T> = {}
  for (const key of changedDraftKeys(saved, overrides)) {
    patch[key] = overrides[key]
  }
  return patch
}

/** 検証の結果(欄ごとのエラー文言)。エラーの無い欄はキーを持たない。 */
export type DraftErrors<T> = Partial<Record<keyof T, string>>

export function hasDraftErrors<T>(errors: DraftErrors<T>): boolean {
  return Object.values(errors).some((v) => typeof v === 'string' && v !== '')
}

/** ヘッダーの「保存」を押せるか: 変更があり、検証が通っていて、保存中でないとき(ADR-0031 3章)。 */
export function canSaveDraft(params: { changedCount: number; valid: boolean; saving: boolean }): boolean {
  return params.changedCount > 0 && params.valid && !params.saving
}
