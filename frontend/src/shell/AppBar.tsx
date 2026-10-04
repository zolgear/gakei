/**
 * App バー(高さ48px)。ロゴ、ワークスペースのタブ(生成/履歴/マップ)、検索ボタン(サイドバーの検索
 * パネルを開く)、キュー状態、「新規生成」。モバイル(狭い幅)ではハンバーガーボタンでサイドバーのドロワーを開閉する。
 * 「新規生成」は Alt+N(Mac は Option+N)でも起動できる(ADR-0009「『新規生成』の
 * ショートカット」2026-09-24)。
 * 「マップ」(`/map`、ADR-0033 8章)は埋め込みが使えるときだけ出す(2026-10-04 にアイコンレールから
 * ここへ移し、タブの順を「生成」「履歴」「マップ」にした)。
 */
import { useCallback, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, NavLink, useNavigate } from 'react-router'
import { getCapabilities } from '../api/client'
import { useRunFormContext } from '../context/useRunFormContext'
import { clearFormContent, hasFormContent } from '../features/run-form/formStateHelpers'
import { loadLastAssetGroupId } from '../context/lastAssetGroupStorage'
import { SearchLauncher } from '../features/search/SearchLauncher'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { GakeiMark } from '../components/GakeiMark'
import { useEmbeddingCapabilities } from '../features/embeddings/useEmbeddingCapabilities'
import { useFaviconIconState } from '../features/favicon/useFaviconProgress'
import { fmt, useI18n } from '../i18n'
import { useQueueStatus } from './useQueueStatus'
import { isNewRunShortcut } from './newRunShortcut'
import { UserMenu } from './UserMenu'
import styles from './AppBar.module.css'

interface AppBarProps {
  onToggleDrawer: () => void
  drawerOpen: boolean
}

export function AppBar({ onToggleDrawer, drawerOpen }: AppBarProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const queue = useQueueStatus()
  // favicon と同じ状態・同じ動き(ADR-0009 8章)。件数のバッジはこれまでどおり別に表示する。
  const faviconIconState = useFaviconIconState()
  // 「新規生成」で使う capabilities の初期値取得。プロバイダーの表示(バッジ)には使わない
  // (モデル選択欄が既にプロバイダーを表示している)。useRunFormLogic 側の同じ queryKey の
  // クエリとキャッシュを共有するので、通常は追加のリクエストにならない。
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const embeddings = useEmbeddingCapabilities()
  const { formState, setFormState } = useRunFormContext()
  const [newRunConfirmOpen, setNewRunConfirmOpen] = useState(false)

  // /studio の上段(結果エリア)は resetAt を見て、進行中/直前の Run の表示を空に戻す
  // (表示中の Asset は ?asset= の有無で決まるので、ここでは navigate だけでよい)。
  const startNewRun = useCallback(() => {
    setFormState(clearFormContent(capsQuery.data, loadLastAssetGroupId()))
    navigate('/studio', { state: { resetAt: Date.now() } })
  }, [capsQuery.data, navigate, setFormState])

  const handleNewRun = useCallback(() => {
    if (hasFormContent(formState)) {
      setNewRunConfirmOpen(true)
      return
    }
    startNewRun()
  }, [formState, startNewRun])

  // Alt+N(Mac は Option+N)でも「新規生成」を起動する。確認ダイアログ表示中は反応させない。
  // IME 変換中の確定キー等(isComposing)も無視する。
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.isComposing || newRunConfirmOpen) return
      if (!isNewRunShortcut(e)) return
      e.preventDefault()
      handleNewRun()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [newRunConfirmOpen, handleNewRun])

  return (
    <header className={styles.bar}>
      <button
        type="button"
        className={styles.drawerToggle}
        aria-label={t.shell.appBar.toggleMenu}
        aria-expanded={drawerOpen}
        onClick={onToggleDrawer}
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
          <path
            d="M2.5 5h13M2.5 9h13M2.5 13h13"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
      </button>

      <div className={styles.brand}>
        <GakeiMark size={22} state={faviconIconState} />
        <span className={styles.brandName}>GAKEI</span>
      </div>

      <nav aria-label={t.shell.appBar.workspaceNav} className={styles.tabs}>
        <NavLink
          to="/studio"
          className={({ isActive }) => `${styles.tab} ${isActive ? styles.tabActive : ''}`}
        >
          {t.shell.appBar.studioTab}
        </NavLink>
        <NavLink
          to="/"
          end
          className={({ isActive }) => `${styles.tab} ${isActive ? styles.tabActive : ''}`}
        >
          {t.shell.appBar.historyTab}
        </NavLink>
        {embeddings && (
          <NavLink
            to="/map"
            className={({ isActive }) => `${styles.tab} ${isActive ? styles.tabActive : ''}`}
          >
            {t.shell.appBar.mapTab}
          </NavLink>
        )}
      </nav>

      <SearchLauncher />

      <div className={styles.spacer} />

      <div className={styles.right}>
        <div
          className={styles.queueBadge}
          role="status"
          aria-label={fmt(t.shell.appBar.queueStatus, { running: queue.running, queued: queue.queued })}
        >
          <span className={styles.queueDot} data-active={queue.running > 0} aria-hidden="true" />
          <span aria-hidden="true">{fmt(t.shell.appBar.running, { count: queue.running })}</span>
          <span className={styles.queueMuted} aria-hidden="true">
            {fmt(t.shell.appBar.queued, { count: queue.queued })}
          </span>
        </div>
        <Link
          to="/settings"
          className={styles.settingsButton}
          aria-label={t.shell.appBar.settings}
          title={t.shell.appBar.settings}
        >
          {/* 歯車(2026-09-27。以前の円+8本線は太陽に見えたため差し替え。ADR-0019 5章)。
              外周のリングと歯を表す8個の小さな矩形、中心の穴で構成する。 */}
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="8" cy="8" r="4.6" stroke="currentColor" strokeWidth="1.4" />
            <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.4" />
            {[0, 45, 90, 135, 180, 225, 270, 315].map((deg) => (
              <rect
                key={deg}
                x="7.25"
                y="0.5"
                width="1.5"
                height="2.1"
                rx="0.4"
                fill="currentColor"
                transform={`rotate(${deg} 8 8)`}
              />
            ))}
          </svg>
        </Link>
        <UserMenu />
        <button
          type="button"
          className={styles.newRunButton}
          onClick={handleNewRun}
          title={t.shell.appBar.newRunTitle}
          aria-keyshortcuts="Alt+N"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
          <span className={styles.newRunLabel}>{t.shell.appBar.newRun}</span>
        </button>
      </div>

      <ConfirmDialog
        open={newRunConfirmOpen}
        message={t.shell.appBar.newRunConfirm.message}
        confirmLabel={t.shell.appBar.newRunConfirm.confirmLabel}
        onConfirm={() => {
          setNewRunConfirmOpen(false)
          startNewRun()
        }}
        onCancel={() => setNewRunConfirmOpen(false)}
      />
    </header>
  )
}
