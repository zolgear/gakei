/**
 * SD WebUI の設定ページ(`/settings/sdwebui`。ADR-0038 6章、ADR-0031)の判定を切り出した純粋関数。
 * ループバックの判定と、URL の保存ボタンを押せるかは ComfyUI と同じ規則なので
 * `comfyuiConnectionForm.ts` のものを使う(最終判定は常にサーバー)。
 */
import { ApiError, type SdWebuiConnectionTestResponse, type SdWebuiStatus } from '../../api/client'
import { msg } from '../../i18n'
import { isLoopbackUrl } from '../comfyui-workflows/comfyuiConnectionForm'

export { canSaveConnection, connectionState, isLoopbackUrl } from '../comfyui-workflows/comfyuiConnectionForm'

export type SdWebuiFlavor = NonNullable<SdWebuiStatus['flavor']>

/** 接続先の種類の表示名。分からなければ null(行ごと出さない)。 */
export function flavorLabel(flavor: SdWebuiStatus['flavor']): string | null {
  if (!flavor) return null
  return msg().sdwebui.connection.flavors[flavor]
}

/** カードの見出し(無効/接続できる/接続できない)。 */
export function connectionHeadline(status: Pick<SdWebuiStatus, 'enabled' | 'available'>): string {
  const c = msg().sdwebui.connection
  if (!status.enabled) return c.disabledHeadline
  return status.available ? c.availableHeadline : c.unavailableHeadline
}

/**
 * 接続できない理由に添える、この画面での次の一手。`reason_message`(サーバーで翻訳済み)が
 * 起動の仕方(`--api`)まで説明しているので、ここでは画面の中で何をすればよいかだけを返す。
 * - `unauthorized`: 資格情報の節で設定する(保存済みなら差し替える)。
 */
export function reasonNextStep(
  reason: SdWebuiStatus['reason'],
  credentialsSet: boolean,
): string | null {
  const c = msg().sdwebui.connection
  if (reason === 'unauthorized') return credentialsSet ? c.unauthorizedReplaceHint : c.unauthorizedSetHint
  return null
}

/** `source === 'env'` のとき、`SDWEBUI_URL` の既定値で動いていることを伝える注記。 */
export function envSourceNote(status: Pick<SdWebuiStatus, 'source'>): string | null {
  return status.source === 'env' ? msg().sdwebui.connection.envNote : null
}

/** 資格情報の入力の状態。 */
export type CredentialsInput =
  /** 両方とも空(保存済みの資格情報を使う/使わない) */
  | { kind: 'empty' }
  /** 片方だけ入っている(送れない) */
  | { kind: 'partial' }
  | { kind: 'filled'; username: string; password: string }

/**
 * ユーザー名とパスワードの入力を判定する。サーバーは「両方か、両方なし」しか受けない。
 * ユーザー名の前後の空白は落とす(パスワードは空白も値の一部としてそのまま送る)。
 */
export function readCredentialsInput(username: string, password: string): CredentialsInput {
  const u = username.trim()
  if (u === '' && password === '') return { kind: 'empty' }
  if (u === '' || password === '') return { kind: 'partial' }
  return { kind: 'filled', username: u, password }
}

/**
 * 接続テストに成功したが、まだ保存していないことを伝える案内(ComfyUI の `testSuccessNotice` と同じ考え方)。
 * テストした URL が今の入力と一致しているときだけ出す。
 */
export function testSuccessNotice(params: {
  enabled: boolean
  trimmedUrl: string
  testResult: Pick<SdWebuiConnectionTestResponse, 'available' | 'url'> | undefined
  confirmNonLoopback: boolean
}): string | null {
  const { enabled, trimmedUrl, testResult, confirmNonLoopback } = params
  if (!testResult?.available) return null
  if (trimmedUrl === '' || testResult.url !== trimmedUrl) return null
  const c = msg().sdwebui.connection
  const base = enabled ? c.testSuccessSaveNotice : c.testSuccessSaveAndConnectNotice
  if (!isLoopbackUrl(trimmedUrl) && !confirmNonLoopback) return base + c.testSuccessNonLoopbackSuffix
  return base
}

/** API のエラーなら、サーバーの文(翻訳済み)をそのまま出す。それ以外は `fallback`。 */
export function errorMessageOf(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}
