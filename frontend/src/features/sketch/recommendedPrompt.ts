/**
 * OpenAI の画像プロンプトガイド(Sketch-to-render の例。ADR-0010 参照)の推奨プロンプト。
 * スケッチエディタの「推奨プロンプトを挿入」で使う。文言自体は言語ごとの辞書
 * (`i18n/locales/{ja,en}.json`)に持ち、ここでは現在の言語のものを返す。
 */
import { msg } from '../../i18n'

export function recommendedSketchPrompt(): string {
  return msg().sketch.recommendedPrompt
}
