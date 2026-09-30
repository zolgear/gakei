/**
 * 共有のページの Run のパラメーター(ADR-0029 3章、2026-10-01 改訂)。スカラーの値は一覧で、
 * 入れ子を含む値(ComfyUI に送ったグラフ全体など)は折り畳んだ JSON のブロックで出す。
 * 巨大な JSON でページを重くしないよう、既定では畳み、開いたときだけ JSON を組み立てる。
 */
import { useState } from 'react'
import { fmt, useI18n } from '../../i18n'
import { splitPublicParams } from './publicRunDetail'
import styles from './PublicSharePage.module.css'

interface PublicParamsProps {
  params: Record<string, unknown> | null | undefined
}

export function PublicParams({ params }: PublicParamsProps) {
  const { t } = useI18n()
  const p = t.publicShare
  const { scalars, nested } = splitPublicParams(params)
  if (scalars.length === 0 && nested.length === 0) return null
  return (
    <>
      <h2 className={styles.subheading}>{p.paramsHeading}</h2>
      {scalars.length > 0 && (
        <dl className={styles.params}>
          {scalars.map(([key, value]) => (
            <div key={key} className={styles.paramRow}>
              <dt>{key}</dt>
              <dd>{value === null ? 'null' : String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {nested.map(([key, value]) => (
        <JsonBlock key={key} summary={fmt(p.paramJsonSummary, { key })} value={value} />
      ))}
    </>
  )
}

function JsonBlock({ summary, value }: { summary: string; value: unknown }) {
  const [open, setOpen] = useState(false)
  return (
    <details className={styles.paramJson} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>{summary}</summary>
      {open && <pre className={styles.paramJsonBlock}>{JSON.stringify(value, null, 2)}</pre>}
    </details>
  )
}
