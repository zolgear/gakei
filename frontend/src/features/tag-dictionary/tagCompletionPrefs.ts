/**
 * プロンプトのタグ補完と、タグの日本語訳の表示の設定(ADR-0041 3章・4章)。ブラウザごとの好みなので
 * localStorage に保存する(`stockPrefs.ts` と同じく、モジュールに1つだけ状態を持ち
 * `useSyncExternalStore` で購読する)。
 *
 * - 「プロンプトのタグ補完」(テキストモードの入力アシスト): `tag-providers`(SD WebUI と ComfyUI のとき
 *   だけ。既定)/ `always` / `off`。タグモードのチップの入力欄は、この設定に関わらず候補を出す。
 * - 「タグの日本語訳を表示」: 既定はオン。
 */
import { useSyncExternalStore } from 'react'

export type TagCompletionMode = 'tag-providers' | 'always' | 'off'

export const TAG_COMPLETION_MODES: readonly TagCompletionMode[] = ['tag-providers', 'always', 'off']

const COMPLETION_KEY = 'gakei:prompt-tag-completion'
const TRANSLATIONS_KEY = 'gakei:tag-translations'

/** タグで書くことの多いプロバイダー(既定の設定で補完を出す)。 */
const TAG_PROVIDERS = new Set(['sdwebui', 'comfyui'])

export function isTagCompletionMode(value: unknown): value is TagCompletionMode {
  return typeof value === 'string' && (TAG_COMPLETION_MODES as readonly string[]).includes(value)
}

/** 保存した値を読む。知らない値・未設定は既定(`tag-providers`)。 */
export function parseTagCompletionMode(raw: unknown): TagCompletionMode {
  return isTagCompletionMode(raw) ? raw : 'tag-providers'
}

/** 訳の表示。'false' のときだけオフ(未設定・不正な値は既定のオン)。 */
export function parseShowTagTranslations(raw: unknown): boolean {
  return raw !== 'false'
}

/** テキストモードのプロンプト欄で、タグの候補を出すか。 */
export function shouldAssistTextPrompt(mode: TagCompletionMode, provider: string): boolean {
  if (mode === 'off') return false
  if (mode === 'always') return true
  return TAG_PROVIDERS.has(provider)
}

/** 訳を添えるか(画面の言語が日本語で、設定がオンのときだけ)。 */
export function shouldShowTagTranslations(locale: string, enabled: boolean): boolean {
  return enabled && locale === 'ja'
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // 保存できなくても、この画面の間は切り替える。
  }
}

let completionMode: TagCompletionMode = parseTagCompletionMode(read(COMPLETION_KEY))
let showTranslations: boolean = parseShowTagTranslations(read(TRANSLATIONS_KEY))
const listeners = new Set<() => void>()

function notify() {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function getTagCompletionMode(): TagCompletionMode {
  return completionMode
}

export function setTagCompletionMode(mode: TagCompletionMode): void {
  completionMode = mode
  write(COMPLETION_KEY, mode)
  notify()
}

export function getShowTagTranslations(): boolean {
  return showTranslations
}

export function setShowTagTranslations(show: boolean): void {
  showTranslations = show
  write(TRANSLATIONS_KEY, show ? 'true' : 'false')
  notify()
}

export function useTagCompletionMode(): TagCompletionMode {
  return useSyncExternalStore(subscribe, getTagCompletionMode, getTagCompletionMode)
}

export function useShowTagTranslationsPref(): boolean {
  return useSyncExternalStore(subscribe, getShowTagTranslations, getShowTagTranslations)
}
