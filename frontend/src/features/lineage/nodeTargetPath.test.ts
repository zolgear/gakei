import { describe, expect, it } from 'vitest'
import { isStudioPath, nodeTargetPath } from './nodeTargetPath'

describe('isStudioPath', () => {
  it('/studio とその配下だけを真にする', () => {
    expect(isStudioPath('/studio')).toBe(true)
    expect(isStudioPath('/studio/')).toBe(true)
    expect(isStudioPath('/studios')).toBe(false)
    expect(isStudioPath('/')).toBe(false)
    expect(isStudioPath('/assets/a')).toBe(false)
  })
})

describe('nodeTargetPath', () => {
  it('スタジオでは Asset を結果エリアに表示する', () => {
    expect(nodeTargetPath({ id: 'a1', type: 'asset' }, '/studio')).toBe('/studio?asset=a1')
  })

  it('スタジオでは Run をその Run の表示に切り替える', () => {
    expect(nodeTargetPath({ id: 'r1', type: 'run' }, '/studio')).toBe('/studio?run=r1')
  })

  it('スタジオ以外では従来どおりビューアと Run 詳細へ移動する', () => {
    expect(nodeTargetPath({ id: 'a1', type: 'asset' }, '/')).toBe('/assets/a1')
    expect(nodeTargetPath({ id: 'r1', type: 'run' }, '/lineage/x')).toBe('/runs/r1')
  })
})
