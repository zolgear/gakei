/**
 * プロンプトセットの入力チェック(サーバー側の 422 を待たずに分かる範囲だけ)。
 * backend/app/domain/schemas.py の制約と合わせる: name 1〜100文字、text 1〜32,000文字、
 * label は 100文字まで(空文字列は「ラベル無し」として扱う)。
 */
import { fmt, msg } from '../../i18n'

export interface FieldValidationResult {
  valid: boolean
  error?: string
}

const NAME_MAX = 100
export const PROMPT_TEXT_MAX_LENGTH = 32_000
const TEXT_MAX = PROMPT_TEXT_MAX_LENGTH
const LABEL_MAX = 100

export function validateSetName(name: string): FieldValidationResult {
  const t = msg().promptSets.validation
  const trimmed = name.trim()
  if (trimmed.length === 0) return { valid: false, error: t.nameRequired }
  if (trimmed.length > NAME_MAX) return { valid: false, error: fmt(t.nameTooLong, { max: NAME_MAX }) }
  return { valid: true }
}

export function validateItemText(text: string): FieldValidationResult {
  const t = msg().promptSets.validation
  if (text.length === 0) return { valid: false, error: t.textRequired }
  if (text.length > TEXT_MAX) return { valid: false, error: fmt(t.textTooLong, { max: TEXT_MAX }) }
  return { valid: true }
}

export function validateItemLabel(label: string): FieldValidationResult {
  const t = msg().promptSets.validation
  if (label.length > LABEL_MAX) return { valid: false, error: fmt(t.labelTooLong, { max: LABEL_MAX }) }
  return { valid: true }
}

/** 空文字列は「ラベル無し」= null として送る(API は null を明示するとクリアする)。 */
export function normalizeLabel(label: string): string | null {
  const trimmed = label.trim()
  return trimmed.length === 0 ? null : trimmed
}
