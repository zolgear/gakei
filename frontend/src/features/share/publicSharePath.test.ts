import { describe, expect, it } from 'vitest'
import { publicShareTokenFromPath } from './publicSharePath'

describe('publicShareTokenFromPath', () => {
  it('/s/{トークン} からトークンを取り出す(末尾の / は許す)', () => {
    expect(publicShareTokenFromPath('/s/abcDEF_123-x')).toBe('abcDEF_123-x')
    expect(publicShareTokenFromPath('/s/abc/')).toBe('abc')
  })

  it('それ以外のパスは null(通常の画面として描く)', () => {
    expect(publicShareTokenFromPath('/')).toBeNull()
    expect(publicShareTokenFromPath('/s/')).toBeNull()
    expect(publicShareTokenFromPath('/s/abc/def')).toBeNull()
    expect(publicShareTokenFromPath('/settings')).toBeNull()
    expect(publicShareTokenFromPath('/assets/s/abc')).toBeNull()
    expect(publicShareTokenFromPath('/s/a.b')).toBeNull()
  })
})
