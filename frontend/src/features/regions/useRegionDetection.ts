/**
 * 「領域」の画面の検出(ADR-0053 7章)。検出の設定(ボタンを出すか)、Asset ごとの検出の履歴、
 * 今出している候補、検出の実行と取り消しをまとめる。
 *
 * - 開いたときに履歴を読み、あれば一番新しい検出の候補を出す。履歴は検出が無効でも読める
 *   (そのときは「検出」のボタンだけを出さない)。
 * - 「検出」は毎回新しく検出し、結果は履歴の先頭に入る。10 秒を超えることがあるので取り消せる
 *   (`AbortController`)。
 * - 候補を押して四角にしたものは、候補の表示から外す(画面の中だけ。履歴は変えない)。
 * - 履歴の1件を消せる(検出した本人か管理者。それ以外はサーバーが 403 で断り、その文言を出す)。
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  deleteAssetRegionDetection,
  detectAssetRegions,
  getRegionDetectionSettings,
  listAssetRegionDetections,
  type RegionDetectionRecord,
} from '../../api/client'
import { msg } from '../../i18n'
import { REGION_DETECTION_SETTINGS_QUERY_KEY } from '../settings/queryKeys'
import { isValidBox, type Box } from './regions'

export interface RegionCandidate {
  key: string
  label: string
  box: Box
}

export function regionDetectionsQueryKey(assetId: string) {
  return ['region-detections', assetId] as const
}

/** 検出1回分の候補(壊れた四角は捨てる)。キーは検出の id と並び順。 */
export function candidatesOf(record: RegionDetectionRecord): RegionCandidate[] {
  return record.regions
    .map((r, index) => ({ key: `${record.id}:${index}`, label: r.label, box: [...r.box] as Box }))
    .filter((c) => isValidBox(c.box))
}

export interface RegionDetection {
  /** 検出のボタンを出してよいか(有効で、接続先がそろっている)。 */
  usable: boolean
  history: RegionDetectionRecord[]
  historyError: boolean
  /** 候補を出している検出(null は出していない)。 */
  selectedId: string | null
  select: (id: string | null) => void
  candidates: RegionCandidate[]
  /** 候補を四角にした(表示から外す)。 */
  markAdopted: (key: string) => void
  running: boolean
  run: (query: string) => void
  cancel: () => void
  remove: (id: string) => void
  error: string | null
  /** 直前の検出で候補が無かった。 */
  emptyResult: boolean
}

export function useRegionDetection(assetId: string): RegionDetection {
  const queryClient = useQueryClient()
  const settingsQuery = useQuery({ queryKey: REGION_DETECTION_SETTINGS_QUERY_KEY, queryFn: getRegionDetectionSettings })
  const historyQuery = useQuery({
    queryKey: regionDetectionsQueryKey(assetId),
    queryFn: async () => (await listAssetRegionDetections(assetId)).items,
    staleTime: 0,
  })
  const history = historyQuery.data ?? []

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [autoSelected, setAutoSelected] = useState(false)
  const [adopted, setAdopted] = useState<Set<string>>(() => new Set())
  const [error, setError] = useState<string | null>(null)
  const [emptyResult, setEmptyResult] = useState(false)
  const [running, setRunning] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // 開いたときに、一番新しい検出の候補を出す(1回だけ。その後は利用者の選択に従う)。
  if (!autoSelected && historyQuery.isSuccess) {
    setAutoSelected(true)
    if (history[0]) setSelectedId(history[0].id)
  }

  useEffect(() => () => abortRef.current?.abort(), [])

  async function run(query: string) {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setRunning(true)
    setError(null)
    setEmptyResult(false)
    try {
      const res = await detectAssetRegions(assetId, query, controller.signal)
      const record = res.detection
      queryClient.setQueryData<RegionDetectionRecord[]>(regionDetectionsQueryKey(assetId), (old) => [
        record,
        ...(old ?? []).filter((r) => r.id !== record.id),
      ])
      setSelectedId(record.id)
      setEmptyResult(record.regions.length === 0)
      void queryClient.invalidateQueries({ queryKey: REGION_DETECTION_SETTINGS_QUERY_KEY })
    } catch (err) {
      if (controller.signal.aborted) return
      setError(err instanceof ApiError ? err.message : msg().regions.detection.failed)
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        setRunning(false)
      }
    }
  }

  function cancel() {
    abortRef.current?.abort()
    abortRef.current = null
    setRunning(false)
  }

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAssetRegionDetection(assetId, id),
    onSuccess: (_data, id) => {
      queryClient.setQueryData<RegionDetectionRecord[]>(regionDetectionsQueryKey(assetId), (old) =>
        (old ?? []).filter((r) => r.id !== id),
      )
      if (selectedId === id) setSelectedId(null)
      setError(null)
    },
    onError: (err: unknown) =>
      setError(err instanceof ApiError ? err.message : msg().regions.detection.historyDeleteFailed),
  })

  const selected = history.find((r) => r.id === selectedId) ?? null
  const candidates = selected ? candidatesOf(selected).filter((c) => !adopted.has(c.key)) : []

  return {
    usable: settingsQuery.data?.usable === true,
    history,
    historyError: historyQuery.isError,
    selectedId: selected ? selected.id : null,
    select: (id) => {
      setSelectedId(id)
      setEmptyResult(false)
    },
    candidates,
    markAdopted: (key) => setAdopted((prev) => new Set(prev).add(key)),
    running,
    run: (query) => void run(query),
    cancel,
    remove: (id) => deleteMutation.mutate(id),
    error,
    emptyResult,
  }
}
