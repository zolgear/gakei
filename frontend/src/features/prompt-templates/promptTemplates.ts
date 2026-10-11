/**
 * プロンプトテンプレート(実験)の純粋関数。画面(パネル・編集・生成フォームの選択)から
 * 見た目を持たないロジックだけを切り出したもの。
 *
 * 3つの役目がある。
 *
 * 1. 生成フォームでの選択状態(`TemplateSelectionState`)の組み立てと、`POST /api/runs` /
 *    `POST /api/prompt-templates/{id}/build` に送る `selections` への変換。
 * 2. 組み合わせが覆うスロットの判定(`lockedSlotIds`)。覆われたスロットに指定を送ると
 *    サーバーが 422 を返すので、画面でロックし、送る `selections` からも外す。
 *    「ランダムに1つ引く」はどれが引かれるか事前に分からないため、引かれる可能性のある
 *    組み合わせが覆うスロットをすべてロックする(そうしないと Run ごとの引き直しで 422 になる)。
 * 3. 編集画面の下書き(`TemplateDraft`)と `PUT`(丸ごと置換)の本文の相互変換。残したい子は
 *    必ず id を付けて送り、本文に無い子はサーバーが論理削除する。新しい候補を組み合わせから
 *    指すときは、候補に `ref`(下書きの一時キー)を付けて `variant_ref` で指す。
 */
import type {
  PromptBuildResponse,
  PromptComboInput,
  PromptSelections,
  PromptTemplateResponse,
  PromptTemplateSummary,
  PromptTemplateWriteRequest,
} from '../../api/client'

/** 一覧の react-query のキー(作成・保存・削除で無効にする)。 */
export const PROMPT_TEMPLATES_QUERY_KEY = ['prompt-templates'] as const

/** 画面で選べるスロットの解決のしかた(`combo` はサーバーの結果にだけ出る)。 */
export type SlotMode = 'fixed' | 'random' | 'off' | 'text'
export type SlotDefaultMode = 'random' | 'off'

export const SLOT_MODES: SlotMode[] = ['fixed', 'random', 'off', 'text']

/**
 * 組み合わせの使い方。画面ではこの3択のラジオで選ぶ。
 *
 * サーバーは「明示的に選んだ分 + さらにランダム1つ」の併用も受けるが、分かりにくいので
 * 画面では出さない(`toPromptSelections` もこの3択しか組み立てない)。
 */
export type ComboMode = 'off' | 'pick' | 'random'

export const COMBO_MODES: ComboMode[] = ['off', 'pick', 'random']

// -- 生成フォームでの選択 ----------------------------------------------------

export interface SlotSelectionState {
  mode: SlotMode
  /** mode=fixed のときに使う候補。候補が無いテンプレートでは null。 */
  variantId: string | null
  /** mode=text のときに使うその場のテキスト。 */
  text: string
}

export interface TemplateSelectionState {
  /** 組み合わせの使い方(3択)。 */
  comboMode: ComboMode
  /**
   * 「選ぶ」で選んだ組み合わせ(並び順のまま先頭から解決される)。
   *
   * `comboMode` が `pick` 以外のときは効かないが、モードを行き来したときに選び直さずに
   * 済むよう覚えておく(`pick` に戻せばそのまま復元される)。
   */
  comboIds: string[]
  /** スロット id → 選択。 */
  slots: Record<string, SlotSelectionState>
}

export function emptySelectionState(): TemplateSelectionState {
  return { comboMode: 'off', comboIds: [], slots: {} }
}

/** いま実際に効いている「選んだ組み合わせ」(`pick` 以外では空)。 */
export function effectiveComboIds(state: TemplateSelectionState): string[] {
  return state.comboMode === 'pick' ? state.comboIds : []
}

/** テンプレートを選んだ直後の選択状態。各スロットは `default_mode` から始める。 */
export function initialSelectionState(template: PromptTemplateResponse): TemplateSelectionState {
  const slots: Record<string, SlotSelectionState> = {}
  for (const slot of template.slots ?? []) {
    slots[slot.id] = {
      mode: slot.default_mode,
      variantId: slot.variants?.[0]?.id ?? null,
      text: '',
    }
  }
  return { comboMode: 'off', comboIds: [], slots }
}

/** 選択状態にそのテンプレートに無いスロットが混ざらないよう整える(テンプレートを保存し直した後)。 */
export function reconcileSelectionState(
  template: PromptTemplateResponse,
  state: TemplateSelectionState,
): TemplateSelectionState {
  const next = initialSelectionState(template)
  for (const slot of template.slots ?? []) {
    const prev = state.slots[slot.id]
    if (!prev) continue
    const variantIds = new Set((slot.variants ?? []).map((v) => v.id))
    // 候補が消えていたら先頭(initialSelectionState が入れた値)に戻す。
    const keepVariant = prev.variantId !== null && variantIds.has(prev.variantId)
    next.slots[slot.id] = {
      mode: prev.mode,
      variantId: keepVariant ? prev.variantId : next.slots[slot.id].variantId,
      text: prev.text,
    }
  }
  const comboIds = new Set((template.combos ?? []).map((c) => c.id))
  return {
    comboMode: state.comboMode,
    comboIds: state.comboIds.filter((id) => comboIds.has(id)),
    slots: next.slots,
  }
}

/** 候補 id → スロット id。 */
export function variantSlotMap(template: PromptTemplateResponse): Map<string, string> {
  const map = new Map<string, string>()
  for (const slot of template.slots ?? []) {
    for (const variant of slot.variants ?? []) map.set(variant.id, slot.id)
  }
  return map
}

/**
 * 組み合わせが覆うスロット id。覆われたスロットは画面でロックし、`selections.slots` からも外す。
 *
 * - 「選ぶ」: 選んだ組み合わせが指す候補のスロット。
 * - 「ランダムに1つ引く」: どれかが引かれるので、組み合わせが指すスロットをすべて
 *   (引かれる可能性がある分)ロックする。Run ごとに引き直してもサーバーが 422 にしない。
 */
export function lockedSlotIds(template: PromptTemplateResponse, state: TemplateSelectionState): Set<string> {
  const slotOf = variantSlotMap(template)
  const locked = new Set<string>()
  const chosen = new Set(effectiveComboIds(state))
  const random = state.comboMode === 'random'
  for (const combo of template.combos ?? []) {
    // 「選ぶ」で選んだものは必ず覆う。「ランダム」のときは、どれが引かれるか分からないので
    // すべてをロックの対象にする。
    if (!chosen.has(combo.id) && !random) continue
    for (const variantId of combo.variant_ids ?? []) {
      const slotId = slotOf.get(variantId)
      if (slotId !== undefined) locked.add(slotId)
    }
  }
  return locked
}

/**
 * `selections`(API の本文)を作る。覆われたスロットは外す。`fixed` なのに候補が選べていない
 * ときは `random` に落とす(サーバーが 422 にするのを避ける)。
 */
export function toPromptSelections(
  template: PromptTemplateResponse,
  state: TemplateSelectionState,
): PromptSelections {
  const locked = lockedSlotIds(template, state)
  const combos: PromptSelections['combos'] = []
  for (const id of effectiveComboIds(state)) combos.push({ id, random: false })
  if (state.comboMode === 'random') combos.push({ id: null, random: true })

  const slots: Record<string, { mode: SlotMode; variant_id?: string | null; text?: string | null }> = {}
  for (const slot of template.slots ?? []) {
    if (locked.has(slot.id)) continue
    const selection = state.slots[slot.id]
    if (!selection) continue
    if (selection.mode === 'fixed') {
      if (selection.variantId === null) {
        slots[slot.id] = { mode: 'random' }
        continue
      }
      slots[slot.id] = { mode: 'fixed', variant_id: selection.variantId }
      continue
    }
    if (selection.mode === 'text') {
      slots[slot.id] = { mode: 'text', text: selection.text }
      continue
    }
    slots[slot.id] = { mode: selection.mode }
  }
  return { combos, slots }
}

/** その選択で生成できるか(モードが text なのに空、など送っても意味が無い状態を弾く)。 */
export function selectionBlockReasons(
  template: PromptTemplateResponse,
  state: TemplateSelectionState,
  messages: { textRequired: string; noSlots: string },
): string[] {
  const reasons: string[] = []
  const slots = template.slots ?? []
  if (slots.length === 0) {
    reasons.push(messages.noSlots)
    return reasons
  }
  const locked = lockedSlotIds(template, state)
  for (const slot of slots) {
    if (locked.has(slot.id)) continue
    const selection = state.slots[slot.id]
    if (selection?.mode === 'text' && selection.text.trim() === '') {
      reasons.push(messages.textRequired)
      break
    }
  }
  return reasons
}

// -- 組み立ての内訳(`prompt_build` と build の応答) --------------------------

/** `prompt_build` の slots[] の1件。`mode` は `combo` も取る(組み合わせが覆ったスロット)。 */
export interface PromptBuildSlot {
  slotId: string
  name: string
  mode: SlotMode | 'combo'
  variantId: string | null
  variantName: string | null
  text: string
}

export interface PromptBuildInfo {
  templateId: string | null
  templateName: string
  separator: string
  combos: { id: string; name: string }[]
  slots: PromptBuildSlot[]
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asStringOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

/**
 * `run.prompt_build`(サーバーは中身の型を持たない JSON として返す)を画面で使う形にする。
 * 形が違う・無いときは null(テンプレートを使っていない Run)。
 */
export function parsePromptBuild(value: unknown): PromptBuildInfo | null {
  const record = asRecord(value)
  if (record === null) return null
  const rawSlots = Array.isArray(record.slots) ? record.slots : []
  const rawCombos = Array.isArray(record.combos) ? record.combos : []
  const slots: PromptBuildSlot[] = []
  for (const raw of rawSlots) {
    const slot = asRecord(raw)
    if (slot === null) continue
    const mode = asString(slot.mode)
    slots.push({
      slotId: asString(slot.slot_id),
      name: asString(slot.name),
      mode:
        mode === 'fixed' || mode === 'random' || mode === 'off' || mode === 'text' || mode === 'combo'
          ? mode
          : 'random',
      variantId: asStringOrNull(slot.variant_id),
      variantName: asStringOrNull(slot.variant_name),
      text: asString(slot.text),
    })
  }
  const combos: { id: string; name: string }[] = []
  for (const raw of rawCombos) {
    const combo = asRecord(raw)
    if (combo === null) continue
    combos.push({ id: asString(combo.id), name: asString(combo.name) })
  }
  // テンプレートの名前もスロットも無いなら、テンプレートの内訳とは見ない。
  if (slots.length === 0 && asString(record.template_name) === '') return null
  return {
    templateId: asStringOrNull(record.template_id),
    templateName: asString(record.template_name),
    separator: asString(record.separator, ', '),
    combos,
    slots,
  }
}

/** 今回の引きで組み合わせが覆ったスロット id(build の応答の `mode === 'combo'`)。 */
export function comboFilledSlotIds(build: PromptBuildResponse | undefined): Set<string> {
  const info = build ? parsePromptBuild(build.build) : null
  const ids = new Set<string>()
  for (const slot of info?.slots ?? []) {
    if (slot.mode === 'combo') ids.add(slot.slotId)
  }
  return ids
}

// -- 編集画面の下書き --------------------------------------------------------

export interface VariantDraft {
  /** 下書きの中だけの一時キー(React の key と、組み合わせからの参照に使う)。 */
  key: string
  /** 既存の候補の id。新しく足した候補は null。 */
  id: string | null
  name: string
  text: string
  weight: number
}

export interface SlotDraft {
  key: string
  id: string | null
  name: string
  defaultMode: SlotDefaultMode
  variants: VariantDraft[]
}

export interface ComboDraft {
  key: string
  id: string | null
  name: string
  weight: number
  /** 指している候補(`VariantDraft.key`)。新しい候補も指せるように id ではなくキーで持つ。 */
  variantKeys: string[]
}

export interface TemplateDraft {
  name: string
  separator: string
  slots: SlotDraft[]
  combos: ComboDraft[]
}

let keyCounter = 0

/** 下書きの中だけで使う一時キー。 */
export function nextDraftKey(prefix: string): string {
  keyCounter += 1
  return `${prefix}-${keyCounter}`
}

export function emptyTemplateDraft(name = ''): TemplateDraft {
  return { name, separator: ', ', slots: [], combos: [] }
}

export function newSlotDraft(name: string): SlotDraft {
  return { key: nextDraftKey('slot'), id: null, name, defaultMode: 'random', variants: [] }
}

export function newVariantDraft(): VariantDraft {
  return { key: nextDraftKey('variant'), id: null, name: '', text: '', weight: 1 }
}

export function newComboDraft(name: string): ComboDraft {
  return { key: nextDraftKey('combo'), id: null, name, weight: 1, variantKeys: [] }
}

/**
 * 新しいスロット・組み合わせに付ける連番。`label(n)` が既にある名前とぶつからない、1 から
 * 数えて最小の `n` を返す。
 *
 * 単に「個数 + 1」にすると、3つ作って2つ目を消してから足したときに重複する。名前の書式は
 * 言語で変わるので、組み立ては呼び出し側の `label`(i18n の文言 + `fmt`)に任せる。
 */
export function nextUnusedNumber(existingNames: string[], label: (n: number) => string): number {
  const used = new Set(existingNames.map((name) => name.trim()))
  let n = 1
  while (used.has(label(n).trim())) n += 1
  return n
}

/** サーバーから読んだテンプレートを下書きにする。既存の子はキーに id をそのまま使う。 */
export function templateToDraft(template: PromptTemplateResponse): TemplateDraft {
  return {
    name: template.name,
    separator: template.separator,
    slots: (template.slots ?? []).map((slot) => ({
      key: slot.id,
      id: slot.id,
      name: slot.name,
      defaultMode: slot.default_mode,
      variants: (slot.variants ?? []).map((variant) => ({
        key: variant.id,
        id: variant.id,
        name: variant.name ?? '',
        text: variant.text,
        weight: variant.weight,
      })),
    })),
    combos: (template.combos ?? []).map((combo) => ({
      key: combo.id,
      id: combo.id,
      name: combo.name,
      weight: combo.weight,
      // 既存の候補はキー = id。
      variantKeys: [...(combo.variant_ids ?? [])],
    })),
  }
}

/**
 * 下書きを `POST` / `PUT` の本文にする。
 *
 * - 既存の子は `id` を付けて残す(付けないとサーバーが論理削除する)。
 * - 新しい候補は `ref`(下書きのキー)を付け、組み合わせからは `variant_ref` で指す。
 * - テキストが空の候補は送らない(サーバーが 422 にする)。消えた候補を指していた組み合わせの
 *   参照も落とし、指す先が無くなった組み合わせは送らない(サーバーが `items` を1件以上求める)。
 */
export function draftToWriteRequest(draft: TemplateDraft): PromptTemplateWriteRequest {
  const liveKeys = liveVariantKeys(draft)
  const slots = draft.slots.map((slot) => {
    const variants = slot.variants.filter((variant) => variant.text.trim() !== '')
    return {
      ...(slot.id !== null ? { id: slot.id } : {}),
      name: slot.name.trim(),
      default_mode: slot.defaultMode,
      variants: variants.map((variant) => ({
        ...(variant.id !== null ? { id: variant.id } : { ref: variant.key }),
        name: variant.name.trim() === '' ? null : variant.name.trim(),
        text: variant.text,
        weight: variant.weight,
      })),
    }
  })

  const variantById = new Map<string, VariantDraft>()
  for (const slot of draft.slots) {
    for (const variant of slot.variants) variantById.set(variant.key, variant)
  }

  const combos: PromptComboInput[] = []
  for (const combo of draft.combos) {
    const items = combo.variantKeys
      .filter((key) => liveKeys.has(key))
      .map((key) => {
        const variant = variantById.get(key)
        return variant?.id !== null && variant?.id !== undefined
          ? { variant_id: variant.id }
          : { variant_ref: key }
      })
    if (items.length === 0) continue
    combos.push({
      ...(combo.id !== null ? { id: combo.id } : {}),
      name: combo.name.trim(),
      weight: combo.weight,
      items,
    })
  }

  return { name: draft.name.trim(), separator: draft.separator, slots, combos }
}

/**
 * 下書きの中で、保存を断る理由。どの項目のことかが分かるよう、問題のある項目ごとに1件ずつ返す。
 *
 * 文言は持たず、種類(`code`)と、どの項目かを指す下書きのキーだけを返す(純粋関数の層に i18n を
 * 持ち込まない)。画面はこのキーで入力欄と突き合わせ、その項目の場所にエラーを出す。
 */
export type DraftBlockReason =
  | { code: 'nameRequired' }
  | { code: 'slotNameRequired'; slotKey: string }
  | { code: 'variantTextRequired'; slotKey: string; variantKey: string }
  | { code: 'comboNameRequired'; comboKey: string }
  | { code: 'comboEmpty'; comboKey: string }

export function draftBlockReasons(draft: TemplateDraft): DraftBlockReason[] {
  const reasons: DraftBlockReason[] = []
  if (draft.name.trim() === '') reasons.push({ code: 'nameRequired' })
  for (const slot of draft.slots) {
    if (slot.name.trim() === '') reasons.push({ code: 'slotNameRequired', slotKey: slot.key })
    // テキストが空の候補は送らずに落とすので(`draftToWriteRequest`)、黙って消えないよう保存を止める。
    // 候補の名前は任意(サーバーも null を受ける)なので見ない。
    for (const variant of slot.variants) {
      if (variant.text.trim() !== '') continue
      reasons.push({ code: 'variantTextRequired', slotKey: slot.key, variantKey: variant.key })
    }
  }
  // 候補を1つも指さない組み合わせは `draftToWriteRequest` が送らずに落とすので(サーバーが
  // `items` を1件以上求める)、黙って消えないよう保存を止める。そのときは名前の空は問わない
  // (そもそも送らないので)。
  // 同じスロットの候補を2つ以上指す違反は画面がラジオで作れなくしてある(サーバー側では検証する)。
  const liveKeys = liveVariantKeys(draft)
  for (const combo of draft.combos) {
    if (!combo.variantKeys.some((key) => liveKeys.has(key))) {
      reasons.push({ code: 'comboEmpty', comboKey: combo.key })
      continue
    }
    if (combo.name.trim() === '') reasons.push({ code: 'comboNameRequired', comboKey: combo.key })
  }
  return reasons
}

/** 画面が「この項目に異常があるか」をその場で引けるようにした、保存を断る理由の索引。 */
export interface DraftBlockIndex {
  /** テンプレートの名前が空。 */
  templateName: boolean
  /** 名前が空のスロットのキー。 */
  slotNames: ReadonlySet<string>
  /** テキストが空の候補のキー。 */
  variantTexts: ReadonlySet<string>
  /** 名前が空の組み合わせのキー。 */
  comboNames: ReadonlySet<string>
  /** 候補を1つも指していない組み合わせのキー。 */
  comboEmpties: ReadonlySet<string>
}

export function indexDraftBlockReasons(reasons: DraftBlockReason[]): DraftBlockIndex {
  let templateName = false
  const slotNames = new Set<string>()
  const variantTexts = new Set<string>()
  const comboNames = new Set<string>()
  const comboEmpties = new Set<string>()
  for (const reason of reasons) {
    switch (reason.code) {
      case 'nameRequired':
        templateName = true
        break
      case 'slotNameRequired':
        slotNames.add(reason.slotKey)
        break
      case 'variantTextRequired':
        variantTexts.add(reason.variantKey)
        break
      case 'comboNameRequired':
        comboNames.add(reason.comboKey)
        break
      case 'comboEmpty':
        comboEmpties.add(reason.comboKey)
        break
    }
  }
  return { templateName, slotNames, variantTexts, comboNames, comboEmpties }
}

/** 保存すると残る候補のキー(テキストが空の候補は `draftToWriteRequest` が落とす)。 */
export function liveVariantKeys(draft: TemplateDraft): Set<string> {
  const keys = new Set<string>()
  for (const slot of draft.slots) {
    for (const variant of slot.variants) {
      if (variant.text.trim() !== '') keys.add(variant.key)
    }
  }
  return keys
}

/** 配列の要素を `from` から `to` へ動かした新しい配列(範囲外なら元のまま)。 */
export function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return items
  const next = [...items]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

/** 候補の表示名(名前が無ければテキストの先頭)。 */
export function variantLabel(variant: { name: string | null; text: string }, max = 40): string {
  const name = variant.name?.trim()
  if (name) return name
  const text = variant.text.trim()
  return text.length > max ? `${text.slice(0, max)}…` : text
}

/** 一覧の絞り込み(名前で前方一致ではなく部分一致。パラメーターセットと同じ)。 */
export function filterPromptTemplates(
  items: PromptTemplateSummary[],
  query: string,
): PromptTemplateSummary[] {
  const needle = query.trim().toLowerCase()
  if (needle === '') return items
  return items.filter((item) => item.name.toLowerCase().includes(needle))
}
