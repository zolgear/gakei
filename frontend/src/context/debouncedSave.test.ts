import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createDebouncedSave } from './debouncedSave'

describe('createDebouncedSave', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('最後の値だけを、待ってから保存する', () => {
    const save = vi.fn()
    const saver = createDebouncedSave<string>(save, 300)
    saver.schedule('a')
    saver.schedule('ab')
    expect(save).not.toHaveBeenCalled()
    vi.advanceTimersByTime(300)
    expect(save).toHaveBeenCalledTimes(1)
    expect(save).toHaveBeenCalledWith('ab')
  })

  it('flush は待っている保存を前倒しする', () => {
    const save = vi.fn()
    const saver = createDebouncedSave<string>(save, 300)
    saver.schedule('a cat')
    saver.flush()
    expect(save).toHaveBeenCalledWith('a cat')
    vi.advanceTimersByTime(300)
    expect(save).toHaveBeenCalledTimes(1)
  })

  it('待っている保存が無ければ flush は何も書かない(別のタブが保存した値を上書きしない)', () => {
    const save = vi.fn()
    const saver = createDebouncedSave<string>(save, 300)
    saver.flush()
    expect(save).not.toHaveBeenCalled()
    saver.schedule('a cat')
    vi.advanceTimersByTime(300)
    saver.flush()
    expect(save).toHaveBeenCalledTimes(1)
  })

  it('cancel は待っている保存を捨てる', () => {
    const save = vi.fn()
    const saver = createDebouncedSave<string>(save, 300)
    saver.schedule('a cat')
    saver.cancel()
    vi.advanceTimersByTime(300)
    saver.flush()
    expect(save).not.toHaveBeenCalled()
  })
})
