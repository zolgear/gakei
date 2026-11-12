/**
 * 番兵が見えたら次のページを読む(IntersectionObserver はモック)。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  AUTO_LOAD_ROOT_MARGIN,
  AUTO_RETRY_DELAYS_MS,
  autoRetryDelayMs,
  startAutoLoadObserver,
} from './useAutoLoadMore'

class MockObserver {
  static instances: MockObserver[] = []
  callback: IntersectionObserverCallback
  options?: IntersectionObserverInit
  observed: Element[] = []
  disconnected = false
  constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
    this.callback = callback
    this.options = options
    MockObserver.instances.push(this)
  }
  observe(el: Element) {
    this.observed.push(el)
  }
  disconnect() {
    this.disconnected = true
  }
  fire(isIntersecting: boolean) {
    this.callback([{ isIntersecting } as IntersectionObserverEntry], this as unknown as IntersectionObserver)
  }
}

const root = {} as Element
const sentinel = {} as Element

function start(overrides: Partial<Parameters<typeof startAutoLoadObserver>[0]> = {}) {
  MockObserver.instances = []
  const fetchNextPage = vi.fn()
  const stop = startAutoLoadObserver({
    root,
    sentinel,
    hasNextPage: true,
    isFetchingNextPage: false,
    isFetchNextPageError: false,
    fetchNextPage,
    observerCtor: MockObserver,
    ...overrides,
  })
  return { stop, fetchNextPage, observer: MockObserver.instances[0] }
}

describe('startAutoLoadObserver', () => {
  it('内側のスクロールコンテナを root にし、先読みの余白を付けて番兵を見る', () => {
    const { observer } = start()
    expect(observer.options).toEqual({ root, rootMargin: AUTO_LOAD_ROOT_MARGIN })
    expect(observer.observed).toEqual([sentinel])
  })

  it('番兵が見えたら次のページを読む', () => {
    const { observer, fetchNextPage } = start()
    observer.fire(true)
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
  })

  it('見えていなければ読まない', () => {
    const { observer, fetchNextPage } = start()
    observer.fire(false)
    expect(fetchNextPage).not.toHaveBeenCalled()
  })

  it('1回張るごとに1回だけ読む(次は張り直しで判定する)', () => {
    const { observer, fetchNextPage } = start()
    observer.fire(true)
    observer.fire(true)
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
  })

  it('読み込み中・続きが無い・直前の失敗のときは監視しない', () => {
    expect(start({ isFetchingNextPage: true }).stop).toBeNull()
    expect(start({ hasNextPage: false }).stop).toBeNull()
    expect(start({ isFetchNextPageError: true }).stop).toBeNull()
    expect(MockObserver.instances).toHaveLength(0)
  })

  it('要素がまだ無ければ監視しない', () => {
    expect(start({ root: null }).stop).toBeNull()
    expect(start({ sentinel: null }).stop).toBeNull()
  })

  it('止める関数で監視を外す', () => {
    const { stop, observer } = start()
    stop?.()
    expect(observer.disconnected).toBe(true)
  })

  it('IntersectionObserver の無い環境では何もしない', () => {
    // vitest の環境は node なので、既定の IntersectionObserver は無い。
    MockObserver.instances = []
    const stop = startAutoLoadObserver({
      root,
      sentinel,
      hasNextPage: true,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      fetchNextPage: vi.fn(),
    })
    expect(stop).toBeNull()
  })
})

describe('失敗の後の読み直し', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  function startAfterError(overrides: Partial<Parameters<typeof startAutoLoadObserver>[0]> = {}) {
    vi.useFakeTimers()
    return start({ isFetchNextPageError: true, retryDelayMs: 5000, isPageVisible: () => true, ...overrides })
  }

  it('番兵が見えていれば、待ってから1回だけ読み直す', () => {
    const { observer, fetchNextPage } = startAfterError()
    observer.fire(true)
    vi.advanceTimersByTime(4999)
    expect(fetchNextPage).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
    observer.fire(true)
    vi.advanceTimersByTime(60_000)
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
  })

  it('番兵が見えていなければ読み直さず、見えなくなったら取りやめる', () => {
    const { observer, fetchNextPage } = startAfterError()
    observer.fire(false)
    vi.advanceTimersByTime(60_000)
    expect(fetchNextPage).not.toHaveBeenCalled()
    observer.fire(true)
    vi.advanceTimersByTime(3000)
    observer.fire(false)
    vi.advanceTimersByTime(60_000)
    expect(fetchNextPage).not.toHaveBeenCalled()
    // もう一度見えたら、待ち時間は最初から。
    observer.fire(true)
    vi.advanceTimersByTime(5000)
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
  })

  it('タブが裏にある間は読み直さず、表に戻ってから読む', () => {
    let visible = false
    const { observer, fetchNextPage } = startAfterError({ isPageVisible: () => visible })
    observer.fire(true)
    vi.advanceTimersByTime(20_000)
    expect(fetchNextPage).not.toHaveBeenCalled()
    visible = true
    vi.advanceTimersByTime(5000)
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
  })

  it('止めたら、待っていた読み直しも取りやめる', () => {
    const { stop, observer, fetchNextPage } = startAfterError()
    observer.fire(true)
    stop?.()
    vi.advanceTimersByTime(60_000)
    expect(fetchNextPage).not.toHaveBeenCalled()
  })

  it('間隔は失敗が続くほど長くし、最後の値で止める', () => {
    expect(autoRetryDelayMs(0)).toBe(AUTO_RETRY_DELAYS_MS[0])
    expect(autoRetryDelayMs(1)).toBeGreaterThan(autoRetryDelayMs(0))
    expect(autoRetryDelayMs(99)).toBe(AUTO_RETRY_DELAYS_MS[AUTO_RETRY_DELAYS_MS.length - 1])
  })
})
