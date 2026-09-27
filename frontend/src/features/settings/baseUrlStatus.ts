/**
 * OpenAI の接続先(Base URL)の状態(`OpenAIBaseUrlStatusResponse`)から、設定画面で使う表示を
 * 純粋関数として切り出したもの(ADR-0017)。環境変数(`.env`)の値は画面で保存したものより
 * 優先し、画面からは変更・削除できない。値は秘密ではないので全文を表示する。
 */
import type { OpenAIBaseUrlStatus } from '../../api/client'
import { msg } from '../../i18n'

/** 設定画面の表示。`title` は接続先の全文(未設定なら既定の案内)、`detail` は出所。 */
export interface BaseUrlStatusView {
  title: string
  detail: string | null
}

export function baseUrlStatusView(status: OpenAIBaseUrlStatus): BaseUrlStatusView {
  const t = msg().settings.apiKey.baseUrl.status
  if (!status.value) {
    return { title: t.default, detail: null }
  }
  const origin = status.source === 'env' ? t.sourceEnv : t.sourceFile
  return { title: status.value, detail: origin }
}

/** 環境変数が優先されていて、画面からの変更・削除ができない状態か。 */
export function isBaseUrlEnvLocked(status: OpenAIBaseUrlStatus): boolean {
  return status.source === 'env'
}

/** 「既定に戻す」ボタンを出してよいか(画面で保存した値があるときだけ)。 */
export function canClearBaseUrl(status: OpenAIBaseUrlStatus): boolean {
  return status.source === 'file' && status.value != null
}
