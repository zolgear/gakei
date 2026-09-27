/**
 * サイドバー(アイコンレール)のパネルをルート配下の任意のページから開く/閉じるためのコンテキスト。
 * 履歴カードの「系列を見る」などから、AppShell が持つサイドバー状態を操作する。
 * `/lineage/:assetId`(系列が主役のページ)は、開いたままだと同じグラフが二重に出るため、
 * マウント中だけ系列パネルを自動で畳む(`collapsePanel`/`selectedPanel` を使う)。
 */
import { createContext, useContext } from 'react'
import type { PanelId } from '../shell/panelStorage'

export interface ResourcePanelContextValue {
  selectedPanel: PanelId | null
  openPanel: (panel: PanelId) => void
  /** 現在のパネルを畳む(選んだ覚えのない他パネルへの影響を避けるため無条件で畳む)。 */
  collapsePanel: () => void
  /**
   * 検索パネルを開いて入力欄にフォーカスする(App バーの虫眼鏡と `/` キー。2026-09-26)。
   * 検索の入力と結果はサイドバーの検索パネルだけが持つ(ADR-0009 6章)。
   */
  focusSearchPanel: () => void
  /** `focusSearchPanel` が呼ばれるたびに増える番号。検索パネルはこれを見てフォーカスする。 */
  searchFocusRequest: number
}

export const ResourcePanelContext = createContext<ResourcePanelContextValue | null>(null)

export function useResourcePanel(): ResourcePanelContextValue {
  const ctx = useContext(ResourcePanelContext)
  if (ctx === null) {
    throw new Error('useResourcePanel must be used inside AppShell')
  }
  return ctx
}
