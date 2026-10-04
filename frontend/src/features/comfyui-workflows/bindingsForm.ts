/**
 * ワークフロー登録・編集フォームの「差し込み先(Bindings)」を扱う純粋関数(ADR-0013)。
 * API の `Bindings`(バックエンド `domain/comfy_workflow.py`)と、フォームが編集しやすい形
 * (`BindingsFormState`)を相互に変換し、クライアント側の最低限の検証も行う。
 *
 * 画像の枠(`images`)は順序付きの配列。GAKEI の入力画像の n 枚目が n 番目の枠に入る
 * (ADR-0013 3章)。枠はすべて必須で、Run の入力画像はちょうど枠の数だけ要る。
 */
import type {
  ComfyBindings,
  ComfyNodeInfo,
  ComfyOperation,
  ComfySuggestedBindings,
} from '../../api/client'
import { msg } from '../../i18n'
import { type InputRefValue, nodeExists, refExistsInNodes } from './nodeOptions'

export type MaskMode = 'none' | 'load_image_mask' | 'image_alpha'

export interface BindingsFormState {
  prompt: InputRefValue | null
  negativePrompt: InputRefValue | null
  /** 空の node("")は「まだ選んでいない行」を表す(削除もできるが、送信時は無視する)。 */
  seeds: InputRefValue[]
  width: InputRefValue | null
  height: InputRefValue | null
  batchSize: InputRefValue | null
  /** 画像の枠(順序付き)。1番目が系列の主たる親になる。seeds と同様、空の node の行は未選択。 */
  images: InputRefValue[]
  maskMode: MaskMode
  maskRef: InputRefValue | null
  outputs: string[]
  /**
   * 最終プロンプト(PE の出力)を記録するノード(ADR-0030 1章)。null は「なし」。
   * 種類で絞らず、テンプレートのどのノードでも選べる。
   */
  finalPrompt: string | null
}

export const EMPTY_BINDINGS_FORM: BindingsFormState = {
  prompt: null,
  negativePrompt: null,
  seeds: [],
  width: null,
  height: null,
  batchSize: null,
  images: [],
  maskMode: 'none',
  maskRef: null,
  outputs: [],
  finalPrompt: null,
}

export function bindingsFormFromApi(bindings: ComfyBindings): BindingsFormState {
  const mask = bindings.mask ?? null
  return {
    prompt: bindings.prompt,
    negativePrompt: bindings.negative_prompt ?? null,
    seeds: bindings.seed ?? [],
    width: bindings.width ?? null,
    height: bindings.height ?? null,
    batchSize: bindings.batch_size ?? null,
    images: bindings.images ?? [],
    maskMode: mask?.mode ?? 'none',
    maskRef: mask?.mode === 'load_image_mask' && mask.node && mask.input
      ? { node: mask.node, input: mask.input }
      : null,
    outputs: bindings.outputs ?? [],
    // 旧形式の bindings(final_prompt なし)は「なし」として読む(ADR-0030 1章)。
    finalPrompt: bindings.final_prompt ?? null,
  }
}

/**
 * 新規登録時、analyze の `suggested_bindings` を初期値にする(無ければ空)。
 * `Bindings` と異なり、`prompt` が null、`outputs` が空のこともある(見つからなかった
 * 必須項目。ADR-0013 フォローアップ)。見つかった項目はそのままフォームの初期値にし、
 * 残りは利用者が手動で選ぶ。
 */
export function bindingsFormFromSuggestion(
  suggested: ComfySuggestedBindings | null | undefined,
): BindingsFormState {
  if (!suggested) return { ...EMPTY_BINDINGS_FORM }
  const mask = suggested.mask ?? null
  return {
    prompt: suggested.prompt ?? null,
    negativePrompt: suggested.negative_prompt ?? null,
    seeds: suggested.seed ?? [],
    width: suggested.width ?? null,
    height: suggested.height ?? null,
    batchSize: suggested.batch_size ?? null,
    images: suggested.images ?? [],
    maskMode: mask?.mode ?? 'none',
    maskRef:
      mask?.mode === 'load_image_mask' && mask.node && mask.input
        ? { node: mask.node, input: mask.input }
        : null,
    outputs: suggested.outputs ?? [],
    finalPrompt: suggested.final_prompt ?? null,
  }
}

/** 「まだ選んでいない行」(seeds・images の空行)かどうか。 */
function isUnsetRef(ref: InputRefValue): boolean {
  return ref.node === '' || ref.input === ''
}

function refKey(ref: InputRefValue): string {
  return `${ref.node}\u0000${ref.input}`
}

function hasDuplicateRefs(refs: InputRefValue[]): boolean {
  const seen = new Set<string>()
  for (const ref of refs) {
    const key = refKey(ref)
    if (seen.has(key)) return true
    seen.add(key)
  }
  return false
}

/** 検証済みの状態から API の `Bindings` を組み立てる(未検証のまま呼ぶと不正な値を含みうる)。 */
export function bindingsFormToApi(form: BindingsFormState): ComfyBindings {
  return {
    prompt: form.prompt ?? { node: '', input: '' },
    negative_prompt: form.negativePrompt,
    seed: form.seeds.filter((ref) => !isUnsetRef(ref)),
    width: form.width,
    height: form.height,
    batch_size: form.batchSize,
    images: form.images.filter((ref) => !isUnsetRef(ref)),
    mask:
      form.maskMode === 'none'
        ? null
        : form.maskMode === 'image_alpha'
          ? { mode: 'image_alpha', node: null, input: null }
          : form.maskRef
            ? { mode: 'load_image_mask', node: form.maskRef.node, input: form.maskRef.input }
            : null,
    outputs: form.outputs,
    // bindings は丸ごと送り直すので、「なし」も null として必ず含める(省くと保存済みの値の
    // 扱いがサーバー任せになる)。
    final_prompt: form.finalPrompt,
  }
}

/** 差し込み先として実際に使われている (node, input) の一覧(重複検出・再検証に使う)。 */
export function bindingRefsList(form: BindingsFormState): InputRefValue[] {
  const refs: InputRefValue[] = []
  if (form.prompt) refs.push(form.prompt)
  if (form.negativePrompt) refs.push(form.negativePrompt)
  refs.push(...form.seeds.filter((ref) => !isUnsetRef(ref)))
  if (form.width) refs.push(form.width)
  if (form.height) refs.push(form.height)
  if (form.batchSize) refs.push(form.batchSize)
  refs.push(...form.images.filter((ref) => !isUnsetRef(ref)))
  if (form.maskMode === 'load_image_mask' && form.maskRef) refs.push(form.maskRef)
  return refs
}

/** 保存前のクライアント側の最低限の検証(ADR-0013 の保存時検証と同じ規則)。 */
export function validateBindingsForm(form: BindingsFormState, operation: ComfyOperation): string[] {
  const t = msg().comfyui.bindingsValidation
  const errors: string[] = []
  if (form.prompt === null) errors.push(t.promptRequired)
  if (form.outputs.length === 0) errors.push(t.outputsRequired)
  if (form.seeds.some(isUnsetRef)) {
    errors.push(t.seedUnset)
  }
  if (form.images.some(isUnsetRef)) {
    errors.push(t.imageSlotUnset)
  }

  const setImages = form.images.filter((ref) => !isUnsetRef(ref))
  if (hasDuplicateRefs(setImages)) {
    errors.push(t.imageSlotDuplicate)
  }
  if (
    form.maskMode === 'load_image_mask' &&
    form.maskRef &&
    setImages.some((ref) => ref.node === form.maskRef?.node && ref.input === form.maskRef?.input)
  ) {
    errors.push(t.maskConflictsWithImage)
  }

  if (operation === 'edit' && setImages.length === 0) {
    errors.push(t.editRequiresImageSlot)
  }
  if (operation === 'generate') {
    if (form.images.length > 0) errors.push(t.generateCannotHaveImages)
    if (form.maskMode !== 'none') errors.push(t.generateCannotHaveMask)
  }
  if (form.maskMode === 'load_image_mask' && form.maskRef === null) {
    errors.push(t.maskRefRequired)
  }
  if (form.maskMode === 'image_alpha' && setImages.length === 0) {
    errors.push(t.imageAlphaRequiresImageSlot)
  }
  return errors
}

/** 画像の枠を1つ上/下へ動かす(index は範囲内である必要がある)。範囲外なら何もしない。 */
export function moveImageSlot(
  images: InputRefValue[],
  index: number,
  direction: 'up' | 'down',
): InputRefValue[] {
  const target = direction === 'up' ? index - 1 : index + 1
  if (index < 0 || index >= images.length || target < 0 || target >= images.length) return images
  const next = images.slice()
  ;[next[index], next[target]] = [next[target], next[index]]
  return next
}

export interface ReconcileResult {
  next: BindingsFormState
  /** 未設定に戻した項目の日本語ラベル(通知文の組み立てに使う)。 */
  clearedFields: string[]
}

/**
 * テンプレートを新しいファイルに差し替えたとき、既存の差し込み先が新しいノード一覧に
 * まだ存在するかを確かめ、無くなったものは未設定に戻す(ADR-0013)。
 */
export function reconcileBindingsWithNodes(
  form: BindingsFormState,
  nodes: ComfyNodeInfo[],
): ReconcileResult {
  const labels = msg().comfyui.bindingsValidation.clearedLabels
  const cleared: string[] = []

  function check(ref: InputRefValue | null, label: string): InputRefValue | null {
    if (ref === null) return null
    if (refExistsInNodes(ref, nodes)) return ref
    cleared.push(label)
    return null
  }

  const nextSeeds = form.seeds.filter((ref) => {
    if (isUnsetRef(ref)) return true
    if (refExistsInNodes(ref, nodes)) return true
    cleared.push(labels.seed)
    return false
  })

  const nextImages = form.images.filter((ref) => {
    if (isUnsetRef(ref)) return true
    if (refExistsInNodes(ref, nodes)) return true
    cleared.push(labels.imageSlot)
    return false
  })

  const nextOutputs = form.outputs.filter((id) => {
    if (nodeExists(id, nodes)) return true
    cleared.push(labels.output)
    return false
  })

  let nextFinalPrompt = form.finalPrompt
  if (nextFinalPrompt !== null && !nodeExists(nextFinalPrompt, nodes)) {
    cleared.push(labels.finalPrompt)
    nextFinalPrompt = null
  }

  const nextMaskRef = form.maskMode === 'load_image_mask' ? check(form.maskRef, labels.mask) : form.maskRef
  const maskCleared = form.maskMode === 'load_image_mask' && form.maskRef !== null && nextMaskRef === null

  return {
    next: {
      prompt: check(form.prompt, labels.prompt),
      negativePrompt: check(form.negativePrompt, labels.negativePrompt),
      seeds: nextSeeds,
      width: check(form.width, labels.width),
      height: check(form.height, labels.height),
      batchSize: check(form.batchSize, labels.batchSize),
      images: nextImages,
      maskMode: maskCleared ? 'none' : form.maskMode,
      maskRef: nextMaskRef,
      outputs: nextOutputs,
      finalPrompt: nextFinalPrompt,
    },
    clearedFields: cleared,
  }
}
