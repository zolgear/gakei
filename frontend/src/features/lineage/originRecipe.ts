/**
 * PNG に埋め込まれた `gakei.lineage/1`/`gakei.lineage/2` メタ情報(`AssetDetail.origin.meta`、
 * 系列グラフの埋め込み(未検証)Run ノードの `embedded_detail`、ADR-0014)のうち、
 * `run`(レシピ: provider/model/prompt/params)をフォームへ読み込むための変換をまとめた
 * 純粋関数群。`meta`/ノードは誰でも書き換えられる画像の自己申告(未検証)なので、形を検証してから
 * 使う。壊れている・型が違う値は例外を投げず、単に「その項目は無かった」として扱う。
 */
import type { CapabilitiesResponse } from '../../api/client'
import { msg } from '../../i18n'
import { findModel } from '../../lib/capabilities'
import { paramsForRerun } from '../run-form/paramsBuilder'
import type { RunFormState } from '../run-form/types'

export interface OriginRunInfo {
  provider: string | null
  model: string | null
  prompt: string | null
  params: Record<string, string | number | boolean>
}

function extractParams(value: unknown): Record<string, string | number | boolean> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return {}
  const result: Record<string, string | number | boolean> = {}
  for (const [key, v] of Object.entries(value as Record<string, unknown>)) {
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
      result[key] = v
    }
    // オブジェクト・配列・null は型が合わないので捨てる(自己申告の壊れ方に備える)。
  }
  return result
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

/**
 * run 情報を持つオブジェクト(v1 の `meta.run`、v2 の run ノード、埋め込み Run ノードの
 * `embedded_detail`)から共通の形へ変換する。`record` 自体は事前に `asRecord` で検証済みの
 * 前提(このモジュールの中だけで使う)。
 */
function runInfoFromRecord(record: Record<string, unknown>): OriginRunInfo {
  return {
    provider: typeof record.provider === 'string' && record.provider !== '' ? record.provider : null,
    model: typeof record.model === 'string' && record.model !== '' ? record.model : null,
    prompt: typeof record.prompt === 'string' ? record.prompt : null,
    params: extractParams(record.params),
  }
}

/**
 * `gakei.lineage/2` のグラフ形式から、起点(`root`)を生んだ Run ノードを探して読む。
 * `root` への `output` エッジの `source` が Run ノードの id と一致するものを1つ選ぶ
 * (通常は1本しかない)。`nodes`/`edges`/`root` の形が崩れている、該当する Run が無い
 * ときは null(v1 になく run 情報が埋め込まれていない場合も同じ扱いにする)。
 */
function extractOriginRunInfoFromGraph(m: Record<string, unknown>): OriginRunInfo | null {
  const root = m.root
  const nodes = m.nodes
  const edges = m.edges
  if (typeof root !== 'string' || !Array.isArray(nodes) || !Array.isArray(edges)) return null

  const outputEdge = edges
    .map((e) => asRecord(e))
    .find((e) => e !== null && e.kind === 'output' && e.target === root)
  const sourceId = outputEdge?.source
  if (typeof sourceId !== 'string') return null

  const runNode = nodes
    .map((n) => asRecord(n))
    .find((n) => n !== null && n.type === 'run' && n.id === sourceId)
  return runNode ? runInfoFromRecord(runNode) : null
}

/**
 * `meta.run`(v1)、または `meta.root`/`nodes`/`edges`(v2、gakei.lineage/2)を読む。
 * `meta` 自体、または該当する run がオブジェクトとして見つからなければ null を返す
 * (sketch の `source_asset` のみを持つ v1 メタ情報、run に辿り着けない v2 グラフ、
 * または壊れている自己申告)。`provider`/`model` は非空文字列でなければ null に、
 * `prompt` は文字列でなければ null にする(空文字は有効な値として通す)。
 */
export function extractOriginRunInfo(meta: unknown): OriginRunInfo | null {
  const m = asRecord(meta)
  if (m === null) return null
  // v2(gakei.lineage/2)はグラフの形(`nodes` を持つ)で判別する。schema 文字列自体も
  // 自己申告なので当てにせず、実際の構造で分岐する。
  if (Array.isArray(m.nodes)) return extractOriginRunInfoFromGraph(m)
  const run = asRecord(m.run)
  return run ? runInfoFromRecord(run) : null
}

/**
 * 系列グラフの埋め込み(未検証)Run ノードの `embedded_detail`(祖先の PNG に埋め込まれていた
 * 生の Run ノード。`{type:"run", provider, model, prompt, params, ...}`)を直接読む。
 * `extractOriginRunInfo` と検証ロジックを共有する(`runInfoFromRecord`)。
 */
export function extractRunInfoFromEmbeddedNode(embeddedDetail: unknown): OriginRunInfo | null {
  const record = asRecord(embeddedDetail)
  return record ? runInfoFromRecord(record) : null
}

/**
 * 「この設定をフォームに読み込む」ボタンを無効にする理由。無効にすべきでなければ null。
 * `info` が null、または provider/model が読み取れないときは埋め込み内容の不足、
 * capabilities にモデルが見つからないときは今の環境で使えないモデル(別インスタンス・
 * 無効化されたプロバイダー等)。
 */
export function originRecipeDisabledReason(
  info: OriginRunInfo | null,
  caps: CapabilitiesResponse | undefined,
): string | null {
  const t = msg().lineage.originRecipe
  if (info === null || info.provider === null || info.model === null) {
    return t.missingRecipe
  }
  if (caps === undefined) return t.capabilitiesLoading
  if (findModel(caps, info.provider, info.model) === undefined) {
    return t.modelUnavailable
  }
  return null
}

/**
 * フォームへ渡す `RunFormState` を組み立てる。呼び出し側は事前に
 * `originRecipeDisabledReason(info, caps) === null` を確かめてから呼ぶこと(provider/model が
 * 無い場合は呼んでも空文字のフォームになるだけで、例外は出さない)。
 * 入力画像は画像に埋め込んでいないため、`inputs` は常に空にする。`comfyui_*`(ADR-0013)は
 * サーバーがクライアント入力として受け付けないため、`paramsForRerun` で取り除く
 * (`comfyui_seed` があれば `seed` として残す。「同じ設定で新規作成」と同じ扱い)。
 */
export function buildOriginFormState(info: OriginRunInfo): RunFormState {
  return {
    provider: info.provider ?? '',
    model: info.model ?? '',
    prompt: info.prompt ?? '',
    params: paramsForRerun(info.params) as Record<string, string | number | boolean>,
    inputs: [],
    // 埋め込みの生成メタ情報にグループは含めていない(グループはインスタンス内の整理であり、
    // 画像と一緒に持ち出す来歴ではないため)。
    assetGroupId: null,
  }
}
