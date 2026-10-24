/**
 * 出力サイズを変えたら、入力の1枚目の配置の結果を新しい大きさで描き直す(ADR-0050 5章、
 * 2026-10-24 追記)。画面は開かず、レシピから書き出して保存し、入力の1枚目を差し替える。
 *
 * - サイズの欄を打っている途中で何度も書き出さないよう、値が少し落ち着いてから1回にする
 *   (セレクトの変更も同じ待ちを通す)。描き直すのは「サイズが変わったとき」だけ。
 * - 保存は作り直しの規則どおり(`replaces_asset_id`。元が Run に使われていなければ置き換え)。
 * - 書き出し中にさらにサイズが変わったら、終わってから最新の大きさでもう一度判断する。
 * - 失敗したら元のまま残し、`onFailed` で知らせる。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { createCompositeAsset, type AssetDetail } from '../../api/client'
import type { RunInputItem } from '../run-form/types'
import { compositeRedrawPlan } from './compositeInputs'
import { exportComposite, exportSpecFromRecipe } from './exportComposite'
import type { Size } from './quad'

/** サイズの欄の値が落ち着くまで待つ時間(ミリ秒)。 */
export const COMPOSITE_REDRAW_DELAY_MS = 600

function keyOf(size: Size | null): string | null {
  return size ? `${size.width}x${size.height}` : null
}

export function useCompositeAutoRedraw(opts: {
  inputs: readonly RunInputItem[]
  detailsByAssetId: ReadonlyMap<string, AssetDetail>
  /** 配置のキャンバスが従う出力サイズ(決まらなければ null)。 */
  targetSize: Size | null
  /** 描き直した配置の結果で、`inputId` の入力(今も `previousAssetId` を指していれば)を差し替える。 */
  onRedrawn: (inputId: string, previousAssetId: string, asset: AssetDetail) => void
  onFailed: () => void
}): { redrawingInputId: string | null } {
  const queryClient = useQueryClient()
  // 書き出しの後で使う最新の入力と差し替えの処理(描画のたびに入れ替える)。
  const latest = useRef(opts)
  useLayoutEffect(() => {
    latest.current = opts
  })

  const targetKey = keyOf(opts.targetSize)
  // 落ち着いた値(初回は今の値。開いた時点のサイズでは描き直さない)。
  const [settledKey, setSettledKey] = useState(targetKey)
  useEffect(() => {
    if (targetKey === settledKey) return
    const timer = setTimeout(() => setSettledKey(targetKey), COMPOSITE_REDRAW_DELAY_MS)
    return () => clearTimeout(timer)
  }, [targetKey, settledKey])

  const [redrawingInputId, setRedrawingInputId] = useState<string | null>(null)
  const busyRef = useRef(false)
  // 書き出し中にサイズが変わった(終わってから判断し直す)。
  const [recheck, setRecheck] = useState(0)
  const pendingRef = useRef(false)
  const handledKeyRef = useRef(settledKey)

  useEffect(() => {
    const changed = handledKeyRef.current !== settledKey
    if (!changed && !pendingRef.current) return
    if (busyRef.current) {
      pendingRef.current = true
      return
    }
    handledKeyRef.current = settledKey
    pendingRef.current = false
    const { inputs, detailsByAssetId, targetSize } = latest.current
    if (keyOf(targetSize) !== settledKey) return
    // 描き直した直後は、新しい Asset がまだ `detailsByAssetId` に届いていないことがあるので、
    // キャッシュも引く。
    const details = new Map<string, AssetDetail>()
    for (const item of inputs) {
      const d = detailsByAssetId.get(item.assetId) ?? queryClient.getQueryData<AssetDetail>(['asset', item.assetId])
      if (d) details.set(item.assetId, d)
    }
    const plan = compositeRedrawPlan(inputs, details, targetSize)
    if (!plan) return
    busyRef.current = true
    setRedrawingInputId(plan.inputId)
    void (async () => {
      try {
        const blob = await exportComposite(exportSpecFromRecipe(plan.recipe))
        const asset = await createCompositeAsset(blob, plan.recipe, plan.assetId)
        queryClient.setQueryData(['asset', asset.id], asset)
        queryClient.invalidateQueries({ queryKey: ['asset', plan.assetId] })
        queryClient.invalidateQueries({ queryKey: ['assets'] })
        queryClient.invalidateQueries({ queryKey: ['lineage'] })
        latest.current.onRedrawn(plan.inputId, plan.assetId, asset)
      } catch {
        latest.current.onFailed()
      } finally {
        busyRef.current = false
        setRedrawingInputId(null)
        setRecheck((n) => n + 1)
      }
    })()
  }, [settledKey, recheck, queryClient])

  return { redrawingInputId }
}
