import { describe, expect, it } from 'vitest'
import {
  inputCountRequirementMessage,
  isInputCountSatisfied,
  isMaskSatisfied,
  isMaskSupported,
  isOperationSupported,
  isProviderUsable,
  providerUnavailableMessage,
} from './submitChecks'
import type { OperationCapabilities, ProviderEntry } from '../../api/client'

function opCaps(overrides: Partial<OperationCapabilities> = {}): OperationCapabilities {
  return {
    operation: 'edit',
    params: [],
    max_input_images: 1,
    min_input_images: 1,
    supports_mask: true,
    requires_mask: false,
    ...overrides,
  }
}

const sizeConstraints = {
  multiple_of: 16,
  max_long_edge: 3840,
  min_total_pixels: 655_360,
  max_total_pixels: 8_294_400,
  min_aspect_ratio: 1 / 3,
  max_aspect_ratio: 3,
  allow_auto: true,
  round_down: false,
}

function providerEntry(overrides: Partial<ProviderEntry> = {}): ProviderEntry {
  return {
    provider: 'comfyui',
    label: 'ComfyUI',
    models: [],
    default_model: '',
    default_size: null,
    size: sizeConstraints,
    incompatible_pairs: [],
    conditional_params: [],
    prompt_max_length: 32_000,
    n_min: 1,
    n_max: 10,
    partial_images_min: 0,
    partial_images_max: 3,
    max_input_image_bytes: 1,
    max_mask_bytes: 1,
    max_input_images: 1,
    available: true,
    unavailable_reason: null,
    requires_api_key: false,
    supports_pricing: false,
    ...overrides,
  }
}

describe('isOperationSupported(operation が合わないと送信が止まる)', () => {
  it('モデルがその operation の定義を持てば true', () => {
    expect(isOperationSupported(opCaps())).toBe(true)
  })

  it('選んだ operation の定義が無ければ false(例: edit しか無いワークフローで generate)', () => {
    expect(isOperationSupported(undefined)).toBe(false)
  })
})

describe('isInputCountSatisfied / inputCountRequirementMessage(ちょうどN枚のワークフロー)', () => {
  it('min===max>1 のとき、足りなければ false で理由を返す', () => {
    const caps = opCaps({ min_input_images: 2, max_input_images: 2 })
    expect(isInputCountSatisfied(caps, 'edit', 1)).toBe(false)
    expect(inputCountRequirementMessage(caps, 'edit', 1)).toBe('入力画像が2枚必要です(現在1枚)')
  })

  it('min===max>1 でちょうど足りていれば true', () => {
    const caps = opCaps({ min_input_images: 2, max_input_images: 2 })
    expect(isInputCountSatisfied(caps, 'edit', 2)).toBe(true)
    expect(inputCountRequirementMessage(caps, 'edit', 2)).toBeNull()
  })

  it('min<=1(OpenAI/Fake 等)のときは枚数が幅を持つので常に満たす', () => {
    const caps = opCaps({ min_input_images: 1, max_input_images: 16 })
    expect(isInputCountSatisfied(caps, 'edit', 1)).toBe(true)
  })

  it('min!==max(幅がある)のときは対象外', () => {
    const caps = opCaps({ min_input_images: 2, max_input_images: 3 })
    expect(isInputCountSatisfied(caps, 'edit', 2)).toBe(true)
  })

  it('operation が generate(0枚)のときは対象外', () => {
    const caps = opCaps({ min_input_images: 2, max_input_images: 2 })
    expect(isInputCountSatisfied(caps, 'generate', 0)).toBe(true)
  })

  it('opCaps が無ければ(operation 非対応側で別途止まるので)true', () => {
    expect(isInputCountSatisfied(undefined, 'edit', 1)).toBe(true)
  })
})

describe('isMaskSatisfied(requires_mask でマスク無しは止まる)', () => {
  it('requires_mask=false ならマスクが無くても true', () => {
    expect(isMaskSatisfied(opCaps({ requires_mask: false }), false)).toBe(true)
  })

  it('requires_mask=true でマスクが無ければ false', () => {
    expect(isMaskSatisfied(opCaps({ requires_mask: true }), false)).toBe(false)
  })

  it('requires_mask=true でマスクがあれば true', () => {
    expect(isMaskSatisfied(opCaps({ requires_mask: true }), true)).toBe(true)
  })

  it('opCaps 自体が無ければ(operation 非対応側で別途止まるので)true', () => {
    expect(isMaskSatisfied(undefined, false)).toBe(true)
  })
})

describe('isMaskSupported(supports_mask=false のモデルにマスク入力があると止まる)', () => {
  it('マスクが無ければ supports_mask=false でも true', () => {
    expect(isMaskSupported(opCaps({ supports_mask: false }), false)).toBe(true)
  })

  it('supports_mask=false でマスクがあれば false(例: ComfyUI の mask 非対応ワークフロー)', () => {
    expect(isMaskSupported(opCaps({ supports_mask: false }), true)).toBe(false)
  })

  it('supports_mask=true ならマスクがあっても true', () => {
    expect(isMaskSupported(opCaps({ supports_mask: true }), true)).toBe(true)
  })

  it('opCaps 自体が無ければ(operation 非対応側で別途止まるので)true', () => {
    expect(isMaskSupported(undefined, true)).toBe(true)
  })
})

describe('isProviderUsable / providerUnavailableMessage(available=false は止まる)', () => {
  it('available=true なら使える', () => {
    expect(isProviderUsable(providerEntry({ available: true }))).toBe(true)
    expect(providerUnavailableMessage(providerEntry({ available: true }))).toBeNull()
  })

  it('available=false なら使えず、理由を返す', () => {
    const entry = providerEntry({ available: false, unavailable_reason: '接続できません' })
    expect(isProviderUsable(entry)).toBe(false)
    expect(providerUnavailableMessage(entry)).toBe('接続できません')
  })

  it('unavailable_reason が無くても既定の文言を返す', () => {
    const entry = providerEntry({ available: false, unavailable_reason: null })
    expect(providerUnavailableMessage(entry)).toBe('このプロバイダーには接続できません')
  })

  it('プロバイダーが未解決(読み込み中)なら妨げない', () => {
    expect(isProviderUsable(undefined)).toBe(true)
    expect(providerUnavailableMessage(undefined)).toBeNull()
  })
})
