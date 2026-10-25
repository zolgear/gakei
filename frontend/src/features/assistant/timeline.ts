/**
 * 会話のメッセージを、パネルに並べる項目に組み立てる純粋関数(ADR-0048 2章)。
 *
 * - 利用者の発言は吹き出し、アシスタントの本文は本文の段。
 * - 道具の結果はカードの種類を決める: `propose_prompt` はプロンプトのカード、プロンプトセットの
 *   書き込みは結果のカード(元に戻す)、`generate_image` は Run のカード、`show_assets` は
 *   画像の格子のカード(10章)、`update_form` は「フォームを変更」のカード(11章)、ストックの整理は
 *   整理の結果のカード(13章)。それ以外(`search_assets` などの捜索も)と失敗は、折りたたみの
 *   小さな行。
 * - 画像を LLM に送る前に許可を求めた道具(`view_assets`・`view_result`。14.1)は、許可のカード。
 *   答えを待っている間は、実行中の行の代わりに許可のカード(ボタンつき)を出す。
 * - 道具の呼び出しの引数は、結果のメッセージには無いので、前のアシスタントのメッセージの
 *   `tool_calls` から引く。
 */
import type { AssistantMessageResponse, AssistantToolCallView, ContentRatingLevel } from '../../api/client'
import { isContentRatingLevel } from '../content-rating/contentRating'
import type { PendingPermission, PendingToolCall } from './assistantSession'
import { readFormWrite } from './formWrite'
import { readStockWrite } from './stockWrite'

/** プロンプトセットに書き込む道具(ADR-0048 4章)。 */
export const WRITE_TOOLS: ReadonlySet<string> = new Set([
  'create_prompt_set',
  'rename_prompt_set',
  'add_prompt_set_items',
  'update_prompt_set_item',
])

export type ToolCardKind = 'prompt' | 'write' | 'runs' | 'assets' | 'form' | 'stock' | 'permission' | 'row'

export function toolCardKind(message: AssistantMessageResponse): ToolCardKind {
  if (message.ok !== true) return 'row'
  if (message.image_permission) return 'permission'
  const name = message.tool_name ?? ''
  if (name === 'propose_prompt' && typeof message.result?.prompt === 'string') return 'prompt'
  if (WRITE_TOOLS.has(name)) return 'write'
  if (name === 'generate_image' && (message.run_ids ?? []).length > 0) return 'runs'
  if (name === 'show_assets' && readShownAssets(message).assets.length > 0) return 'assets'
  if (readFormWrite(message) !== null) return 'form'
  if (readStockWrite(message) !== null) return 'stock'
  return 'row'
}

// -- 見つけた画像(`show_assets`。ADR-0048 10章) -------------------------------------

export interface ShownAsset {
  assetId: string
  title: string
  width: number | null
  height: number | null
  focalPoint: { x: number; y: number } | null
  /**
   * レーティング(ADR-0049)。null は未判定。レーティングより前に保存した会話の結果には欄が無いので
   * undefined(マスクしない)。
   */
  contentRating: ContentRatingLevel | null | undefined
}

export interface ShownAssets {
  title: string
  assets: ShownAsset[]
}

function readFocalPoint(value: unknown): ShownAsset['focalPoint'] {
  const record = asRecord(value)
  if (!record || typeof record.x !== 'number' || typeof record.y !== 'number') return null
  return { x: record.x, y: record.y }
}

function readContentRating(entry: Record<string, unknown>): ShownAsset['contentRating'] {
  if (!('content_rating' in entry)) return undefined
  return isContentRatingLevel(entry.content_rating) ? entry.content_rating : null
}

/** `show_assets` の結果を、カードに出す形にする。形の崩れた項目は落とす。 */
export function readShownAssets(message: AssistantMessageResponse): ShownAssets {
  const result = asRecord(message.result) ?? {}
  const raw = Array.isArray(result.assets) ? result.assets : []
  const assets: ShownAsset[] = []
  for (const entry of raw.map(asRecord)) {
    if (!entry || typeof entry.asset_id !== 'string') continue
    assets.push({
      assetId: entry.asset_id,
      title: asText(entry.title),
      width: typeof entry.width === 'number' ? entry.width : null,
      height: typeof entry.height === 'number' ? entry.height : null,
      focalPoint: readFocalPoint(entry.focal_point),
      contentRating: readContentRating(entry),
    })
  }
  return { title: asText(result.title), assets }
}

export type TimelineItem =
  | { kind: 'user'; key: string; message: AssistantMessageResponse }
  | { kind: 'assistant'; key: string; message: AssistantMessageResponse }
  | {
      kind: 'tool'
      key: string
      card: ToolCardKind
      message: AssistantMessageResponse
      call: AssistantToolCallView | null
    }
  | { kind: 'pending'; key: string; call: PendingToolCall }
  | { kind: 'permission'; key: string; permission: PendingPermission }
  | { kind: 'streaming'; key: string; text: string }

export function buildTimeline(
  messages: readonly AssistantMessageResponse[],
  pendingCalls: readonly PendingToolCall[] = [],
  streamingText = '',
  pendingPermissions: readonly PendingPermission[] = [],
): TimelineItem[] {
  const calls = new Map<string, AssistantToolCallView>()
  const items: TimelineItem[] = []
  for (const message of messages) {
    if (message.role === 'user') {
      items.push({ kind: 'user', key: message.id, message })
    } else if (message.role === 'assistant') {
      for (const call of message.tool_calls ?? []) calls.set(call.id, call)
      // 本文の無い応答(道具を呼んだだけ)は段を出さない。失敗・中断は本文が無くても出す。
      if ((message.text ?? '').length > 0 || message.status === 'error' || message.status === 'stopped') {
        items.push({ kind: 'assistant', key: message.id, message })
      }
    } else {
      const call = message.tool_call_id ? (calls.get(message.tool_call_id) ?? null) : null
      items.push({ kind: 'tool', key: message.id, card: toolCardKind(message), message, call })
    }
  }
  for (const call of pendingCalls) {
    const permission = pendingPermissions.find((p) => p.toolCallId === call.id)
    if (permission) items.push({ kind: 'permission', key: `permission-${permission.requestId}`, permission })
    else items.push({ kind: 'pending', key: `pending-${call.id}`, call })
  }
  if (streamingText.length > 0) items.push({ kind: 'streaming', key: 'streaming', text: streamingText })
  return items
}

// -- プロンプトセットの書き込みの結果(ADR-0048 4章) --------------------------------

export interface WriteItemView {
  label: string
  text: string
}

/** 変更前と変更後の1項目(名前・ラベル・本文・並び順)。 */
export interface WriteFieldChange {
  field: 'name' | 'label' | 'text' | 'position'
  before: string
  after: string
}

export interface WriteSummary {
  tool: 'create_prompt_set' | 'rename_prompt_set' | 'add_prompt_set_items' | 'update_prompt_set_item'
  promptSetId: string | null
  /** 対象のセットの名前(分からなければ空)。 */
  setName: string
  /** 作った・足した項目。 */
  items: WriteItemView[]
  /** 変えた項目(名前の変更・項目の更新)。 */
  changes: WriteFieldChange[]
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function asText(value: unknown): string {
  if (value === null || value === undefined) return ''
  return String(value)
}

function readItems(value: unknown): WriteItemView[] {
  if (!Array.isArray(value)) return []
  return value
    .map(asRecord)
    .filter((item): item is Record<string, unknown> => item !== null)
    .map((item) => ({ label: asText(item.label), text: asText(item.text) }))
}

/** 書き込みの道具の結果を、カードに出す形にする。書き込みの道具でなければ null。 */
export function describeWrite(message: AssistantMessageResponse): WriteSummary | null {
  const tool = message.tool_name ?? ''
  if (!WRITE_TOOLS.has(tool)) return null
  const result = asRecord(message.result) ?? {}
  const before = asRecord(result.before)
  const after = asRecord(result.after) ?? {}
  const promptSetId = typeof result.prompt_set_id === 'string' ? result.prompt_set_id : null
  const summary: WriteSummary = {
    tool: tool as WriteSummary['tool'],
    promptSetId,
    setName: asText(result.prompt_set_name),
    items: [],
    changes: [],
  }
  switch (summary.tool) {
    case 'create_prompt_set':
      summary.setName = asText(after.name)
      summary.items = readItems(after.items)
      break
    case 'rename_prompt_set':
      summary.setName = asText(after.name)
      summary.changes = [{ field: 'name', before: asText(before?.name), after: asText(after.name) }]
      break
    case 'add_prompt_set_items':
      summary.items = readItems(after.items)
      break
    case 'update_prompt_set_item':
      for (const field of ['label', 'text', 'position'] as const) {
        const b = asText(before?.[field])
        const a = asText(after[field])
        if (b !== a) summary.changes.push({ field, before: b, after: a })
      }
      break
  }
  return summary
}
