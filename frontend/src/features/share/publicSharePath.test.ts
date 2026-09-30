import { describe, expect, it } from 'vitest'
import { buildPublicSharePath, parsePublicSharePath, publicShareTokenFromPath } from './publicSharePath'

const RUN_ID = '0b9a4a3e-6a53-4a6f-9d0e-3f1c2b7d8e90'

describe('publicShareTokenFromPath', () => {
  it('/s/{トークン} からトークンを取り出す(末尾の / は許す)', () => {
    expect(publicShareTokenFromPath('/s/abcDEF_123-x')).toBe('abcDEF_123-x')
    expect(publicShareTokenFromPath('/s/abc/')).toBe('abc')
  })

  it('/s/{トークン}/runs/{run_id} もトークンを取り出す', () => {
    expect(publicShareTokenFromPath(`/s/abc/runs/${RUN_ID}`)).toBe('abc')
  })

  it('それ以外のパスは null(通常の画面として描く)', () => {
    expect(publicShareTokenFromPath('/')).toBeNull()
    expect(publicShareTokenFromPath('/s/')).toBeNull()
    expect(publicShareTokenFromPath('/s/abc/def')).toBeNull()
    expect(publicShareTokenFromPath('/s/abc/runs')).toBeNull()
    expect(publicShareTokenFromPath('/s/abc/runs/')).toBeNull()
    expect(publicShareTokenFromPath('/s/abc/runs/x/y')).toBeNull()
    expect(publicShareTokenFromPath('/s/abc/runs/a_b')).toBeNull()
    expect(publicShareTokenFromPath('/settings')).toBeNull()
    expect(publicShareTokenFromPath('/assets/s/abc')).toBeNull()
    expect(publicShareTokenFromPath('/s/a.b')).toBeNull()
  })
})

describe('parsePublicSharePath', () => {
  it('Run の直リンクを run_id ごと解釈する', () => {
    expect(parsePublicSharePath(`/s/abc/runs/${RUN_ID}`)).toEqual({ token: 'abc', runId: RUN_ID })
    expect(parsePublicSharePath(`/s/abc/runs/${RUN_ID}/`)).toEqual({ token: 'abc', runId: RUN_ID })
    expect(parsePublicSharePath('/s/abc')).toEqual({ token: 'abc', runId: null })
    expect(parsePublicSharePath('/runs/abc')).toBeNull()
  })

  it('組み立てた URL は解釈し直すと元に戻る', () => {
    expect(buildPublicSharePath('abc')).toBe('/s/abc')
    expect(buildPublicSharePath('abc', RUN_ID)).toBe(`/s/abc/runs/${RUN_ID}`)
    expect(parsePublicSharePath(buildPublicSharePath('abc', RUN_ID))).toEqual({ token: 'abc', runId: RUN_ID })
  })
})
