/**
 * `/settings`。上から「表示言語」「表示」「OpenAI API キー」「生成」「ComfyUI」「GAKEI について」の
 * 6セクション(oidc モードでは「プロフィール」「アクセストークン」、管理者には「MCP」も。ADR-0023。
 * 自分の「共有リンク」の一覧(管理者設定で有効のときだけ)と、管理者には共有リンクの有効/無効も。ADR-0029)を、「ユーザー設定」(言語、表示)と「管理者設定」(OpenAI キー・Base URL、生成、
 * ComfyUI)の見出しで括る(ADR-0019 5章)。「GAKEI について」はどちらにも属さず末尾のまま。
 * 管理者設定は `isAdmin`(`features/auth/authState.ts`。none モードは常に true)のときだけ
 * 描画し、非管理者には一文(`settings.adminOnly`)だけを出す。どのセクションを見せるかは
 * 純粋関数 `settingsSections.ts::visibleSections` が決める。
 * 表示: 生成中の進捗をファビコンに出すかの設定と、生成画面の入力欄の配置(下段/サイドバー。
 * ADR-0009 1章・2026-09-26 追記)。どちらも即時反映(保存ボタンなし)で `useSyncExternalStore` /
 * `useStudioLayout` を使う。
 * OpenAI: 環境変数(`.env`)のキーは画面のものより優先し、画面からは変更・削除できない
 * (`isEnvLocked`)。保存前に OpenAI へ有効性を確認するため、保存ボタンの応答に
 * 最大15秒程度かかる(`確認中...` を出す)。キーはこのページの入力欄以外(localStorage 等)
 * には一切保持しない。
 * 生成: `GenerationSettingsSection` が moderation(Generate 専用。入力画像を使わない生成にだけ
 * 効く)を auto/low から選ぶ(ADR-0009 1章、2026-09-24)。API キーと違い、環境変数
 * (`MODERATION`)由来でも入力欄はロックしない(画面で保存した値が優先。注記だけ出す)。
 * ComfyUI: 接続の設定(接続/URL変更/接続テスト/切り離す)は `ComfyUIStatusPanel` に、
 * 1回の実行を待つ上限(タイムアウト)は `ComfyUITimeoutField` に集約し、ここでインラインに
 * 表示する。`/settings/comfyui`(ワークフロー一覧)からは `#comfyui` 付きでこのページへ
 * 戻れるので、マウント時にハッシュがあればそこへスクロールする。
 */
import { useEffect, useState, useSyncExternalStore } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router'
import {
  ApiError,
  deleteOpenAiBaseUrl,
  deleteOpenAiKey,
  getComfyUIStatus,
  getOpenAiBaseUrlStatus,
  getOpenAiKeyStatus,
  getShareSettings,
  setOpenAiBaseUrl,
  setOpenAiKey,
} from '../api/client'
import { useBackNavigate } from '../lib/useBackNavigate'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ToastHost, useToast } from '../components/Toast'
import {
  OPENAI_BASE_URL_STATUS_QUERY_KEY,
  OPENAI_KEY_STATUS_QUERY_KEY,
  SHARE_SETTINGS_QUERY_KEY,
} from '../features/settings/queryKeys'
import {
  apiKeyStatusView,
  canDeleteKey,
  isEnvLocked,
  type ApiKeyStatusView,
} from '../features/settings/apiKeyStatus'
import {
  baseUrlStatusView,
  canClearBaseUrl,
  isBaseUrlEnvLocked,
  type BaseUrlStatusView,
} from '../features/settings/baseUrlStatus'
import { ComfyUIStatusPanel } from '../features/comfyui-workflows/ComfyUIStatusPanel'
import { AboutSection } from '../features/settings/AboutSection'
import { ProfileSection } from '../features/settings/ProfileSection'
import { GenerationSettingsSection } from '../features/settings/GenerationSettingsSection'
import { ComfyUITimeoutField } from '../features/settings/ComfyUITimeoutField'
import { McpSettingsSection } from '../features/settings/McpSettingsSection'
import { AnnotationSettingsSection } from '../features/settings/AnnotationSettingsSection'
import { ApiTokensSection } from '../features/settings/ApiTokensSection'
import { SharesSection } from '../features/settings/SharesSection'
import { ShareSettingsSection } from '../features/settings/ShareSettingsSection'
import { visibleSections } from '../features/settings/settingsSections'
import { isAdmin, useAuth } from '../features/auth/authState'
import { LANGUAGE_SETTING_LABEL, LOCALES, LOCALE_LABELS, isLocale, useI18n } from '../i18n'
import {
  getFaviconProgressEnabled,
  setFaviconProgressEnabled,
  subscribeFaviconProgressEnabled,
} from '../features/favicon/faviconPrefs'
import { isStudioLayout, setStudioLayout } from '../features/workspace/studioLayout'
import { useStudioLayout } from '../features/workspace/useStudioLayout'
import styles from './SettingsPage.module.css'

export function SettingsPage() {
  const { t, locale, setLocale } = useI18n()
  const goBack = useBackNavigate('/')
  const location = useLocation()
  const queryClient = useQueryClient()
  const toast = useToast()
  const auth = useAuth()
  const admin = isAdmin(auth)
  // 共有リンク(ADR-0029)が無効のあいだは、ユーザー設定の一覧ごと隠す。取得できるまでと失敗したときも
  // 出さない(存在を漏らさない側に倒す)。管理者設定で切り替えると同じキャッシュが更新され、すぐ反映される。
  const shareSettingsQuery = useQuery({ queryKey: SHARE_SETTINGS_QUERY_KEY, queryFn: getShareSettings })
  const sections = visibleSections(admin, auth.mode === 'oidc', shareSettingsQuery.data?.enabled === true)
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [baseUrlInput, setBaseUrlInput] = useState('')
  const [baseUrlClearConfirmOpen, setBaseUrlClearConfirmOpen] = useState(false)
  const faviconProgressEnabled = useSyncExternalStore(
    subscribeFaviconProgressEnabled,
    getFaviconProgressEnabled,
    getFaviconProgressEnabled,
  )
  const studioLayout = useStudioLayout()

  // ハッシュ付きでこのページに来たとき(例: `/settings/comfyui` の「接続の設定」リンク)、
  // 該当セクションへスクロールする。react-router はハッシュへの自動スクロールをしない。
  useEffect(() => {
    if (!location.hash) return
    const target = document.getElementById(location.hash.slice(1))
    target?.scrollIntoView({ block: 'start' })
  }, [location.hash])

  const statusQuery = useQuery({
    queryKey: OPENAI_KEY_STATUS_QUERY_KEY,
    queryFn: getOpenAiKeyStatus,
  })

  const baseUrlQuery = useQuery({
    queryKey: OPENAI_BASE_URL_STATUS_QUERY_KEY,
    queryFn: getOpenAiBaseUrlStatus,
  })

  // Base URL は秘密ではないので、現在の値を常に入力欄に映す(キーと違い読み戻す)。
  useEffect(() => {
    if (baseUrlQuery.data) {
      setBaseUrlInput(baseUrlQuery.data.value ?? '')
    }
  }, [baseUrlQuery.data])

  // `ComfyUIStatusPanel` と同じキャッシュ(`['comfyui-status']`)を読むだけ。パネル側の
  // 取得と重複しても react-query が1回のリクエストにまとめる。未接続時だけ下のリンクに
  // ヒントを添えるために使う。
  const comfyStatusQuery = useQuery({ queryKey: ['comfyui-status'], queryFn: getComfyUIStatus })

  const saveMutation = useMutation({
    mutationFn: setOpenAiKey,
    onSuccess: (data) => {
      queryClient.setQueryData(OPENAI_KEY_STATUS_QUERY_KEY, data)
      setApiKeyInput('')
      toast.show({ message: t.settings.apiKey.savedToast })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteOpenAiKey,
    onSuccess: (data) => {
      queryClient.setQueryData(OPENAI_KEY_STATUS_QUERY_KEY, data)
      toast.show({ message: t.settings.apiKey.deletedToast })
    },
  })

  const saveBaseUrlMutation = useMutation({
    mutationFn: setOpenAiBaseUrl,
    onSuccess: (data) => {
      queryClient.setQueryData(OPENAI_BASE_URL_STATUS_QUERY_KEY, data)
      toast.show({ message: t.settings.apiKey.baseUrl.savedToast })
    },
  })

  const clearBaseUrlMutation = useMutation({
    mutationFn: deleteOpenAiBaseUrl,
    onSuccess: (data) => {
      queryClient.setQueryData(OPENAI_BASE_URL_STATUS_QUERY_KEY, data)
      toast.show({ message: t.settings.apiKey.baseUrl.clearedToast })
    },
  })

  const status = statusQuery.data
  const locked = status ? isEnvLocked(status) : false
  const showDelete = status ? canDeleteKey(status) : false
  const activeError = saveMutation.error ?? deleteMutation.error
  const errorMessage =
    activeError instanceof ApiError ? activeError.message : activeError ? t.settings.apiKey.communicationFailed : null

  function handleSave() {
    const value = apiKeyInput.trim()
    if (!value) return
    saveMutation.mutate(value)
  }

  const baseUrlStatus = baseUrlQuery.data
  const baseUrlLocked = baseUrlStatus ? isBaseUrlEnvLocked(baseUrlStatus) : false
  const showClearBaseUrl = baseUrlStatus ? canClearBaseUrl(baseUrlStatus) : false
  const activeBaseUrlError = saveBaseUrlMutation.error ?? clearBaseUrlMutation.error
  const baseUrlErrorMessage =
    activeBaseUrlError instanceof ApiError
      ? activeBaseUrlError.message
      : activeBaseUrlError
        ? t.settings.apiKey.baseUrl.communicationFailed
        : null

  function handleSaveBaseUrl() {
    const value = baseUrlInput.trim()
    if (!value) return
    saveBaseUrlMutation.mutate(value)
  }

  return (
    <div className={styles.page}>
      <button type="button" className={styles.backLink} onClick={goBack}>
        {t.common.back}
      </button>
      <h1 className={styles.title}>{t.settings.title}</h1>

      <h2 className={styles.groupHeading}>{t.settings.userHeading}</h2>

      {sections.includes('profile') && <ProfileSection toast={toast} />}

      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>
          <label htmlFor="gakei-locale">{LANGUAGE_SETTING_LABEL}</label>
        </h2>
        <select
          id="gakei-locale"
          className={styles.languageSelect}
          value={locale}
          onChange={(e) => {
            if (isLocale(e.target.value)) setLocale(e.target.value)
          }}
        >
          {LOCALES.map((l) => (
            <option key={l} value={l}>
              {LOCALE_LABELS[l]}
            </option>
          ))}
        </select>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>{t.settings.display.heading}</h2>
        <label className={styles.checkboxRow}>
          <input
            type="checkbox"
            checked={faviconProgressEnabled}
            onChange={(e) => setFaviconProgressEnabled(e.target.checked)}
          />
          <span>{t.settings.display.faviconProgress.label}</span>
        </label>
        <p className={styles.helpText}>{t.settings.display.faviconProgress.help}</p>

        <div className={styles.field}>
          <label htmlFor="gakei-studio-layout">{t.settings.display.studioLayout.label}</label>
          <select
            id="gakei-studio-layout"
            className={styles.languageSelect}
            value={studioLayout}
            onChange={(e) => {
              if (isStudioLayout(e.target.value)) setStudioLayout(e.target.value)
            }}
          >
            <option value="bottom">{t.settings.display.studioLayout.optionBottom}</option>
            <option value="sidebar">{t.settings.display.studioLayout.optionSidebar}</option>
          </select>
        </div>
        <p className={styles.helpText}>{t.settings.display.studioLayout.help}</p>
      </section>

      {sections.includes('shares') && <SharesSection toast={toast} />}

      {sections.includes('accessTokens') && <ApiTokensSection toast={toast} />}

      <h2 className={styles.groupHeading}>{t.settings.adminHeading}</h2>

      {!admin && <p className={styles.helpText}>{t.settings.adminOnly}</p>}

      {sections.includes('apiKey') && (
      <section id="openai" className={styles.section}>
        <h2 className={styles.sectionHeading}>{t.settings.apiKey.heading}</h2>

        {statusQuery.isLoading && <p className={styles.placeholder}>{t.settings.apiKey.loading}</p>}

        {statusQuery.isError && (
          <div className={styles.loadError}>
            <p className={styles.errorText}>{t.settings.apiKey.loadFailed}</p>
            <button type="button" className={styles.retryButton} onClick={() => void statusQuery.refetch()}>
              {t.settings.apiKey.retry}
            </button>
          </div>
        )}

        {status && (
          <>
            <StatusBadge view={apiKeyStatusView(status)} />
            {locked && <p className={styles.helpText}>{t.settings.apiKey.envLocked}</p>}
            {!status.required && <p className={styles.helpText}>{t.settings.apiKey.notRequired}</p>}

            {!locked && (
              <div className={styles.form}>
                <input
                  type="password"
                  className={styles.input}
                  value={apiKeyInput}
                  onChange={(e) => setApiKeyInput(e.target.value)}
                  placeholder={t.settings.apiKey.inputPlaceholder}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={saveMutation.isPending}
                />
                <div className={styles.actions}>
                  <button
                    type="button"
                    className={styles.saveButton}
                    disabled={apiKeyInput.trim() === '' || saveMutation.isPending}
                    onClick={handleSave}
                  >
                    {saveMutation.isPending ? t.settings.apiKey.verifying : t.settings.apiKey.save}
                  </button>
                  {showDelete && (
                    <button
                      type="button"
                      className={styles.deleteButton}
                      disabled={deleteMutation.isPending}
                      onClick={() => setDeleteConfirmOpen(true)}
                    >
                      {t.settings.apiKey.delete}
                    </button>
                  )}
                </div>
                {errorMessage && <p className={styles.errorText}>{errorMessage}</p>}
              </div>
            )}
          </>
        )}

        <p className={styles.helpText}>
          {t.settings.apiKey.helpTextBefore}{' '}
          <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer">
            {t.settings.apiKey.helpTextLink}
          </a>{' '}
          {t.settings.apiKey.helpTextAfter}
        </p>

        <div className={styles.subsection}>
          <h3 className={styles.subheading}>{t.settings.apiKey.baseUrl.heading}</h3>

          {baseUrlQuery.isLoading && (
            <p className={styles.placeholder}>{t.settings.apiKey.baseUrl.loading}</p>
          )}

          {baseUrlQuery.isError && (
            <div className={styles.loadError}>
              <p className={styles.errorText}>{t.settings.apiKey.baseUrl.loadFailed}</p>
              <button
                type="button"
                className={styles.retryButton}
                onClick={() => void baseUrlQuery.refetch()}
              >
                {t.settings.apiKey.baseUrl.retry}
              </button>
            </div>
          )}

          {baseUrlStatus && (
            <>
              {baseUrlLocked ? (
                <>
                  <p className={styles.helpText}>
                    <BaseUrlDetail view={baseUrlStatusView(baseUrlStatus)} />
                  </p>
                  <p className={styles.helpText}>{t.settings.apiKey.baseUrl.envLocked}</p>
                </>
              ) : (
                <div className={styles.form}>
                  <input
                    type="text"
                    className={styles.input}
                    value={baseUrlInput}
                    onChange={(e) => setBaseUrlInput(e.target.value)}
                    placeholder={t.settings.apiKey.baseUrl.inputPlaceholder}
                    autoComplete="off"
                    spellCheck={false}
                    disabled={saveBaseUrlMutation.isPending}
                  />
                  <div className={styles.actions}>
                    <button
                      type="button"
                      className={styles.saveButton}
                      disabled={baseUrlInput.trim() === '' || saveBaseUrlMutation.isPending}
                      onClick={handleSaveBaseUrl}
                    >
                      {saveBaseUrlMutation.isPending
                        ? t.settings.apiKey.baseUrl.verifying
                        : t.settings.apiKey.baseUrl.save}
                    </button>
                    {showClearBaseUrl && (
                      <button
                        type="button"
                        className={styles.deleteButton}
                        disabled={clearBaseUrlMutation.isPending}
                        onClick={() => setBaseUrlClearConfirmOpen(true)}
                      >
                        {t.settings.apiKey.baseUrl.clear}
                      </button>
                    )}
                  </div>
                  {baseUrlErrorMessage && (
                    <p className={styles.errorText}>{baseUrlErrorMessage}</p>
                  )}
                </div>
              )}
            </>
          )}

          <p className={styles.helpText}>{t.settings.apiKey.baseUrl.helpText}</p>
        </div>
      </section>
      )}

      {sections.includes('generation') && <GenerationSettingsSection toast={toast} />}

      {sections.includes('annotation') && <AnnotationSettingsSection toast={toast} />}

      {sections.includes('comfyui') && (
      <section id="comfyui" className={styles.section}>
        <h2 className={styles.sectionHeading}>{t.settings.comfyui.heading}</h2>
        <p className={styles.helpText}>{t.settings.comfyui.intro}</p>

        <ComfyUIStatusPanel toast={toast} />

        <ComfyUITimeoutField toast={toast} />

        <Link to="/settings/comfyui" className={styles.linkButton}>
          {t.settings.comfyui.manageWorkflows}
        </Link>
        {comfyStatusQuery.data?.enabled === false && (
          <p className={styles.helpText}>{t.settings.comfyui.notConnectedHint}</p>
        )}
      </section>
      )}

      {sections.includes('mcp') && <McpSettingsSection toast={toast} />}

      {sections.includes('shareAdmin') && <ShareSettingsSection toast={toast} />}

      <AboutSection />

      <ConfirmDialog
        open={deleteConfirmOpen}
        message={t.settings.apiKey.deleteConfirm.message}
        confirmLabel={t.settings.apiKey.deleteConfirm.confirmLabel}
        onConfirm={() => {
          setDeleteConfirmOpen(false)
          deleteMutation.mutate()
        }}
        onCancel={() => setDeleteConfirmOpen(false)}
      />

      <ConfirmDialog
        open={baseUrlClearConfirmOpen}
        message={t.settings.apiKey.baseUrl.clearConfirm.message}
        confirmLabel={t.settings.apiKey.baseUrl.clearConfirm.confirmLabel}
        onConfirm={() => {
          setBaseUrlClearConfirmOpen(false)
          clearBaseUrlMutation.mutate()
        }}
        onCancel={() => setBaseUrlClearConfirmOpen(false)}
      />

      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </div>
  )
}

function StatusBadge({ view }: { view: ApiKeyStatusView }) {
  return (
    <div className={styles.status} data-state={view.state}>
      <span className={styles.statusDot} aria-hidden="true" />
      <span className={styles.statusTitle}>{view.title}</span>
      {view.detail && <span className={styles.statusDetail}>{view.detail}</span>}
    </div>
  )
}

function BaseUrlDetail({ view }: { view: BaseUrlStatusView }) {
  return (
    <>
      {view.title}
      {view.detail && <> · {view.detail}</>}
    </>
  )
}
