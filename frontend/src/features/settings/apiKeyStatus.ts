/**
 * OpenAI API キーの状態(`OpenAIKeyStatusResponse`)から、設定画面・案内バナーで使う表示を
 * 純粋関数として切り出したもの(ADR-0012 Decision 4)。環境変数(`.env`)の値は画面で保存した
 * ものより優先し、画面からは変更・削除できない。
 */
import type { OpenAIKeyStatus } from '../../api/client'
import { msg } from '../../i18n'

/** 設定画面の状態表示。`state` でバッジの色を、`detail` で出所を示す(キーは一部も出さない)。 */
export interface ApiKeyStatusView {
  state: 'configured' | 'missing'
  title: string
  detail: string | null
}

export function apiKeyStatusView(status: OpenAIKeyStatus): ApiKeyStatusView {
  const t = msg().settings.apiKey.status
  if (!status.configured) {
    return { state: 'missing', title: t.missing, detail: null }
  }
  const origin = status.source === 'env' ? t.sourceEnv : t.sourceFile
  return {
    state: 'configured',
    title: t.configured,
    detail: origin,
  }
}

/** 環境変数が優先されていて、画面からの変更・削除ができない状態か。 */
export function isEnvLocked(status: OpenAIKeyStatus): boolean {
  return status.source === 'env'
}

/** 削除ボタンを出してよいか(画面で保存したキーがあるときだけ)。 */
export function canDeleteKey(status: OpenAIKeyStatus): boolean {
  return status.source === 'file' && status.configured
}

/**
 * 初回起動の案内バナーを出すべきか。プロバイダーがキーを必要とする(`required`)のに、
 * どこにもキーが設定されていない(`configured` が false)ときだけ出す。
 */
export function shouldShowMissingKeyBanner(status: OpenAIKeyStatus): boolean {
  return status.required && !status.configured
}
