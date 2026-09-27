/**
 * 他の画像生成ツールや C2PA が画像に埋め込んだ生成メタ情報(`AssetDetail.embedded_meta`、
 * `EmbeddedGenerationMeta`、ADR-0018)を画面表示用に整形する純粋関数群。値は署名の無い
 * 自己申告(誰でも書き換えられる)であり、GAKEI は検証しない(`verified` は常に false)。
 * フォームへは読み込まない(ADR-0018 4章)ため、`originRecipe.ts` と違い
 * `RunFormState` への変換は持たない。
 */
import type { EmbeddedGenerationMeta } from '../../api/client'
import type { Messages } from '../../i18n'

/** これより長いプロンプトは折りたたんで表示する(`OriginRecipeSection` と同じ長さ)。 */
export const PROMPT_COLLAPSE_LENGTH = 80

/**
 * ツール名の表示文字列。`software` が非空で、ツール名(訳文)そのものと違う場合だけ
 * ` · ${software}` を添える(例: 「ComfyUI · ComfyUI-portable-0.1」)。
 */
export function describeTool(meta: EmbeddedGenerationMeta, messages: Messages): string {
  const toolLabel = messages.lineage.embeddedMeta.tools[meta.tool]
  const software = meta.software
  if (typeof software === 'string' && software !== '' && software !== toolLabel) {
    return `${toolLabel} · ${software}`
  }
  return toolLabel
}

/**
 * 設定一覧(`seed` + `params`、挿入順)。`seed` があれば先頭に `['seed', ...]` を置く。
 * `params` の値は表示用にすべて文字列化する。
 */
export function paramEntries(meta: EmbeddedGenerationMeta): Array<[string, string]> {
  const entries: Array<[string, string]> = []
  if (meta.seed !== null && meta.seed !== undefined) {
    entries.push(['seed', String(meta.seed)])
  }
  for (const [key, value] of Object.entries(meta.params ?? {})) {
    entries.push([key, String(value)])
  }
  return entries
}

/** 元のテキストチャンクや EXIF の項目(折りたたみ表示用)。 */
export function rawEntries(meta: EmbeddedGenerationMeta): Array<[string, string]> {
  return Object.entries(meta.raw ?? {})
}

/**
 * C2PA のマニフェストはあるが、claim generator も設定も読み取れなかった(構造が読めない、
 * または対応していない形式だった)ことを示す。
 */
export function isC2paUnknown(meta: EmbeddedGenerationMeta): boolean {
  return meta.tool === 'c2pa' && !meta.software && Object.keys(meta.params ?? {}).length === 0
}
