import { afterEach, describe, expect, it } from 'vitest'
import { messagesFor, setLocale } from '../../i18n'
import { describeError } from './errorMessages'

describe('describeError', () => {
  afterEach(() => setLocale('ja'))

  it('Mod の送る前のチェックで止まった Run(hook_denied)は、見出しに理由を添える', () => {
    setLocale('ja')
    expect(describeError('hook_denied', '社内の検査 が送信を止めました: 禁止語')).toBe(
      '送る前のチェックで止められました: 社内の検査 が送信を止めました: 禁止語',
    )
    setLocale('en')
    expect(describeError('hook_denied', null)).toBe('Stopped by a pre-send check')
  })

  it('知らないコードはサーバーの文をそのまま出す', () => {
    expect(describeError('something_new', '詳細')).toBe('詳細')
  })

  it('既知のコードは今の言語の見出しを出す', () => {
    setLocale('en')
    expect(describeError('contentFilter', 'blocked')).toBe(messagesFor('en').runStatus.errorCodes.contentFilter)
  })
})

