import { describe, expect, it } from 'vitest'
import { clampTextareaHeight } from './textareaSizing'

describe('clampTextareaHeight', () => {
  it('範囲内ならそのまま', () => {
    expect(clampTextareaHeight(100, 60, 300)).toBe(100)
  })

  it('最小未満なら最小に引き上げる', () => {
    expect(clampTextareaHeight(20, 60, 300)).toBe(60)
  })

  it('最大超過なら最大に切り詰める', () => {
    expect(clampTextareaHeight(500, 60, 300)).toBe(300)
  })

  it('ちょうど最小/最大でもそのまま', () => {
    expect(clampTextareaHeight(60, 60, 300)).toBe(60)
    expect(clampTextareaHeight(300, 60, 300)).toBe(300)
  })

  it('最小が最大より大きい異常値では最小を返す', () => {
    expect(clampTextareaHeight(100, 400, 300)).toBe(400)
  })
})
