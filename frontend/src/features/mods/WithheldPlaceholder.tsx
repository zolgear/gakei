/**
 * 「画像なし(拒否)」の置き換えの表示(ADR-0051 4章「画像を残さない」・5章「画面」)。
 *
 * Mod の `deny` の判定で原本を残さなかった Asset は、`/content` が 410 を返す。画像を出す場所では
 * `<img>` を読みに行かず(410 を何度も取りに行かないため)、これを代わりに置く。
 *
 * - 中身は、斜線の入った画像のアイコンと「画像なし(拒否)」の短い文。広ければ判定の `label` も出す。
 * - 大きさは置き場所が決める。`<img>` の代わりに置くときは、その `<img>` の className と style を
 *   そのまま渡す(寸法の CSS を崩さないため)。中身はコンテナクエリで出し分け、狭いサムネイルでは
 *   アイコンだけにする。
 * - `canvas` はビューア・結果エリアなどの大きな枠向け(文字を大きくし、`message` も出す)。
 */
import type { CSSProperties } from 'react'
import { useI18n, fmt } from '../../i18n'
import styles from './Withheld.module.css'

/** 斜線の入った画像: 画像なし。 */
export function ImageWithheldIcon({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M13.5 9.5v3a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h3" />
      <path d="M2.5 11l3-3 2 2" />
      <path d="M10 2.5h3.5V6" />
      <path d="M2 14 14 2" />
    </svg>
  )
}

export interface WithheldPlaceholderProps {
  /** 判定の `label`(広いときだけ出す)。 */
  label?: string | null
  /** 判定の `message`(`canvas` のときだけ出す)。 */
  message?: string | null
  variant?: 'thumb' | 'canvas'
  className?: string
  style?: CSSProperties
}

export function WithheldPlaceholder({ label, message, variant = 'thumb', className, style }: WithheldPlaceholderProps) {
  const { t } = useI18n()
  const m = t.mods
  const title = label ? fmt(m.withheldWithLabel, { label }) : m.withheldDescription
  // 画像の代わりなので、読み上げには <img> の alt に当たる説明を1つ渡す(中の文字は隠す)。
  return (
    <span
      role="img"
      aria-label={title}
      title={title}
      className={`${styles.placeholder} ${className ?? ''}`}
      style={style}
      data-variant={variant}
      data-withheld="true"
    >
      <span className={styles.inner} aria-hidden="true">
        <span className={styles.icon}>
          <ImageWithheldIcon size={variant === 'canvas' ? 32 : 16} />
        </span>
        <span className={styles.text}>{m.withheld}</span>
        {label && <span className={styles.label}>{label}</span>}
        {variant === 'canvas' && message && <span className={styles.message}>{message}</span>}
      </span>
    </span>
  )
}
