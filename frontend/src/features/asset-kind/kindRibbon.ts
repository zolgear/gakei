/**
 * タイルの左下の隅に置く種別のリボン(三角の印)の見せ方を、Asset の種別から決める。
 * 純粋な判定にして `AssetKindRibbon` から切り出す(テストのため)。
 *
 * - 大半は生成した画像なので、生成(`generated`)には何も付けない。付くのは生成以外のものだけ。
 * - 系列の取り込み(ADR-0037)で入った、取り込んだ Run の生成画像(`imported`)は「取り込み」。
 *   記録は ZIP を信じるしかない未検証のもので、自分で生成したものと見分けたい。
 * - アップロード(`upload`)は「アップロード」。
 * - 手で作った画像(スケッチ・配置・マスク・領域)は1つの色とアイコンにまとめる。どれもアプリの中で
 *   人が描いた・並べたもので、タイルの小さな印で見分ける必要は薄い。文言(読み上げ・ツールチップ)は
 *   種別ごとの名前にする。
 */
export type KindRibbonTone = 'upload' | 'handmade' | 'imported'

export interface KindRibbon {
  tone: KindRibbonTone
  /**
   * 文言のキー。`imported` は「取り込んだ画像(未検証)」、それ以外は `common.assetKindLabels` の種別。
   */
  label: 'imported' | 'upload' | 'sketch' | 'composite' | 'mask' | 'regions'
}

const HANDMADE_KINDS = new Set(['sketch', 'composite', 'mask', 'regions'])

export function kindRibbon(kind: string, imported = false): KindRibbon | null {
  if (kind === 'generated') return imported ? { tone: 'imported', label: 'imported' } : null
  if (kind === 'upload') return { tone: 'upload', label: 'upload' }
  if (HANDMADE_KINDS.has(kind)) return { tone: 'handmade', label: kind as KindRibbon['label'] }
  return null
}
