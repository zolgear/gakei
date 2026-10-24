/**
 * マップで選んだ画像を大きく見るパネル(ADR-0033 8章・2026-10-04 追記、ユーザーの指示)。
 * 選択カードの「↗」で開き、「↙」か Esc で小さなカードに戻る(開閉はブラウザに覚え、開いている間は
 * 別の画像を選んでもこのパネルで出す。`MapCanvas`)。画像を押すとビューアを開く。デスクトップではマップの右側に、
 * 768px 未満では下からのシートとして重ねる。
 *
 * 読むだけのパネル(編集はビューアで行う)。情報はビューアの情報欄を縮めたもので、取得もビューアと
 * 同じ `['asset', id]` のクエリを使う(ビューアへ移ったときに取り直さない)。
 * パネルはマップの canvas の兄弟要素なので、パネルの上のポインタ操作とホイールはマップを動かさない。
 *
 * マップでマスクしているあいだ(ADR-0049 5章「マップ」)は、マスクする画像をぼかして段階を重ねる。
 * ここでは1枚ずつは外さない(外すのはツールバーの目のボタンだけ)。画像を押すと今までどおり
 * ビューアを開く(ビューアでは1枚ずつ外せる)。
 */
import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { getAsset, type ContentRatingLevel } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { AssetImage } from '../mods/AssetImage'
import { assetKindLabel, formatBytes, formatDateTime } from '../../lib/format'
import { useI18n } from '../../i18n'
import { TagChip } from '../annotations/TagChip'
import { buildSimilarSearchPath } from '../search/searchQuerySync'
import { RatingBadge } from '../content-rating/RatingBadge'
import ratingStyles from '../content-rating/ContentRating.module.css'
import styles from './MapPreviewPanel.module.css'
import { WithheldPlaceholder } from '../mods/WithheldPlaceholder'
import { AssetFavoriteButton } from '../history/AssetFavoriteButton'
import { LineageIcon } from '../../components/icons'

interface MapPreviewPanelProps {
  assetId: string
  /** グラフの元データにあるタイトル(詳細が届く前に出す)。 */
  fallbackTitle: string | null | undefined
  /** 開いたときに閉じるボタンへ焦点を移すか(「↗」で広げたときだけ)。 */
  autoFocus: boolean
  onCollapse: () => void
  /** マップでマスクしているか(ユーザー設定でマスクする画像で、マップ全体で外していない)。 */
  ratingMasked?: boolean
  /** 段階(マスクしているときに重ねる。null は未判定)。 */
  rating?: ContentRatingLevel | null
}

/** プロンプトの抜粋の長さ(残りは CSS で行数を切る)。 */
const PROMPT_SNIPPET_MAX = 400

export function MapPreviewPanel({
  assetId,
  fallbackTitle,
  autoFocus,
  onCollapse,
  ratingMasked = false,
  rating,
}: MapPreviewPanelProps) {
  const { t } = useI18n()
  const m = t.map
  const v = t.viewer
  const collapseRef = useRef<HTMLButtonElement | null>(null)
  const query = useQuery({ queryKey: ['asset', assetId], queryFn: () => getAsset(assetId) })
  const asset = query.data

  // 「↗」で開いたら閉じるボタンに焦点を移す(Esc ですぐ戻れるように)。覚えた開閉で開いたとき
  // (マップで選んだとき)は canvas の焦点を奪わない。Esc はページ全体で受けるので、どちらでも効く。
  useEffect(() => {
    if (autoFocus) collapseRef.current?.focus({ preventScroll: true })
  }, [autoFocus])

  // Esc で畳む。パネルの余白を押すと焦点が body に移るので、ページ全体で受ける(canvas が先に
  // 受けて処理したときは defaultPrevented になっているので二重に畳まない)。
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      // 入力欄(絞り込みのタグなど)での Esc はその欄に任せる。
      const target = e.target as HTMLElement | null
      if (target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))) return
      e.preventDefault()
      onCollapse()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onCollapse])

  const title = asset?.title || fallbackTitle || m.untitled
  const runPrompt = asset?.produced_by_run?.prompt
  const embeddedPrompt = asset?.embedded_meta?.prompt
  const prompt = runPrompt || embeddedPrompt
  const tags = asset?.tags ?? []

  return (
    <section className={styles.panel} aria-label={m.previewLabel}>
      <header className={styles.header}>
        <h2 className={styles.title} title={title}>
          {title}
        </h2>
        <Link
          to={`/lineage/${assetId}`}
          className={styles.iconButton}
          aria-label={m.lineageGraph}
          title={m.lineageGraph}
        >
          <LineageIcon size={15} />
        </Link>
        {/* お気に入り(ADR-0055)。詳細が届くまでは状態が分からないので出さない。 */}
        {asset && (
          <AssetFavoriteButton
            assetId={assetId}
            favorite={asset.favorite}
            disabled={Boolean(asset.deleted_at)}
            className={styles.iconButton}
          />
        )}
        <button
          ref={collapseRef}
          type="button"
          className={styles.iconButton}
          aria-label={m.collapsePreview}
          title={m.collapsePreview}
          onClick={onCollapse}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M13 3L4 12M4 6v6h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </header>

      <div className={styles.body}>
        <Link to={`/assets/${assetId}`} className={styles.imageLink} aria-label={m.openInViewer} title={m.openInViewer}>
          {asset?.content_withheld ? (
            // 画像を残さなかった(ADR-0051 4章。マップに出た後で判定を受けたとき)。読みに行かない。
            <WithheldPlaceholder className={styles.image} label={asset.content_withheld_verdict?.label} />
          ) : (
            <AssetImage
              key={assetId}
              src={assetUrl(assetId, 'preview')}
              alt=""
              className={styles.image}
              data-rating-masked={ratingMasked || undefined}
            />
          )}
          {ratingMasked && !asset?.content_withheld && (
            <span className={styles.imageMask}>
              {rating ? (
                <RatingBadge level={rating} size="mask" />
              ) : (
                <span className={ratingStyles.badge} data-size="mask" data-level="unrated">
                  {t.contentRating.unrated}
                </span>
              )}
              <span className={styles.imageMaskHint}>{m.ratingMaskPreviewHint}</span>
            </span>
          )}
        </Link>

        <div className={styles.actions}>
          <Link to={buildSimilarSearchPath(assetId)}>{m.similarInSearch}</Link>
        </div>

        {query.isError && <p className={styles.muted}>{m.previewLoadFailed}</p>}
        {query.isLoading && <p className={styles.muted}>{m.loading}</p>}
        {asset && (
          <>
            <dl className={styles.meta}>
              <dt>{m.kindLabel}</dt>
              <dd>{assetKindLabel(asset.kind) ?? asset.kind}</dd>
              <dt>{v.dimensions}</dt>
              <dd>
                {asset.width} × {asset.height} px
              </dd>
              <dt>{v.format}</dt>
              <dd>{asset.mime}</dd>
              <dt>{v.size}</dt>
              <dd>{formatBytes(asset.bytes)}</dd>
              <dt>{v.created}</dt>
              <dd>{formatDateTime(asset.created_at)}</dd>
            </dl>

            {tags.length > 0 && (
              <div className={styles.section}>
                <h3 className={styles.subheading}>{v.annotation.tagsLabel}</h3>
                <div className={styles.tags}>
                  {tags.map((tag) => (
                    <TagChip key={tag.name} name={tag.name} source={tag.source} />
                  ))}
                </div>
              </div>
            )}

            {prompt && (
              <div className={styles.section}>
                <h3 className={styles.subheading}>{runPrompt ? v.promptHeading : m.embeddedPromptHeading}</h3>
                <p className={styles.prompt}>
                  {prompt.length > PROMPT_SNIPPET_MAX ? `${prompt.slice(0, PROMPT_SNIPPET_MAX)}…` : prompt}
                </p>
                {asset.produced_by_run && (
                  <p className={styles.muted}>
                    {asset.produced_by_run.model} ・ {asset.produced_by_run.operation}
                  </p>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  )
}
