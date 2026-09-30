import { describe, expect, it } from 'vitest'
import { fmt } from '../../i18n'
import en from '../../i18n/locales/en.json'
import ja from '../../i18n/locales/ja.json'
import { sharesCardState, sharesDialogCloseTarget } from './sharesSummary'

describe('sharesCardState', () => {
  it('読み込み中は loading', () => {
    expect(sharesCardState({ isLoading: true, isError: false, itemCount: undefined })).toBe('loading')
  })

  it('0 件なら empty(「一覧を開く」は出さない)', () => {
    expect(sharesCardState({ isLoading: false, isError: false, itemCount: 0 })).toBe('empty')
  })

  it('1 件以上なら list(件数と「一覧を開く」を出す)', () => {
    expect(sharesCardState({ isLoading: false, isError: false, itemCount: 1 })).toBe('list')
    expect(sharesCardState({ isLoading: false, isError: false, itemCount: 3 })).toBe('list')
  })

  it('取得に失敗しデータが無ければ error', () => {
    expect(sharesCardState({ isLoading: false, isError: true, itemCount: undefined })).toBe('error')
  })

  it('再取得に失敗しても、手元のデータがあれば件数を見せ続ける', () => {
    expect(sharesCardState({ isLoading: false, isError: true, itemCount: 2 })).toBe('list')
  })
})

describe('件数の表示', () => {
  it('日本語は単数・複数とも「n 件」', () => {
    expect(fmt(ja.settings.shares.count, { count: 1 })).toBe('1 件')
    expect(fmt(ja.settings.shares.count, { count: 3 })).toBe('3 件')
  })

  it('英語は単数・複数で形を変える', () => {
    expect(fmt(en.settings.shares.count, { count: 1 })).toBe('1 share link')
    expect(fmt(en.settings.shares.count, { count: 3 })).toBe('3 share links')
  })
})

describe('sharesDialogCloseTarget', () => {
  it('取り消しの確認を重ねていなければ、一覧ダイアログを閉じる', () => {
    expect(sharesDialogCloseTarget(false)).toBe('list')
  })

  it('取り消しの確認を重ねているあいだは、内側の確認だけを閉じる', () => {
    expect(sharesDialogCloseTarget(true)).toBe('confirm')
  })
})
