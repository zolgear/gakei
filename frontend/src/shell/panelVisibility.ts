/**
 * ページによってサイドバーのパネルを自動で畳む規則(2026-10-04、ユーザーの指示。ADR-0033 8章)。
 *
 * マップ(`/map`)は canvas を広く使い、サイドバーのパネル(系列グラフなど)とは連動しないので、
 * 開いている間はパネルを畳んで見せる。ただし利用者が覚えさせた選択(`gakei:selected-panel`)は
 * 書き換えず、マップを離れたら元のパネルに戻す。マップの上でアイコンレールから明示的に開いたときは
 * その訪問の間だけ従う(`override`。保存はしない)。
 */
import type { PanelId } from './panelStorage'

/** パネルを自動で畳むページか。 */
export function autoCollapsesPanel(pathname: string): boolean {
  return pathname === '/map' || pathname.startsWith('/map/')
}

/**
 * 畳むページでの、その訪問の間だけの選択。`undefined` は「まだ触っていない」(畳んだまま)、
 * `null` は「利用者が明示的に畳んだ」。
 */
export type PanelOverride = PanelId | null | undefined

/** 実際に出すパネル。 */
export function displayedPanel(remembered: PanelId | null, collapsed: boolean, override: PanelOverride): PanelId | null {
  if (!collapsed) return remembered
  return override === undefined ? null : override
}

/**
 * アイコンレールで `panel` を押したときの次の状態。同じものをもう一度押すと畳む。
 * 畳むページでは `override` だけを変え、覚えた選択(`remembered`)は変えない(`save` は false)。
 */
export function selectPanel(
  remembered: PanelId | null,
  collapsed: boolean,
  override: PanelOverride,
  panel: PanelId,
): { remembered: PanelId | null; override: PanelOverride; save: boolean } {
  const shown = displayedPanel(remembered, collapsed, override)
  const next = shown === panel ? null : panel
  if (collapsed) return { remembered, override: next, save: false }
  return { remembered: next, override, save: true }
}
