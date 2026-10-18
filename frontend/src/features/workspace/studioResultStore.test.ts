import { afterEach, describe, expect, it } from 'vitest'
import { getStudioResultAssetId, setStudioResultAssetId } from './studioResultStore'

describe('studioResultStore', () => {
  afterEach(() => setStudioResultAssetId(null))

  it('既定は null で、書き込んだ値を返し、null で戻せる', () => {
    expect(getStudioResultAssetId()).toBeNull()
    setStudioResultAssetId('a1')
    expect(getStudioResultAssetId()).toBe('a1')
    setStudioResultAssetId(null)
    expect(getStudioResultAssetId()).toBeNull()
  })
})
