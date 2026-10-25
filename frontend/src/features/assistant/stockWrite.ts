/**
 * ストックの整理の結果(ADR-0048 13章)。道具の結果をカードに出す形にする純粋関数と、書き込みの後・
 * 戻した後に作り直すクエリ。
 */
import type { QueryClient } from '@tanstack/react-query'
import type { AssistantMessageResponse, AssistantUndoResult, ContentRatingLevel } from '../../api/client'
import { fmt, type Messages } from '../../i18n'
import { isContentRatingLevel } from '../content-rating/contentRating'
import { ASSET_GROUPS_QUERY_KEY } from '../stock/groups/assetGroupQueries'

/** ストックに書き込む道具。 */
export const STOCK_WRITE_TOOLS = [
  'add_tags',
  'remove_tags',
  'set_title',
  'create_group',
  'rename_group',
  'move_to_group',
  'remove_from_group',
] as const

export type StockWriteTool = (typeof STOCK_WRITE_TOOLS)[number]

export function isStockWriteTool(name: string | null | undefined): name is StockWriteTool {
  return (STOCK_WRITE_TOOLS as readonly string[]).includes(name ?? '')
}

export interface StockPreviewAsset {
  assetId: string
  focalPoint: { x: number; y: number } | null
  contentRating: ContentRatingLevel | null
}

export interface StockWriteSummary {
  tool: StockWriteTool
  /** 対象にした枚数。 */
  count: number
  /** 実際に変えた枚数。 */
  changed: number
  tags: string[]
  group: { id: string; name: string } | null
  /** 名前・タイトルの変更前と変更後(それ以外は null)。 */
  before: string | null
  after: string | null
  preview: StockPreviewAsset[]
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function asCount(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function readField(value: unknown, field: 'name' | 'title'): string | null {
  const record = asRecord(value)
  if (!record) return null
  const v = record[field]
  return typeof v === 'string' ? v : ''
}

/** ストックの整理の道具の結果を、カードに出す形にする。該当しなければ null。 */
export function readStockWrite(message: AssistantMessageResponse): StockWriteSummary | null {
  const tool = message.tool_name
  if (!isStockWriteTool(tool) || message.ok !== true) return null
  const result = asRecord(message.result) ?? {}
  const group = asRecord(result.group)
  const preview: StockPreviewAsset[] = []
  for (const entry of (Array.isArray(result.preview) ? result.preview : []).map(asRecord)) {
    if (!entry || typeof entry.asset_id !== 'string') continue
    const point = asRecord(entry.focal_point)
    preview.push({
      assetId: entry.asset_id,
      focalPoint: point && typeof point.x === 'number' && typeof point.y === 'number' ? { x: point.x, y: point.y } : null,
      contentRating: isContentRatingLevel(entry.content_rating) ? entry.content_rating : null,
    })
  }
  const field = tool === 'rename_group' ? 'name' : 'title'
  const hasChange = tool === 'rename_group' || tool === 'set_title'
  return {
    tool,
    count: asCount(result.count),
    changed: asCount(result.changed),
    tags: Array.isArray(result.tags) ? result.tags.filter((t): t is string => typeof t === 'string') : [],
    group:
      group && typeof group.id === 'string' ? { id: group.id, name: typeof group.name === 'string' ? group.name : '' } : null,
    before: hasChange ? readField(result.before, field) : null,
    after: hasChange ? readField(result.after, field) : null,
    preview,
  }
}

/** 戻した結果の文(戻した枚数、飛ばした枚数、作ったグループの扱い)。 */
export function describeStockUndo(t: Messages, tool: StockWriteTool, result: AssistantUndoResult): string {
  const c = t.assistant.stockCard
  const parts: string[] = []
  if (tool === 'rename_group' || tool === 'set_title') {
    parts.push(c.undoneSingle)
  } else if (result.restored > 0) {
    parts.push(fmt(c.undoneCount, { count: result.restored }))
  }
  if (result.skipped > 0) parts.push(fmt(c.skipped, { count: result.skipped }))
  if (result.group_deleted) parts.push(c.groupDeleted)
  if (result.group_kept) parts.push(c.groupKept)
  return parts.join(' ')
}

/**
 * ストックの整理の後に作り直す: ストックの一覧(`['assets', ...]`)、グループの一覧、画像の詳細
 * (ビューアの `['asset', id]`。タグ・タイトル・グループ)、タグの一覧と候補、履歴(タイトルを出す)。
 */
export function invalidateStockQueries(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: ['assets'] })
  void queryClient.invalidateQueries({ queryKey: ASSET_GROUPS_QUERY_KEY })
  void queryClient.invalidateQueries({ queryKey: ['asset'] })
  void queryClient.invalidateQueries({ queryKey: ['tags'] })
  void queryClient.invalidateQueries({ queryKey: ['runs'] })
  void queryClient.invalidateQueries({ queryKey: ['history-assets'] })
  void queryClient.invalidateQueries({ queryKey: ['search'] })
}
