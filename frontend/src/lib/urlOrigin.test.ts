import { describe, expect, it } from 'vitest'
import { originChanges, originOf } from './urlOrigin'

describe('originOf', () => {
  it('scheme・host・port にそろえる', () => {
    expect(originOf('https://API.example.com/v1')).toBe('https://api.example.com:443')
    expect(originOf('http://127.0.0.1:7860/sub')).toBe('http://127.0.0.1:7860')
    expect(originOf('http://localhost')).toBe('http://localhost:80')
  })
  it('読めない URL は null', () => {
    expect(originOf('')).toBeNull()
    expect(originOf('not a url')).toBeNull()
    expect(originOf('ftp://x/y')).toBeNull()
  })
})

describe('originChanges', () => {
  it('パスだけの変更はオリジンが変わらない', () => {
    expect(originChanges('http://127.0.0.1:7860', 'http://127.0.0.1:7860/api')).toBe(false)
    expect(originChanges('https://api.openai.com/v1', 'https://api.openai.com:443/v2')).toBe(false)
  })
  it('ホスト・ポート・スキームのどれかが違えば変わる', () => {
    expect(originChanges('http://127.0.0.1:7860', 'http://127.0.0.1:7861')).toBe(true)
    expect(originChanges('http://127.0.0.1:7860', 'https://127.0.0.1:7860')).toBe(true)
    expect(originChanges('http://127.0.0.1:7860', 'http://other.local:7860')).toBe(true)
  })
  it('入力の途中(読めない URL)では変わらないものとする', () => {
    expect(originChanges('http://127.0.0.1:7860', 'http://')).toBe(false)
  })
  it('今の接続先が無ければ変わるものとする', () => {
    expect(originChanges(null, 'http://127.0.0.1:7860')).toBe(true)
  })
})
