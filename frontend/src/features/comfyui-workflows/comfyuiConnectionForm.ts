/**
 * 接続パネル(`ComfyUIStatusPanel.tsx`)、ワークフロー一覧のコンパクトな接続状態表示
 * (`ComfyUIConnectionSummary.tsx`)、ワークフロー一覧・登録編集画面の未接続案内、
 * それぞれの判定を切り出した純粋関数。
 * ループバック判定はサーバー側(`app/providers/registry.py` の `_is_loopback_url`)と
 * 同じ規則(ホストが `localhost`、`127.0.0.0/8`、`::1`)を保存前の画面側でも再現する。
 * 最終判定は常にサーバー(422)なので、ここでの判定は確認チェックを出す/省くための目安。
 */
import type { ComfyUIConnectionTestResponse, ComfyUIStatus } from '../../api/client'
import { fmt, msg } from '../../i18n'

export type ConnectionState = 'disabled' | 'available' | 'unavailable'

/** 接続の3状態(無効/接続できる/接続できない)。パネルとコンパクト表示の両方の色分けに使う。 */
export function connectionState(status: Pick<ComfyUIStatus, 'enabled' | 'available'>): ConnectionState {
  if (!status.enabled) return 'disabled'
  return status.available ? 'available' : 'unavailable'
}

export interface ConnectionSummary {
  state: ConnectionState
  label: string
}

/** `/settings/comfyui` に出す1行分の要約(状態 + 短いラベル)。詳しい操作はパネル側で行う。 */
export function connectionSummary(status: Pick<ComfyUIStatus, 'enabled' | 'available' | 'url'>): ConnectionSummary {
  const t = msg().comfyui.connection
  const state = connectionState(status)
  if (state === 'disabled') return { state, label: t.disconnectedLabel }
  if (state === 'available') return { state, label: fmt(t.connectedLabel, { url: status.url ?? '' }) }
  return { state, label: t.unavailableLabel }
}

export function isLoopbackUrl(url: string): boolean {
  let hostname: string
  try {
    hostname = new URL(url).hostname
  } catch {
    return false
  }
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '')
  if (host === 'localhost' || host === '::1') return true
  const ipv4 = host.match(/^(\d{1,3})\.\d{1,3}\.\d{1,3}\.\d{1,3}$/)
  if (ipv4) return Number(ipv4[1]) === 127
  return false
}

/**
 * 保存ボタンを押せる状態か。ループバック以外は確認チェック(`confirmNonLoopback`)が要る。
 * URL が空、またはループバック判定できない(パース失敗)場合は押せない。
 */
export function canSaveConnection(url: string, confirmNonLoopback: boolean): boolean {
  const trimmed = url.trim()
  if (trimmed === '') return false
  return isLoopbackUrl(trimmed) || confirmNonLoopback
}

/**
 * `source === 'env'` のとき、画面に出す注記。今の接続が `COMFYUI_URL` の既定値であり、
 * この画面で保存・切り離しをすると以降はそちらが優先されることを伝える。
 */
export function envSourceNote(status: Pick<ComfyUIStatus, 'source'>): string | null {
  if (status.source !== 'env') return null
  return msg().comfyui.connection.envNote
}

export function lockedMessage(action: 'save' | 'detach'): string {
  const t = msg().comfyui.connection
  return action === 'save' ? t.lockedSave : t.lockedDetach
}

/**
 * 接続テストは成功したが、まだ「保存して接続」/「保存」を押していないことを伝える案内。
 * 接続テストは設定を変えない(ADR-0013 7章)ので、テストだけで有効になったと誤解しないようにする。
 * フォーム表示中で、テストした URL が今入力中の URL と一致しているときだけ出す
 * (入力を変えると `testMutation.reset()` されるので通常は一致するが、念のため確認する)。
 */
export function testSuccessNotice(params: {
  showForm: boolean
  /** 既に有効(URL の変更中)か。ボタンの文言と、保存で何が起きるかの説明が変わる。 */
  enabled: boolean
  trimmedUrl: string
  testResult: Pick<ComfyUIConnectionTestResponse, 'available' | 'url'> | undefined
  confirmNonLoopback: boolean
}): string | null {
  const { showForm, enabled, trimmedUrl, testResult, confirmNonLoopback } = params
  if (!showForm || !testResult?.available) return null
  if (trimmedUrl === '' || testResult.url !== trimmedUrl) return null

  const t = msg().comfyui.connection
  const base = enabled ? t.testSuccessSaveNotice : t.testSuccessSaveAndConnectNotice
  if (!isLoopbackUrl(trimmedUrl) && !confirmNonLoopback) {
    return base + t.testSuccessNonLoopbackSuffix
  }
  return base
}

/**
 * ワークフロー一覧・登録編集画面(`/settings/comfyui`)で出す未接続の案内。
 * 接続テストのみで保存し忘れた場合と同じく `enabled === false` を根拠にする
 * (`GET /api/comfyui/status` の結果。ADR-0013 7章: 無効時はモデル選択に出ない)。
 */
export function disconnectedNotice(enabled: boolean): string | null {
  if (enabled) return null
  return msg().comfyui.connection.disconnectedNotice
}

/** ワークフロー一覧向け: ワークフローが1件もなければ「未接続」の表示だけで十分なので出さない。 */
export function listDisconnectedNotice(enabled: boolean, workflowCount: number): string | null {
  if (workflowCount === 0) return null
  return disconnectedNotice(enabled)
}

/** 登録・編集画面向け: 未接続の間は analyze の `/object_info` 補完も効かないことを付け加える。 */
export function formDisconnectedNotice(enabled: boolean): string | null {
  const base = disconnectedNotice(enabled)
  if (base === null) return null
  return base + msg().comfyui.connection.disconnectedNoticeFormSuffix
}
