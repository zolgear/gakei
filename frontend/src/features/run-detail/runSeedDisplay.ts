/**
 * ビューア・系列のインスペクター・Run 詳細で、Run の seed を1行で見せるための振り分け。
 * ComfyUI(`comfyui_seed`。ADR-0013)と SD WebUI(`sdwebui_seed` と `usage.all_seeds`。ADR-0038)で
 * 見せ方が違う(ComfyUI は1つの seed からバッチ全体、SD WebUI は1枚ごとに別の seed)。
 * どちらでもない Run は null(何も出さない)。
 */
import { comfyUiSeedTooltip, describeComfyUiSeed, extractComfyUiSeed } from './comfyuiPromptDisplay'
import {
  describeSdWebuiSeed,
  extractSdWebuiAllSeeds,
  extractSdWebuiSeed,
  sdWebuiSeedTooltip,
} from './sdwebuiRunDisplay'

export interface RunSeedSource {
  params?: Record<string, unknown> | null
  usage?: Record<string, unknown> | null
  outputs?: readonly unknown[] | null
}

export interface RunSeedDisplay {
  text: string
  tooltip: string | undefined
}

export function runSeedDisplay(run: RunSeedSource, outputIndex?: number | null): RunSeedDisplay | null {
  const params = run.params ?? {}
  const outputCount = run.outputs?.length ?? 0
  const comfySeed = extractComfyUiSeed(params)
  if (comfySeed !== null) {
    const text = describeComfyUiSeed(comfySeed, outputCount, outputIndex)
    return text ? { text, tooltip: comfyUiSeedTooltip(outputCount) } : null
  }
  if ('sdwebui_seed' in params) {
    const text = describeSdWebuiSeed(
      extractSdWebuiSeed(params),
      extractSdWebuiAllSeeds(run.usage),
      outputCount,
      outputIndex,
    )
    return text ? { text, tooltip: sdWebuiSeedTooltip(outputCount) } : null
  }
  return null
}
