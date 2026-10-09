/**
 * プロンプト欄の「テキスト / タグ」のモードを、(プロバイダー, モデル)と欄ごとに
 * localStorage に覚える(ADR-0039 2章)。モデルがタグ向きか文章向きかは GAKEI が判定しない
 * ので、利用者が選んだモードをモデルごとに覚えておき、そのモデルを選んだときにそのモードで開く。
 * 初めてのモデルはテキスト。`seedModePrefs.ts` と同じく try/catch で囲み、使えない環境では
 * 黙って既定(テキスト)で動く。
 */

export type PromptEditMode = 'text' | 'tags'

export const DEFAULT_PROMPT_EDIT_MODE: PromptEditMode = 'text'

const STORAGE_KEY = 'gakei.runForm.promptEditMode'
/** 覚えるモデルの数の上限(古いものから捨てる)。 */
const MAX_ENTRIES = 200

type Stored = Record<string, Record<string, PromptEditMode>>

function scopeKey(provider: string, model: string): string {
  return `${provider}/${model}`
}

function readAll(): Stored {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return parsed as Stored
  } catch {
    return {}
  }
}

/** 覚えているモード。無い・壊れている・読めないときはテキスト。 */
export function loadPromptEditMode(provider: string, model: string, field: string): PromptEditMode {
  const entry = readAll()[scopeKey(provider, model)]
  const value = entry && typeof entry === 'object' ? entry[field] : undefined
  return value === 'tags' ? 'tags' : DEFAULT_PROMPT_EDIT_MODE
}

/** モードを覚える。保存できなくても黙って無視する。 */
export function savePromptEditMode(provider: string, model: string, field: string, mode: PromptEditMode): void {
  try {
    const all = readAll()
    const key = scopeKey(provider, model)
    const entry = { ...(all[key] && typeof all[key] === 'object' ? all[key] : {}) }
    if (mode === DEFAULT_PROMPT_EDIT_MODE) delete entry[field]
    else entry[field] = mode
    // 最後に使ったものを後ろに置き直す(上限を超えたら先頭から捨てる)。
    delete all[key]
    if (Object.keys(entry).length > 0) all[key] = entry
    const keys = Object.keys(all)
    for (const old of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) delete all[old]
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all))
  } catch {
    // 保存できなくても致命的ではない。
  }
}
