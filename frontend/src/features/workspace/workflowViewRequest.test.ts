import { describe, expect, it, vi } from 'vitest'
import { requestWorkflowView, subscribeWorkflowView } from './workflowViewRequest'

describe('workflowViewRequest', () => {
  it('購読している間だけ合図が届く', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeWorkflowView(listener)
    requestWorkflowView()
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
    requestWorkflowView()
    expect(listener).toHaveBeenCalledTimes(1)
  })
})
