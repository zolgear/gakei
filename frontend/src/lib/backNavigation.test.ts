import { describe, expect, it } from 'vitest'
import { resolveBackDestination } from './backNavigation'

describe('resolveBackDestination', () => {
  it('直接開いた/リロード直後(key === "default")は fallback へ置き換える', () => {
    expect(resolveBackDestination('default', '/')).toEqual({ type: 'replace', path: '/' })
  })

  it('アプリ内の履歴がある場合は実際に一つ戻る', () => {
    expect(resolveBackDestination('abc123', '/')).toEqual({ type: 'back' })
  })

  it('fallbackPath はそのまま渡される', () => {
    expect(resolveBackDestination('default', '/lineage/xyz')).toEqual({
      type: 'replace',
      path: '/lineage/xyz',
    })
  })
})
