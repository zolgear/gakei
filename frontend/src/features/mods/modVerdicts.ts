/**
 * Mod の判定と呼び出しの記録(ADR-0051 4章・5章)を画面に出すための純粋関数。
 *
 * - 判定(`ModVerdictView`)は API が Mod ごとの最新だけを新しい順に返す。
 * - `deny` の判定がある Asset は、画像が残っていても外部の AI へ送らない(4章「判定の効力」)。
 *   入力に使う前に分かるよう、画面でも同じ条件で止める(サーバーは Run の作成で 409 にする)。
 */
import type { ModVerdictView, RunModInvocationView } from '../../api/client'

export type Verdict = ModVerdictView['verdict']

/** 判定の項目だけを持つもの(AssetDetail など)。 */
export interface WithVerdicts {
  verdicts?: ModVerdictView[] | null
}

/** 画像を残さなかったかの項目だけを持つもの(一覧・詳細・入出力の参照・系列のノード)。 */
export interface WithWithheld {
  content_withheld?: boolean | null
}

export function isWithheld(asset: WithWithheld | null | undefined): boolean {
  return asset?.content_withheld === true
}

/** 最新の判定に `deny` の Mod が1つでもあるか。 */
export function hasDenyVerdict(asset: WithVerdicts | null | undefined): boolean {
  return (asset?.verdicts ?? []).some((v) => v.verdict === 'deny')
}

/** 最新の判定に `flag` の Mod があるか(表示だけで、動作は変えない)。 */
export function hasFlagVerdict(asset: WithVerdicts | null | undefined): boolean {
  return (asset?.verdicts ?? []).some((v) => v.verdict === 'flag')
}

/**
 * 入力に使えない(外部の AI へ送らない)Asset か。画像を残さなかったもの、または最新の判定が
 * `deny` の Mod があるもの(サーバーの `blocked_asset_ids` と同じ条件)。
 */
export function isSendBlocked(asset: (WithWithheld & WithVerdicts) | null | undefined): boolean {
  return isWithheld(asset) || hasDenyVerdict(asset)
}

/** スコア(0〜1)を百分率の文字にする。範囲外・数でないものは出さない。 */
export function formatVerdictScore(score: number | null | undefined): string | null {
  if (typeof score !== 'number' || !Number.isFinite(score)) return null
  const clamped = Math.min(1, Math.max(0, score))
  return `${Math.round(clamped * 1000) / 10}%`
}

/**
 * 外部へのリンクとして出してよい URL か。`http:` と `https:` だけ(`javascript:` などは出さない)。
 */
export function safeExternalUrl(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null
  } catch {
    return null
  }
}

/** 送る前のチェックの結果の見た目の区分。 */
export type CheckTone = 'allow' | 'deny' | 'failed' | 'skipped'

/**
 * 呼び出しの結果(`outcome`)を、許可・拒否・失敗・飛ばしたに分ける。失敗と時間切れは
 * まとめて「失敗」にし、実際に止めたか通したかは `effective` で別に出す。
 */
export function checkTone(outcome: string): CheckTone {
  if (outcome === 'allow' || outcome === 'ok') return 'allow'
  if (outcome === 'deny') return 'deny'
  if (outcome === 'skipped') return 'skipped'
  return 'failed'
}

export interface ModInvocationGroup {
  key: string
  modName: string
  modKind: RunModInvocationView['mod_kind']
  items: RunModInvocationView[]
}

/**
 * 送る前のチェックの記録を Mod ごとにまとめる。並びは最初に呼ばれた順(API は古い順で返す)、
 * 中の記録も古い順のまま。
 */
export function groupInvocationsByMod(invocations: RunModInvocationView[]): ModInvocationGroup[] {
  const groups = new Map<string, ModInvocationGroup>()
  for (const inv of invocations) {
    const key = `${inv.mod_kind}:${inv.mod_id}`
    let group = groups.get(key)
    if (!group) {
      group = { key, modName: inv.mod_name, modKind: inv.mod_kind, items: [] }
      groups.set(key, group)
    }
    group.items.push(inv)
  }
  return [...groups.values()]
}

/** かかった時間を短い文字にする(1 秒未満は ms、それ以上は小数1桁の秒)。 */
export function formatCheckDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '-'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(Math.round(ms / 100) / 10).toFixed(1)} s`
}
