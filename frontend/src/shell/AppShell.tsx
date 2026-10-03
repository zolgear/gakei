/**
 * 全ルート共通のレイアウト。App バー + アイコンレール/サイドバー + メイン領域。
 * 狭い幅(767px以下)ではアイコンレール/サイドバーをドロワーにする。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Outlet, useLocation } from 'react-router'
import { AppBar } from './AppBar'
import { IconRail } from './IconRail'
import { SidebarPanel } from './SidebarPanel'
import { loadSelectedPanel, saveSelectedPanel, type PanelId } from './panelStorage'
import { autoCollapsesPanel, displayedPanel, selectPanel, type PanelOverride } from './panelVisibility'
import { ResourcePanelContext } from '../context/useResourcePanel'
import { useScrollRestoration } from './useScrollRestoration'
import { MissingApiKeyBanner } from '../features/settings/MissingApiKeyBanner'
import { useFaviconProgress } from '../features/favicon/useFaviconProgress'
import { useI18n } from '../i18n'
import styles from './AppShell.module.css'

/**
 * リソース用サイドバー(アイコンレール + パネル)をメイン領域のどちら側に置くか(実験中、
 * 2026-09-26)。生成画面の入力欄を左に固定する構成(ADR-0009 1章・2026-09-26 追記の
 * サイドバー配置)と組み合わせ、「左: 入力 / 中央: 結果 / 右: リソース」を試す。
 * 採用が決まったらこの定数ごと片側に固定し、ADR-0009 を更新する。
 */
const RESOURCE_SIDE: 'left' | 'right' = 'right'

export function AppShell() {
  const { t } = useI18n()
  const location = useLocation()
  // タブの favicon で Run の進捗を示す(ADR-0009 8章)。App バーのロゴも同じ状態を読む
  // (useFaviconIconState、AppBar.tsx)。
  useFaviconProgress()
  // 利用者が覚えさせた選択(localStorage)。マップのようにパネルを自動で畳むページでは、これとは
  // 別に「その訪問の間だけの選択」(`panelOverride`)を持ち、覚えた選択は書き換えない
  // (panelVisibility.ts。2026-10-04、ユーザーの指示)。
  const [rememberedPanel, setRememberedPanel] = useState<PanelId | null>(() => loadSelectedPanel())
  const collapsed = autoCollapsesPanel(location.pathname)
  const [panelOverride, setPanelOverride] = useState<PanelOverride>(undefined)
  // 畳むページに入った/出たら、その訪問の間だけの選択を捨てる(描画中に直す。effect だと1フレーム
  // 前の選択が見えてしまう)。
  const [prevCollapsed, setPrevCollapsed] = useState(collapsed)
  if (prevCollapsed !== collapsed) {
    setPrevCollapsed(collapsed)
    setPanelOverride(undefined)
  }
  const selectedPanel = displayedPanel(rememberedPanel, collapsed, panelOverride)
  const [drawerOpen, setDrawerOpen] = useState(false)
  // App バーの虫眼鏡 / `/` キーから検索パネルにフォーカスを渡すための要求番号。
  const [searchFocusRequest, setSearchFocusRequest] = useState(0)
  const mainRef = useRef<HTMLElement | null>(null)
  // <main> 自身がスクロールするページ(Run詳細・ビューア・スタジオ等)向け。履歴のように
  // ページ側に専用の .scrollArea がある場合はそちらが実際のスクロールコンテナになり、
  // ここでは(namespace が違うので)干渉しない。
  useScrollRestoration(mainRef, 'main')

  // ルートが変わったら(ナビゲーションが起きたら)モバイルのドロワーは閉じる。
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  // アイコンレールは常にパネルを開閉する(同じものをもう一度押すと畳む)。以前は系列ページで
  // 「系列」、履歴ページで「履歴」などを押してもパネルを開かず、ページにフォーカスするだけに
  // していたが、押せるのに反応しない違和感の方が大きく、開いても壊れるものは無いのでやめた
  // (2026-09-26。メインと同じ内容が二重に見えるだけ)。
  function handleSelectPanel(panel: PanelId) {
    const next = selectPanel(rememberedPanel, collapsed, panelOverride, panel)
    setRememberedPanel(next.remembered)
    setPanelOverride(next.override)
    if (next.save) saveSelectedPanel(next.remembered)
  }

  // ページから開く/畳む。畳むページではその訪問の間だけ従い、覚えた選択は変えない。
  function setPanelFromPage(panel: PanelId | null) {
    if (collapsed) {
      setPanelOverride(panel)
      return
    }
    setRememberedPanel(panel)
    saveSelectedPanel(panel)
  }

  // 履歴カードの「系列を見る」など、ルート配下のページから特定のパネルを開く/閉じるための窓口。
  // デスクトップでは常にインライン表示なので setDrawerOpen(true) は無害(CSSで隠れる)。
  const resourcePanelValue = useMemo(
    () => ({
      selectedPanel,
      openPanel: (panel: PanelId) => {
        setPanelFromPage(panel)
        setDrawerOpen(true)
      },
      collapsePanel: () => setPanelFromPage(null),
      focusSearchPanel: () => {
        setPanelFromPage('search')
        setDrawerOpen(true)
        setSearchFocusRequest((n) => n + 1)
      },
      searchFocusRequest,
    }),
    // setPanelFromPage は collapsed だけに依存する。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedPanel, collapsed, searchFocusRequest],
  )

  return (
    <ResourcePanelContext.Provider value={resourcePanelValue}>
      <div className={styles.shell}>
        <AppBar drawerOpen={drawerOpen} onToggleDrawer={() => setDrawerOpen((v) => !v)} />

        <div className={styles.body}>
          <div className={styles.resourceDesktop} data-side={RESOURCE_SIDE}>
            <IconRail selected={selectedPanel} onSelect={handleSelectPanel} />
            <SidebarPanel selected={selectedPanel} resizable side={RESOURCE_SIDE} />
          </div>

          {drawerOpen && (
            <div className={styles.drawerOverlay}>
              <button
                type="button"
                aria-label={t.shell.drawer.closeMenu}
                className={styles.drawerBackdrop}
                onClick={() => setDrawerOpen(false)}
              />
              <div className={styles.drawerPanel}>
                <IconRail selected={selectedPanel} onSelect={handleSelectPanel} />
                <SidebarPanel selected={selectedPanel} />
              </div>
            </div>
          )}

          <main ref={mainRef} className={`${styles.main} dot-grid`} tabIndex={-1}>
            <MissingApiKeyBanner />
            <Outlet />
          </main>
        </div>

      </div>
    </ResourcePanelContext.Provider>
  )
}
