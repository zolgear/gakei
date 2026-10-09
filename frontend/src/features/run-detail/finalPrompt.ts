/**
 * 最終プロンプト(ADR-0030)と展開後のプロンプト(ADR-0038 7章)の表示に使う純粋関数。
 * `run.text_outputs` のうち `role` が `final_prompt` / `final_negative_prompt` の要素だけを扱い、
 * 将来の別の role は無視する。
 *
 * 要素の `output_index` が null(または無い)なら Run のすべての出力に当たる(ComfyUI の最終
 * プロンプト)。数値なら、同じ `output_index` の出力 Asset に当たる(SD WebUI が出力ごとに記録
 * する展開後のプロンプト)。
 */
import type { RunTextOutput } from '../../api/client'

export const FINAL_PROMPT_ROLE = 'final_prompt'
export const FINAL_NEGATIVE_PROMPT_ROLE = 'final_negative_prompt'

/** 折り畳み時に見せる行数(CSS の line-clamp と揃える)。 */
export const FINAL_PROMPT_COLLAPSED_LINES = 4
/** 行数が少なくても、この文字数を超えたら折り畳む(1行が長い文は折り返して何行にもなるため)。 */
export const FINAL_PROMPT_COLLAPSE_CHARS = 240

/** 要素が当たる出力の番号。null は Run のすべての出力。 */
function itemOutputIndex(item: RunTextOutput): number | null {
  return typeof item.output_index === 'number' ? item.output_index : null
}

/**
 * `text_outputs` から、ある出力に当たる要素を1つ取り出す。無ければ null。
 * `outputIndex` に数値を渡すと、`output_index` が一致する要素を優先し、無ければ null(すべての
 * 出力に当たる)の要素を返す。null や省略では、null の要素だけを見る。
 */
export function findFinalPrompt(
  textOutputs: RunTextOutput[] | null | undefined,
  outputIndex: number | null = null,
  role: string = FINAL_PROMPT_ROLE,
): RunTextOutput | null {
  if (!textOutputs) return null
  const ofRole = textOutputs.filter((item) => item.role === role)
  if (outputIndex !== null) {
    const exact = ofRole.find((item) => itemOutputIndex(item) === outputIndex)
    if (exact) return exact
  }
  return ofRole.find((item) => itemOutputIndex(item) === null) ?? null
}

/**
 * 表示する1件。`final` は ComfyUI の最終プロンプト(すべての出力に当たる。今までどおり常に出す)、
 * `expanded` / `expandedNegative` は出力ごとの展開後のプロンプトとネガティブプロンプト。
 */
export interface FinalPromptEntry {
  kind: 'final' | 'expanded' | 'expandedNegative'
  /** 当たる出力の番号(0 始まり)。null はすべての出力。 */
  outputIndex: number | null
  item: RunTextOutput
}

export interface FinalPromptBaseline {
  /** Run のプロンプト(`run.prompt`)。展開後のプロンプトがこれと同じなら出さない。 */
  prompt: string
  /**
   * Run のネガティブプロンプト(`params.negative_prompt`。無ければ "")。展開後のネガティブ
   * プロンプトがこれと同じなら出さない。まだ分からない(Run の詳細を読み込み中)ときは
   * undefined で、展開後のネガティブプロンプトは出さない。
   */
  negativePrompt?: string
}

/** 展開後の文が Run の値と違うか(前後の空白だけの違いは同じとみなす)。 */
function differs(text: string, baseline: string | undefined): boolean {
  if (baseline === undefined) return false
  return text.trim() !== baseline.trim()
}

function entryFor(item: RunTextOutput, baseline: FinalPromptBaseline): FinalPromptEntry | null {
  const outputIndex = itemOutputIndex(item)
  if (item.role === FINAL_PROMPT_ROLE) {
    // すべての出力に当たる最終プロンプト(ComfyUI)は、Run のプロンプトと同じでも出す(ADR-0030)。
    if (outputIndex === null) return { kind: 'final', outputIndex, item }
    return differs(item.text, baseline.prompt) ? { kind: 'expanded', outputIndex, item } : null
  }
  if (item.role === FINAL_NEGATIVE_PROMPT_ROLE) {
    return differs(item.text, baseline.negativePrompt) ? { kind: 'expandedNegative', outputIndex, item } : null
  }
  return null
}

/**
 * ある出力(Asset)について見せる要素。`outputIndex` は Asset の `output_index`(無ければ null)。
 * 展開後のプロンプトは、Run のプロンプトと同じなら出さない(ADR-0038 7章)。
 */
export function finalPromptEntriesForOutput(
  textOutputs: RunTextOutput[] | null | undefined,
  outputIndex: number | null,
  baseline: FinalPromptBaseline,
): FinalPromptEntry[] {
  const entries: FinalPromptEntry[] = []
  for (const role of [FINAL_PROMPT_ROLE, FINAL_NEGATIVE_PROMPT_ROLE]) {
    const item = findFinalPrompt(textOutputs, outputIndex, role)
    const entry = item ? entryFor(item, baseline) : null
    if (entry) entries.push(entry)
  }
  return entries
}

/**
 * Run 全体(Run の詳細)で見せる要素。すべての出力に当たるもの(ComfyUI の最終プロンプト)を先に、
 * 続けて Run の値と違う展開後のプロンプトを出力の順に(同じ出力ではプロンプト、ネガティブの順)並べる。
 * 全部が Run の値と同じなら、展開後のものは1件も出さない。
 */
export function finalPromptEntriesForRun(
  textOutputs: RunTextOutput[] | null | undefined,
  baseline: FinalPromptBaseline,
): FinalPromptEntry[] {
  if (!textOutputs) return []
  const roleOrder = (entry: FinalPromptEntry) => (entry.kind === 'expandedNegative' ? 1 : 0)
  const shared: FinalPromptEntry[] = []
  const perOutput: FinalPromptEntry[] = []
  const seen = new Set<string>()
  for (const item of textOutputs) {
    // 同じ出力・同じ role の重複は最初の1件だけ(findFinalPrompt と同じ選び方)。
    const key = `${item.role}:${itemOutputIndex(item) ?? 'all'}`
    if (seen.has(key)) continue
    seen.add(key)
    const entry = entryFor(item, baseline)
    if (!entry) continue
    if (entry.outputIndex === null) shared.push(entry)
    else perOutput.push(entry)
  }
  perOutput.sort(
    (a, b) => (a.outputIndex ?? 0) - (b.outputIndex ?? 0) || roleOrder(a) - roleOrder(b),
  )
  return [...shared, ...perOutput]
}

/** `run.params` から、比べる基準のネガティブプロンプトを取り出す(無ければ "")。 */
export function baselineNegativePrompt(params: Record<string, unknown> | null | undefined): string {
  const value = params?.negative_prompt
  return typeof value === 'string' ? value : ''
}

/**
 * 見出しに添えるノードのラベル。登録画面の選択肢(`nodeOptionLabel`)と同じ
 * "ID: class_type — title" の形(title が無ければ省く)。node_id も class_type も無ければ null。
 */
export function finalPromptNodeLabel(item: RunTextOutput): string | null {
  const nodeId = item.node_id ?? null
  const classType = item.class_type ?? null
  if (nodeId === null && classType === null) return null
  const base = nodeId === null ? classType : `${nodeId}: ${classType ?? '?'}`
  return item.title ? `${base} — ${item.title}` : base
}

/** 折り畳みのボタンを出すか(行数か文字数が閾値を超えるか)。 */
export function isFinalPromptCollapsible(text: string): boolean {
  if (text.length > FINAL_PROMPT_COLLAPSE_CHARS) return true
  return text.split('\n').length > FINAL_PROMPT_COLLAPSED_LINES
}

/** 表示する要素の選び方(`FinalPromptSection` の props の一部)。 */
export interface FinalPromptSelection {
  textOutputs: RunTextOutput[] | null | undefined
  /** 表示する出力(Asset の `output_index`。無ければ null)。省くと Run 全体の表示になる。 */
  outputIndex?: number | null
  /** Run のプロンプト。展開後のプロンプトがこれと同じなら出さない(省くと展開後のものは出さない)。 */
  prompt?: string
  /** Run のネガティブプロンプト(`params.negative_prompt`、無ければ "")。省くと展開後のネガティブは出さない。 */
  negativePrompt?: string
}

/** 表示する要素を選ぶ(`outputIndex` の有無で、その出力か Run 全体か)。 */
export function selectFinalPromptEntries(selection: FinalPromptSelection): FinalPromptEntry[] {
  const baseline = { prompt: selection.prompt ?? '', negativePrompt: selection.negativePrompt }
  const all =
    selection.outputIndex === undefined
      ? finalPromptEntriesForRun(selection.textOutputs, baseline)
      : finalPromptEntriesForOutput(selection.textOutputs, selection.outputIndex, baseline)
  // prompt を渡さない呼び出し(展開後のものを比べられない)では、展開後のものは出さない。
  return selection.prompt === undefined ? all.filter((entry) => entry.kind === 'final') : all
}
