import { describe, expect, it } from 'vitest'
import type { ParamDef } from '../../api/client'
import { fillSeedDefaults } from './seedDefaults'

const seedDef: ParamDef = {
  name: 'seed',
  type: 'int',
  label: 'シード',
  minimum: 0,
  maximum: 1000,
  required: false,
  description: '',
  widget: 'seed',
}

const otherDef: ParamDef = {
  name: 'quality',
  type: 'enum',
  label: '画質',
  choices: ['auto', 'high'],
  required: false,
  description: '',
}

const defs: ParamDef[] = [seedDef, otherDef]

describe('fillSeedDefaults', () => {
  it('固定モードで値が無ければ乱数を埋める', () => {
    const result = fillSeedDefaults(defs, { seed: '', quality: 'auto' }, 'fixed', () => 42)
    expect(result.seed).toBe('42')
    expect(result.quality).toBe('auto')
  })

  it('固定モードで seed キーが無ければ(未指定と同じ扱い)埋める', () => {
    const result = fillSeedDefaults(defs, { quality: 'auto' }, 'fixed', () => 7)
    expect(result.seed).toBe('7')
  })

  it('固定モードで既に値があれば変更しない(同じ設定で再実行・下書き復元)', () => {
    const result = fillSeedDefaults(defs, { seed: '123' }, 'fixed', () => 999)
    expect(result.seed).toBe('123')
  })

  it('固定モードで上限を超える値(別のプロバイダーから持ち込んだもの)は、範囲内の乱数に置き換える', () => {
    const received: number[] = []
    const result = fillSeedDefaults(defs, { seed: '5000' }, 'fixed', (max) => {
      received.push(max)
      return 7
    })
    expect(result.seed).toBe('7')
    expect(received).toEqual([1000])
  })

  it('ランダムモードでは何もしない(空文字列のまま)', () => {
    const result = fillSeedDefaults(defs, { seed: '' }, 'random', () => 999)
    expect(result.seed).toBe('')
  })

  it('ランダムモードで値があってもそのまま残す(同じ設定で再実行直後 等)', () => {
    const result = fillSeedDefaults(defs, { seed: '123' }, 'random', () => 999)
    expect(result.seed).toBe('123')
  })

  it('seed 欄を持たない defs では何もしない', () => {
    const result = fillSeedDefaults([otherDef], { quality: 'auto' }, 'fixed', () => 999)
    expect(result).toEqual({ quality: 'auto' })
  })

  it('変更が無ければ同じ参照を返す', () => {
    const raw = { seed: '123', quality: 'auto' }
    expect(fillSeedDefaults(defs, raw, 'fixed', () => 999)).toBe(raw)
    expect(fillSeedDefaults(defs, raw, 'random', () => 999)).toBe(raw)
  })
})
