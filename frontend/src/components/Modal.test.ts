import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { msg } from '../i18n'
import { Modal } from './Modal'
import {
  FOCUSABLE_SELECTOR,
  collectFocusable,
  handleModalKeyDown,
  initialFocusTarget,
  nextFocusTarget,
  restoreFocus,
} from './modalFocus'

// 画面のテストの仕組み(jsdom)は入れていないので、描いた HTML の属性と、
// フォーカス管理の規則(`modalFocus.ts`)を偽の要素に対して確かめる。

interface FakeInit {
  name: string
  /** 表示されていない(矩形を持たない)要素。 */
  hidden?: boolean
  /** `disabled` を性質として持つ(button / input など)。 */
  disabledProp?: boolean
  /** `disabled` を属性として持つ。 */
  disabledAttr?: boolean
  tabindex?: string
  isConnected?: boolean
}

class FakeElement {
  readonly name: string
  readonly attrs: Record<string, string> = {}
  readonly disabled?: boolean
  readonly hidden: boolean
  isConnected: boolean
  focusCount = 0

  constructor(init: FakeInit) {
    this.name = init.name
    this.hidden = init.hidden ?? false
    this.isConnected = init.isConnected ?? true
    if (init.disabledProp) this.disabled = true
    if (init.disabledAttr) this.attrs.disabled = ''
    if (init.tabindex !== undefined) this.attrs.tabindex = init.tabindex
  }

  focus() {
    this.focusCount += 1
  }

  getClientRects() {
    return this.hidden ? [] : [{}]
  }

  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null
  }

  hasAttribute(name: string): boolean {
    return name in this.attrs
  }
}

/** パネルの偽物。`querySelectorAll` はセレクターに当たる候補として `children` をそのまま返す。 */
class FakePanel extends FakeElement {
  children: FakeElement[] = []

  constructor(children: FakeElement[] = []) {
    super({ name: 'panel' })
    this.children = children
  }

  querySelectorAll(): FakeElement[] {
    return this.children
  }
}

/** 本文を持つパネル。初期フォーカスが本文を優先することを確かめるために使う。 */
class FakePanelWithBody extends FakePanel {
  body: FakePanel | null

  constructor(children: FakeElement[], body: FakePanel | null) {
    super(children)
    this.body = body
  }

  querySelector(): FakePanel | null {
    return this.body
  }
}

function el(init: FakeInit | string): FakeElement {
  return new FakeElement(typeof init === 'string' ? { name: init } : init)
}

function asPanel(panel: FakePanel): HTMLElement {
  return panel as unknown as HTMLElement
}

function asEl(element: FakeElement): HTMLElement {
  return element as unknown as HTMLElement
}

function keyEvent(key: string, shiftKey = false) {
  return { key, shiftKey, preventDefault: vi.fn() }
}

/** `children` を props で渡すのを lint が嫌うので、組み立てた props を渡して描く。 */
function renderModal(open: boolean): string {
  const props = { open, title: 'タイトル', onClose: () => {}, children: null }
  return renderToStaticMarkup(createElement(Modal, props))
}

describe('Modal markup', () => {
  it('フォーカスを受けられる dialog のパネルを描く', () => {
    const html = renderModal(true)
    expect(html).toContain('role="dialog"')
    expect(html).toContain('aria-modal="true"')
    // 操作できる要素が1つも無いときの受け皿として、パネル自身がフォーカスを受ける。
    expect(html).toMatch(/role="dialog"[^>]*tabindex="-1"|tabindex="-1"[^>]*role="dialog"/)
    expect(html).toContain(`aria-label="${msg().common.close}"`)
  })

  it('open が false なら何も描かない', () => {
    expect(renderModal(false)).toBe('')
  })
})

describe('FOCUSABLE_SELECTOR', () => {
  it('操作できる見込みのある要素を拾う', () => {
    for (const part of ['a[href]', 'button', 'input', 'select', 'textarea', '[tabindex]']) {
      expect(FOCUSABLE_SELECTOR).toContain(part)
    }
  })
})

describe('collectFocusable', () => {
  it('disabled・tabindex="-1"・表示されていない要素を除いて文書順で返す', () => {
    const panel = new FakePanel([
      el('close'),
      el({ name: 'disabledProp', disabledProp: true }),
      el({ name: 'disabledAttr', disabledAttr: true }),
      el({ name: 'skipped', tabindex: '-1' }),
      el({ name: 'collapsed', hidden: true }),
      el({ name: 'ordered', tabindex: '0' }),
      el('save'),
    ])
    expect(collectFocusable(asPanel(panel)).map((e) => (e as unknown as FakeElement).name)).toEqual([
      'close',
      'ordered',
      'save',
    ])
  })
})

describe('initialFocusTarget', () => {
  it('中の最初の操作できる要素を返す', () => {
    const first = el('close')
    const panel = new FakePanel([el({ name: 'hidden', hidden: true }), first, el('save')])
    expect(initialFocusTarget(asPanel(panel))).toBe(asEl(first))
  })

  it('操作できる要素が1つも無ければパネル自身を返す', () => {
    const panel = new FakePanel([el({ name: 'skipped', tabindex: '-1' })])
    expect(initialFocusTarget(asPanel(panel))).toBe(asPanel(panel))
  })

  it('本文の最初の操作できる要素を、ヘッダーの閉じるボタンより優先する', () => {
    const close = el('close')
    const nameInput = el('name')
    const body = new FakePanel([nameInput, el('save')])
    const panel = new FakePanelWithBody([close, nameInput, el('save')], body)
    expect(initialFocusTarget(asPanel(panel))).toBe(asEl(nameInput))
  })

  it('本文に操作できる要素が無ければ、パネル全体の先頭(閉じるボタン)に戻る', () => {
    const close = el('close')
    const body = new FakePanel([el({ name: 'text', tabindex: '-1' })])
    const panel = new FakePanelWithBody([close], body)
    expect(initialFocusTarget(asPanel(panel))).toBe(asEl(close))
  })
})

describe('nextFocusTarget', () => {
  const first = el('first')
  const middle = el('middle')
  const last = el('last')
  const panel = new FakePanel([first, middle, last])

  it('最後の要素で Tab なら最初へ回す', () => {
    expect(nextFocusTarget(asPanel(panel), asEl(last), false)).toBe(asEl(first))
  })

  it('最初の要素で Shift+Tab なら最後へ回す', () => {
    expect(nextFocusTarget(asPanel(panel), asEl(first), true)).toBe(asEl(last))
  })

  it('途中の要素ならブラウザの既定に任せる', () => {
    expect(nextFocusTarget(asPanel(panel), asEl(middle), false)).toBeNull()
    expect(nextFocusTarget(asPanel(panel), asEl(middle), true)).toBeNull()
  })

  it('フォーカスがダイアログの外にあるときは中へ引き戻す', () => {
    const outside = el('outside')
    expect(nextFocusTarget(asPanel(panel), asEl(outside), false)).toBe(asEl(first))
    expect(nextFocusTarget(asPanel(panel), asEl(outside), true)).toBe(asEl(last))
    expect(nextFocusTarget(asPanel(panel), null, false)).toBe(asEl(first))
  })

  it('パネル自身にフォーカスがあるときも中へ引き戻す', () => {
    expect(nextFocusTarget(asPanel(panel), asPanel(panel), false)).toBe(asEl(first))
  })

  it('操作できる要素が1つも無ければパネル自身に留める', () => {
    const empty = new FakePanel([])
    expect(nextFocusTarget(asPanel(empty), asPanel(empty), false)).toBe(asPanel(empty))
  })

  it('Tab のたびに集め直すので、開いている間に増えた要素も対象になる', () => {
    const a = el('a')
    const b = el('b')
    const dynamic = new FakePanel([a, b])
    expect(nextFocusTarget(asPanel(dynamic), asEl(b), false)).toBe(asEl(a))
    // 折りたたみが開いて要素が増えた。
    const c = el('c')
    dynamic.children = [a, b, c]
    expect(nextFocusTarget(asPanel(dynamic), asEl(b), false)).toBeNull()
    expect(nextFocusTarget(asPanel(dynamic), asEl(c), false)).toBe(asEl(a))
  })
})

describe('handleModalKeyDown', () => {
  it('Escape で onClose を呼ぶ', () => {
    const onClose = vi.fn()
    const panel = new FakePanel([el('close')])
    const event = keyEvent('Escape')
    handleModalKeyDown(event, asPanel(panel), null, onClose)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(event.preventDefault).not.toHaveBeenCalled()
  })

  it('パネルがまだ無くても Escape は効く', () => {
    const onClose = vi.fn()
    handleModalKeyDown(keyEvent('Escape'), null, null, onClose)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Tab は回す先へフォーカスを移し、既定の移動を止める', () => {
    const onClose = vi.fn()
    const first = el('first')
    const last = el('last')
    const panel = new FakePanel([first, last])
    const event = keyEvent('Tab')
    handleModalKeyDown(event, asPanel(panel), asEl(last), onClose)
    expect(first.focusCount).toBe(1)
    expect(event.preventDefault).toHaveBeenCalledTimes(1)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('途中の要素での Tab は止めない', () => {
    const panel = new FakePanel([el('first'), el('middle'), el('last')])
    const event = keyEvent('Tab')
    handleModalKeyDown(event, asPanel(panel), asEl(panel.children[1]), vi.fn())
    expect(event.preventDefault).not.toHaveBeenCalled()
  })

  it('Escape と Tab 以外のキーには何もしない', () => {
    const onClose = vi.fn()
    const first = el('first')
    const panel = new FakePanel([first])
    const event = keyEvent('Enter')
    handleModalKeyDown(event, asPanel(panel), null, onClose)
    expect(onClose).not.toHaveBeenCalled()
    expect(event.preventDefault).not.toHaveBeenCalled()
    expect(first.focusCount).toBe(0)
  })
})

describe('restoreFocus', () => {
  it('開く前にフォーカスがあった要素へ戻す', () => {
    const opener = el('opener')
    restoreFocus(asEl(opener))
    expect(opener.focusCount).toBe(1)
  })

  it('その要素が既に DOM から外れていれば何もしない(例外にもしない)', () => {
    const removed = el({ name: 'removed', isConnected: false })
    expect(() => restoreFocus(asEl(removed))).not.toThrow()
    expect(removed.focusCount).toBe(0)
  })

  it('戻す先が無くても例外にしない', () => {
    expect(() => restoreFocus(null)).not.toThrow()
    expect(() => restoreFocus(undefined)).not.toThrow()
  })
})
