/**
 * `/settings` と `/settings/:page`(ADR-0031)。設定をページに分け、左に目次、右に本文を置く。
 * - 目次は「ユーザー設定」「管理者設定」の見出しで括る(ADR-0019 5章)。どのページを出すかは
 *   `settingsPages.ts::settingsToc`。非管理者には管理者設定の見出しも出さない。
 * - 設定ページの幅で判定する(コンテナクエリ。720px 以上)。広ければ目次と本文を横に並べ、
 *   `/settings` では「表示」を出す。狭ければ `/settings` は目次だけ、各ページは別の画面になる。
 * - 1ページだった頃のハッシュ付きのリンク(`/settings#comfyui` など)は、対応するページへ置き換える。
 * - 見せてはいけない・知らないページは `/settings` へ置き換える。
 * - ComfyUI のワークフローの登録・編集(`/settings/comfyui/workflows/new`、`/:id`)も枠の中に出し、
 *   目次は ComfyUI を選んだ状態にする(ADR-0031 1章の 2026-10-01 追記)。
 * ルートは `/settings` の子のルート(`App.tsx`)。子の間を移ってもこの枠は作り直さない。
 * トーストは設定画面で1つだけ持ち、各ページへは `SettingsShellContext` で渡す。
 */
import { useCallback, useMemo, useState, type ComponentType } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, Navigate, useLocation, useMatch } from 'react-router'
import { getShareSettings } from '../api/client'
import { ToastHost, useToast } from '../components/Toast'
import { isAdmin, useAuth } from '../features/auth/authState'
import { useI18n, type Messages } from '../i18n'
import { useBackNavigate } from '../lib/useBackNavigate'
import { useElementSize } from '../lib/useElementSize'
import { SHARE_SETTINGS_QUERY_KEY } from '../features/settings/queryKeys'
import {
  DEFAULT_SETTINGS_PAGE,
  SETTINGS_TOC_STATE,
  SETTINGS_WIDE_MIN_WIDTH,
  legacySettingsHashPath,
  settingsPageFromSlug,
  settingsPagePath,
  settingsToc,
  visibleSettingsPages,
  type SettingsPageId,
} from '../features/settings/settingsPages'
import { SettingsShellContext, type SettingsShellValue } from '../features/settings/settingsShell'
import { DisplaySettingsPage } from '../features/settings/pages/DisplaySettingsPage'
import { McpSettingsPage } from '../features/settings/pages/McpSettingsPage'
import { OpenAiSettingsPage } from '../features/settings/pages/OpenAiSettingsPage'
import { ComfyUISettingsPage } from '../features/settings/pages/ComfyUISettingsPage'
import { SdWebuiSettingsPage } from '../features/settings/pages/SdWebuiSettingsPage'
import { TagDictionarySettingsPage } from '../features/settings/pages/TagDictionarySettingsPage'
import { ShareLinksSettingsPage } from '../features/settings/pages/ShareLinksSettingsPage'
import { AnnotationSettingsPage } from '../features/settings/pages/AnnotationSettingsPage'
import { EmbeddingSettingsPage } from '../features/settings/pages/EmbeddingSettingsPage'
import { LlmConnectionsSettingsPage } from '../features/settings/pages/LlmConnectionsSettingsPage'
import { AuthSettingsPage } from '../features/settings/pages/AuthSettingsPage'
import {
  AboutSettingsPage,
  AccessTokensSettingsPage,
  ProfileSettingsPage,
  SharesSettingsPage,
} from '../features/settings/pages/OperationSettingsPages'
import { ComfyUIWorkflowFormPage } from '../features/comfyui-workflows/ComfyUIWorkflowFormPage'
import styles from '../features/settings/settings.module.css'

const PAGE_COMPONENTS: Record<SettingsPageId, ComponentType> = {
  profile: ProfileSettingsPage,
  display: DisplaySettingsPage,
  shares: SharesSettingsPage,
  accessTokens: AccessTokensSettingsPage,
  openai: OpenAiSettingsPage,
  llmConnections: LlmConnectionsSettingsPage,
  annotation: AnnotationSettingsPage,
  embeddings: EmbeddingSettingsPage,
  comfyui: ComfyUISettingsPage,
  sdwebui: SdWebuiSettingsPage,
  tagDictionary: TagDictionarySettingsPage,
  mcp: McpSettingsPage,
  shareLinks: ShareLinksSettingsPage,
  authentication: AuthSettingsPage,
  about: AboutSettingsPage,
}

export function SettingsPage() {
  const { t } = useI18n()
  const slug = useMatch('/settings/:page')?.params.page
  const workflowNewMatch = useMatch('/settings/comfyui/workflows/new')
  const workflowEditMatch = useMatch('/settings/comfyui/workflows/:id')
  // ワークフローの登録・編集の画面か(`new` は `:id` にも当たるので先に見る)。
  const workflowForm = workflowNewMatch
    ? { workflowId: undefined }
    : workflowEditMatch
      ? { workflowId: workflowEditMatch.params.id }
      : null
  // 目次だけでなく、ページの本文を出すパスか(狭いときは目次を隠す)。
  const isPageView = Boolean(slug) || workflowForm !== null
  const location = useLocation()
  const auth = useAuth()
  const toast = useToast()
  const goBack = useBackNavigate('/')
  const [rootRef, , rootSize] = useElementSize<HTMLDivElement>()
  const isWide = (rootSize?.width ?? 0) >= SETTINGS_WIDE_MIN_WIDTH
  const [dirtyPages, setDirtyPages] = useState<ReadonlySet<SettingsPageId>>(() => new Set())

  // 共有リンク(ADR-0029)が無効のあいだは、ユーザー設定の一覧ごと隠す。取得できるまでと失敗したときも
  // 出さない(存在を漏らさない側に倒す)。管理者設定で切り替えると同じキャッシュが更新され、すぐ反映される。
  const shareSettingsQuery = useQuery({ queryKey: SHARE_SETTINGS_QUERY_KEY, queryFn: getShareSettings })
  const visibility = {
    isAdmin: isAdmin(auth),
    isOidc: auth.mode === 'oidc',
    sharingEnabled: shareSettingsQuery.data?.enabled === true,
  }
  const toc = settingsToc(visibility)

  const reportDirty = useCallback((page: SettingsPageId, dirty: boolean) => {
    setDirtyPages((prev) => {
      if (prev.has(page) === dirty) return prev
      const next = new Set(prev)
      if (dirty) next.add(page)
      else next.delete(page)
      return next
    })
  }, [])

  const shell = useMemo<SettingsShellValue>(() => ({ toast, isWide, reportDirty }), [toast, isWide, reportDirty])

  if (auth.status === 'loading') return null

  // 1ページだった頃のハッシュ付きのリンク。
  if (!isPageView && location.hash) {
    const legacy = legacySettingsHashPath(location.hash)
    if (legacy) return <Navigate to={legacy} replace />
  }

  const pageId: SettingsPageId | null = workflowForm ? 'comfyui' : slug ? settingsPageFromSlug(slug) : DEFAULT_SETTINGS_PAGE
  if (!pageId) return <Navigate to="/settings" replace />
  if (!visibleSettingsPages(visibility).includes(pageId)) {
    // 共有リンクの一覧は、設定を読み込み終わるまで出すかどうか決められない。
    if (pageId === 'shares' && shareSettingsQuery.isLoading) return null
    return <Navigate to="/settings" replace />
  }

  const PageComponent = PAGE_COMPONENTS[pageId]
  const activePage = isPageView ? pageId : isWide ? DEFAULT_SETTINGS_PAGE : null

  function renderTocItem(id: SettingsPageId) {
    return (
      <li key={id}>
        <Link
          to={settingsPagePath(id)}
          // 広いときは目次での移動を履歴に積まない(「戻る」で設定を開く前の画面へ戻れるように)。
          // 狭いときは目次 → ページを積み、ページの「戻る」で目次へ戻る。
          replace={isWide}
          state={isWide ? undefined : SETTINGS_TOC_STATE}
          className={styles.tocLink}
          aria-current={activePage === id ? 'page' : undefined}
        >
          <span className={styles.tocLinkLabel}>{pageTitle(t, id)}</span>
          {dirtyPages.has(id) && (
            <span className={styles.tocDot} role="img" aria-label={t.settings.frame.unsavedMark} />
          )}
        </Link>
      </li>
    )
  }

  return (
    <SettingsShellContext.Provider value={shell}>
      <div ref={rootRef} className={styles.root}>
        <div className={styles.layout} data-view={isPageView ? 'page' : 'index'}>
          <nav className={styles.toc} aria-label={t.settings.frame.tocLabel}>
            <div className={styles.tocHeader}>
              <button type="button" className={`${styles.backButton} ${styles.tocBack}`} onClick={goBack}>
                {t.common.back}
              </button>
              <h1 className={styles.tocTitle}>{t.settings.title}</h1>
            </div>
            <div className={styles.tocGroup}>
              <h2 className={styles.tocGroupHeading}>{t.settings.userHeading}</h2>
              <ul className={styles.tocList}>{toc.user.map(renderTocItem)}</ul>
            </div>
            {toc.admin.length > 0 && (
              <div className={styles.tocGroup}>
                <h2 className={styles.tocGroupHeading}>{t.settings.adminHeading}</h2>
                <ul className={styles.tocList}>{toc.admin.map(renderTocItem)}</ul>
              </div>
            )}
            <div className={styles.tocGroup}>
              <ul className={styles.tocList}>{toc.other.map(renderTocItem)}</ul>
            </div>
          </nav>
          <div className={styles.content}>
            {/* 狭いときの `/settings` は目次だけ(本文は CSS で隠す)。ページを切り替えたら作り直す。 */}
            {workflowForm ? (
              <ComfyUIWorkflowFormPage key={location.pathname} workflowId={workflowForm.workflowId} />
            ) : (
              <PageComponent key={pageId} />
            )}
          </div>
        </div>
      </div>
      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </SettingsShellContext.Provider>
  )
}

function pageTitle(t: Messages, id: SettingsPageId): string {
  return t.settings.pages[id]
}
