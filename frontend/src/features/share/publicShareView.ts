/**
 * 共有のページで「いま何を開いているか」(ビューア / Run の詳細 / 全画面の系列グラフ)と、URL・
 * 履歴との対応(ADR-0029 3章、2026-10-01 追記)。ルーターの外なので `pushState` / `popstate` で扱う。
 *
 * 全画面の系列グラフの URL(`/s/{トークン}/lineage`)には、広げる前に開いていた Run を書かない。
 * 代わりに履歴の state に持たせ、戻る/進むや「ビューアに戻る」で元の表示(Run の詳細なら
 * その Run)に戻れるようにする。直リンクで開いたときは state が無いので、起点の画像に戻る。
 */
import { buildPublicShareLineagePath, buildPublicSharePath, parsePublicSharePath } from './publicSharePath'

export interface PublicShareView {
  /** Run の詳細を開いている Run(全画面の系列グラフでは、閉じたときに戻る Run)。 */
  runId: string | null
  /** 全画面の系列グラフを開いているか。 */
  lineage: boolean
}

/** 全画面の系列グラフの履歴に持たせる state。 */
interface LineageHistoryState {
  gakeiShareReturnRunId: string | null
}

export function publicShareViewPath(token: string, view: PublicShareView): string {
  return view.lineage ? buildPublicShareLineagePath(token) : buildPublicSharePath(token, view.runId)
}

export function publicShareHistoryState(view: PublicShareView): LineageHistoryState | null {
  return view.lineage ? { gakeiShareReturnRunId: view.runId } : null
}

function returnRunIdFromState(state: unknown): string | null {
  if (typeof state !== 'object' || state === null) return null
  const value = (state as Partial<LineageHistoryState>).gakeiShareReturnRunId
  return typeof value === 'string' ? value : null
}

/** URL と履歴の state から、開いているものを決める(共有のページの URL でなければ null)。 */
export function publicShareViewFromLocation(pathname: string, state: unknown): PublicShareView | null {
  const parsed = parsePublicSharePath(pathname)
  if (!parsed) return null
  if (parsed.lineage) return { runId: returnRunIdFromState(state), lineage: true }
  return { runId: parsed.runId, lineage: false }
}

/** 全画面の系列グラフを開く(開いている Run は、閉じたときに戻るために持っておく)。 */
export function openLineageView(current: PublicShareView): PublicShareView {
  return { runId: current.runId, lineage: true }
}

/** 「ビューアに戻る」: 全画面を閉じ、広げる前の表示(Run の詳細か、画像の情報)に戻る。 */
export function closeLineageView(current: PublicShareView): PublicShareView {
  return { runId: current.runId, lineage: false }
}

export interface LineageNodeTarget {
  view: PublicShareView
  /** ビューアで開く画像(Run のノードなら null。ビューアはその Run の出力に合わせる)。 */
  assetId: string | null
}

/**
 * 全画面の系列グラフでノードを押したときの行き先。どちらも全画面を閉じる。画像のノードは
 * ビューアでその画像を開き(画像の情報に戻る)、Run(Generated)のノードはその Run の詳細を開く。
 */
export function lineageNodeTarget(node: { id: string; type: string | undefined }): LineageNodeTarget | null {
  if (node.type === 'asset') return { view: { runId: null, lineage: false }, assetId: node.id }
  if (node.type === 'run') return { view: { runId: node.id, lineage: false }, assetId: null }
  return null
}
