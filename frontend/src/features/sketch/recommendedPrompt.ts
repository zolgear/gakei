/**
 * OpenAI の画像プロンプトガイド(Sketch-to-render の例。ADR-0010 参照)の推奨プロンプト。
 * スケッチエディタの「推奨プロンプトを挿入」で使う。文言自体は言語ごとの辞書
 * (`i18n/locales/{ja,en}.json`)に持ち、ここでは現在の言語のものを返す。
 */
import { msg } from '../../i18n'

export function recommendedSketchPrompt(mode: 'render' | 'markup' = 'render'): string {
  // 線を印で送るとき(ADR-0010、2026-11-05 追記)は、Image API のガイドの使い方に合わせ、印の場所の
  // 変更と、保つものを書く。
  return mode === 'markup' ? msg().sketch.recommendedMarkupPrompt : msg().sketch.recommendedPrompt
}
