import { describe, expect, it } from 'vitest'
import type { ModVerdictView, RunModInvocationView } from '../../api/client'
import {
  checkTone,
  formatCheckDuration,
  formatVerdictScore,
  groupInvocationsByMod,
  hasDenyVerdict,
  hasFlagVerdict,
  isSendBlocked,
  isWithheld,
  safeExternalUrl,
} from './modVerdicts'

function verdict(v: ModVerdictView['verdict'], extra: Partial<ModVerdictView> = {}): ModVerdictView {
  return {
    id: `v-${v}`,
    mod_kind: 'webhook',
    mod_id: 'w1',
    mod_name: 'Checker',
    verdict: v,
    propagated: false,
    created_at: '2026-10-16T00:00:00Z',
    ...extra,
  }
}

function invocation(extra: Partial<RunModInvocationView>): RunModInvocationView {
  return {
    id: 'i',
    mod_kind: 'webhook',
    mod_id: 'w1',
    mod_name: 'Checker',
    event: 'ai.before_send',
    purpose: 'image_run',
    outcome: 'allow',
    duration_ms: 12,
    created_at: '2026-10-16T00:00:00Z',
    ...extra,
  }
}

describe('判定の有無', () => {
  it('画像なしは content_withheld だけで決める', () => {
    expect(isWithheld({ content_withheld: true })).toBe(true)
    expect(isWithheld({ content_withheld: false })).toBe(false)
    expect(isWithheld(undefined)).toBe(false)
  })

  it('deny と flag は Mod ごとの最新の判定のどれかで決める', () => {
    const asset = { verdicts: [verdict('ok'), verdict('flag')] }
    expect(hasFlagVerdict(asset)).toBe(true)
    expect(hasDenyVerdict(asset)).toBe(false)
    expect(hasDenyVerdict({ verdicts: [verdict('deny')] })).toBe(true)
    expect(hasFlagVerdict({ verdicts: undefined })).toBe(false)
  })

  it('入力に使えないのは、画像なしか deny の判定があるもの(flag は止めない)', () => {
    expect(isSendBlocked({ content_withheld: true })).toBe(true)
    expect(isSendBlocked({ content_withheld: false, verdicts: [verdict('deny')] })).toBe(true)
    expect(isSendBlocked({ content_withheld: false, verdicts: [verdict('flag')] })).toBe(false)
    expect(isSendBlocked(undefined)).toBe(false)
  })
})

describe('formatVerdictScore', () => {
  it('0〜1 を百分率にする(小数1桁まで)', () => {
    expect(formatVerdictScore(0.873)).toBe('87.3%')
    expect(formatVerdictScore(1)).toBe('100%')
    expect(formatVerdictScore(0)).toBe('0%')
  })

  it('範囲外は丸め、数でなければ出さない', () => {
    expect(formatVerdictScore(1.4)).toBe('100%')
    expect(formatVerdictScore(-0.2)).toBe('0%')
    expect(formatVerdictScore(null)).toBeNull()
    expect(formatVerdictScore(undefined)).toBeNull()
    expect(formatVerdictScore(Number.NaN)).toBeNull()
  })
})

describe('safeExternalUrl', () => {
  it('http と https だけをリンクにする', () => {
    expect(safeExternalUrl('https://example.com/case/1')).toBe('https://example.com/case/1')
    expect(safeExternalUrl('http://intra.example/x')).toBe('http://intra.example/x')
    expect(safeExternalUrl('javascript:alert(1)')).toBeNull()
    expect(safeExternalUrl('data:text/html,hi')).toBeNull()
    expect(safeExternalUrl('not a url')).toBeNull()
    expect(safeExternalUrl(null)).toBeNull()
  })
})

describe('checkTone', () => {
  it('結果を許可・拒否・失敗・飛ばしたに分ける(失敗と時間切れはまとめて失敗)', () => {
    expect(checkTone('allow')).toBe('allow')
    expect(checkTone('deny')).toBe('deny')
    expect(checkTone('error')).toBe('failed')
    expect(checkTone('timeout')).toBe('failed')
    expect(checkTone('skipped')).toBe('skipped')
  })
})

describe('groupInvocationsByMod', () => {
  it('Mod ごとにまとめ、最初に呼ばれた順に並べる', () => {
    const groups = groupInvocationsByMod([
      invocation({ id: '1', mod_id: 'a', mod_name: 'A' }),
      invocation({ id: '2', mod_kind: 'python', mod_id: 'b', mod_name: 'B' }),
      invocation({ id: '3', mod_id: 'a', mod_name: 'A', outcome: 'deny' }),
    ])
    expect(groups.map((g) => [g.modName, g.items.map((i) => i.id)])).toEqual([
      ['A', ['1', '3']],
      ['B', ['2']],
    ])
  })

  it('同じ id でも種類が違えば別の Mod', () => {
    const groups = groupInvocationsByMod([
      invocation({ id: '1', mod_kind: 'webhook', mod_id: 'x' }),
      invocation({ id: '2', mod_kind: 'python', mod_id: 'x' }),
    ])
    expect(groups).toHaveLength(2)
  })
})

describe('formatCheckDuration', () => {
  it('1 秒未満は ms、それ以上は秒', () => {
    expect(formatCheckDuration(12.4)).toBe('12 ms')
    expect(formatCheckDuration(1530)).toBe('1.5 s')
    expect(formatCheckDuration(-1)).toBe('-')
  })
})
