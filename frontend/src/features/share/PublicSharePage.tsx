/**
 * ログイン不要の共有のページ `/s/:token`(ADR-0029)。`main.tsx` が `AuthGate` とルーティングの
 * 外で描く(`/api/auth/me` に依存しない。App バーやサイドバーなど、ログイン前提の画面は出さない)。
 *
 * - 画像: 選んだ画像をパン/ズームで表示する(`AssetCanvas`)。原本を許す共有なら、拡大すると
 *   原本に差し替え、ダウンロードもできる。許さない共有はプレビュー(長辺 2048px)まで。
 * - 画像のタイトル、作成日時、その画像を作った Run のプロンプトとパラメーター。
 * - 含まれる画像の一覧(2枚以上のとき)と、範囲が系列なら系列グラフ。
 * - 見つからない・取り消し済み・機能が無効は、区別せず「このリンクは無効です」だけを出す。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getPublicShare, type PublicShareAsset, type PublicShareResponse } from '../../api/client'
import { publicShareAssetUrl } from '../../api/assetUrl'
import { GakeiMark } from '../../components/GakeiMark'
import { assetKindLabel, formatDateTime } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import { AssetCanvas } from '../viewer/AssetCanvas'
import { PublicLineageGraph } from './PublicLineageGraph'
import { showsLineage } from './shareScope'
import styles from './PublicSharePage.module.css'

interface PublicSharePageProps {
  token: string
}

export function PublicSharePage({ token }: PublicSharePageProps) {
  const { t } = useI18n()
  const query = useQuery({
    queryKey: ['public-share', token],
    queryFn: () => getPublicShare(token),
    retry: false,
    refetchOnWindowFocus: false,
  })

  useEffect(() => {
    document.title = 'GAKEI'
  }, [])

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <GakeiMark size={22} />
        <span className={styles.brand}>GAKEI</span>
        {query.data && (
          <span className={styles.headerNote}>
            {t.publicShare.sharedBy} · {fmt(t.publicShare.images, { count: query.data.assets?.length ?? 0 })}
          </span>
        )}
      </header>
      {query.isLoading && <p className={styles.message}>{t.publicShare.loading}</p>}
      {query.isError && (
        <div className={styles.invalid}>
          <h1 className={styles.invalidTitle}>{t.publicShare.invalid}</h1>
          <p className={styles.invalidDetail}>{t.publicShare.invalidDetail}</p>
        </div>
      )}
      {query.data && <PublicShareBody token={token} data={query.data} />}
    </div>
  )
}

function PublicShareBody({ token, data }: { token: string; data: PublicShareResponse }) {
  const { t } = useI18n()
  const p = t.publicShare
  const assets = useMemo(() => data.assets ?? [], [data.assets])
  const [selectedId, setSelectedId] = useState(data.root_asset_id)
  const selected: PublicShareAsset | undefined =
    assets.find((a) => a.id === selectedId) ?? assets.find((a) => a.id === data.root_asset_id) ?? assets[0]
  const run = selected?.run_id ? (data.runs ?? []).find((r) => r.id === selected.run_id) : undefined
  const params = Object.entries(run?.params ?? {})
  // 一覧の画像(マスクは系列グラフにだけ出す。単独で見せる意味が薄いため)。
  const listed = assets.filter((a) => a.kind !== 'mask')

  const thumbUrlFor = useCallback(
    (assetId: string) => publicShareAssetUrl(token, assetId, 'thumb'),
    [token],
  )

  if (!selected) {
    return (
      <div className={styles.invalid}>
        <h1 className={styles.invalidTitle}>{p.invalid}</h1>
      </div>
    )
  }

  return (
    <main className={styles.main}>
      <section className={styles.viewer}>
        <AssetCanvas
          asset={selected}
          urlFor={(variant) => publicShareAssetUrl(token, selected.id, variant)}
          allowOriginal={data.allow_original}
        />
      </section>

      <aside className={styles.side}>
        <h1 className={styles.title} data-untitled={selected.title ? undefined : 'true'}>
          {selected.title ?? p.untitled}
        </h1>
        <dl className={styles.meta}>
          <dt>{p.created}</dt>
          <dd>{formatDateTime(selected.created_at)}</dd>
          <dt>{p.dimensions}</dt>
          <dd>
            {selected.width} × {selected.height} px
            {selected.kind !== 'generated' && assetKindLabel(selected.kind) && ` · ${assetKindLabel(selected.kind)}`}
          </dd>
          {run && (
            <>
              <dt>{p.model}</dt>
              <dd>{run.model || '-'}</dd>
              <dt>{p.operation}</dt>
              <dd>{p.operations[run.operation]}</dd>
            </>
          )}
        </dl>

        {run ? (
          <>
            <h2 className={styles.subheading}>{p.promptHeading}</h2>
            <p className={styles.prompt}>{run.prompt}</p>
            {params.length > 0 && (
              <>
                <h2 className={styles.subheading}>{p.paramsHeading}</h2>
                <dl className={styles.params}>
                  {params.map(([key, value]) => (
                    <div key={key} className={styles.paramRow}>
                      <dt>{key}</dt>
                      <dd>{value === null ? 'null' : String(value)}</dd>
                    </div>
                  ))}
                </dl>
              </>
            )}
          </>
        ) : (
          <p className={styles.note}>{p.noRun}</p>
        )}

        {data.allow_original ? (
          <a
            className={styles.downloadButton}
            href={publicShareAssetUrl(token, selected.id, 'original', { download: true })}
          >
            {p.downloadOriginal}
          </a>
        ) : (
          <p className={styles.note}>{p.originalNotAllowed}</p>
        )}

        {listed.length > 1 && (
          <>
            <h2 className={styles.subheading}>{p.imageListLabel}</h2>
            <ul className={styles.thumbs}>
              {listed.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    className={styles.thumbButton}
                    aria-current={a.id === selected.id}
                    aria-label={a.title ?? p.untitled}
                    title={a.title ?? undefined}
                    onClick={() => setSelectedId(a.id)}
                  >
                    <img src={thumbUrlFor(a.id)} alt="" className={`${styles.thumbImg} checkerboard`} />
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        {showsLineage(data.scope) && (
          <>
            <h2 className={styles.subheading}>{p.lineageHeading}</h2>
            <PublicLineageGraph
              data={data}
              selectedAssetId={selected.id}
              thumbUrlFor={thumbUrlFor}
              onSelectAsset={setSelectedId}
            />
          </>
        )}
      </aside>
    </main>
  )
}
