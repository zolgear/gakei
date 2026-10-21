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
import { setSelectedDetection, useSelectedDetection } from './regionAssistStore'

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
  /** 出している検出のすべての候補(四角にした物も含む。名前のチップに使う)。 */
  allCandidates: RegionCandidate[]
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

/**
 * Asset の検出の履歴と、出す検出(「領域」の画面で選んだもの。選んでいなければいちばん新しいもの)。
 * フォームの入力の補助(`RegionPromptAssist`)と「領域」の画面が同じキャッシュと選択を使う。
 */
export function useDetectionHistory(assetId: string | null, enabled = true) {
  const historyQuery = useQuery({
    queryKey: regionDetectionsQueryKey(assetId ?? ''),
    queryFn: async () => (await listAssetRegionDetections(assetId!)).items,
    enabled: enabled && assetId !== null,
    staleTime: 0,
  })
  const history = historyQuery.data ?? []
  const choice = useSelectedDetection(assetId)
  const selected =
    choice === undefined ? (history[0] ?? null) : choice === null ? null : (history.find((r) => r.id === choice) ?? null)
  return { historyQuery, history, selected }
}

export function useRegionDetection(assetId: string | null): RegionDetection {
  // `assetId` が null(入力画像の無い Run の白紙。ADR-0053 6章)なら、画像が無いので検出は使えない。
  const queryClient = useQueryClient()
  const settingsQuery = useQuery({
    queryKey: REGION_DETECTION_SETTINGS_QUERY_KEY,
    queryFn: getRegionDetectionSettings,
    enabled: assetId !== null,
  })
  const { historyQuery, history, selected } = useDetectionHistory(assetId)
  const setSelectedId = (id: string | null | undefined) => {
    if (assetId !== null) setSelectedDetection(assetId, id)
  }

  const [adopted, setAdopted] = useState<Set<string>>(() => new Set())
  const [error, setError] = useState<string | null>(null)
  const [emptyResult, setEmptyResult] = useState(false)
  const [running, setRunning] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => () => abortRef.current?.abort(), [])

  async function run(query: string) {
    if (assetId === null) return
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
    mutationFn: (id: string) => deleteAssetRegionDetection(assetId ?? '', id),
    onSuccess: (_data, id) => {
      queryClient.setQueryData<RegionDetectionRecord[]>(regionDetectionsQueryKey(assetId ?? ''), (old) =>
        (old ?? []).filter((r) => r.id !== id),
      )
      // 出していた検出を消したら、いちばん新しい検出に戻す。
      if (selected?.id === id) setSelectedId(undefined)
      setError(null)
    },
    onError: (err: unknown) =>
      setError(err instanceof ApiError ? err.message : msg().regions.detection.historyDeleteFailed),
  })

  const allCandidates = selected ? candidatesOf(selected) : []
  const candidates = allCandidates.filter((c) => !adopted.has(c.key))

  return {
    usable: assetId !== null && settingsQuery.data?.usable === true,
    history,
    historyError: historyQuery.isError,
    selectedId: selected ? selected.id : null,
    select: (id) => {
      setSelectedId(id)
      setEmptyResult(false)
    },
    candidates,
    allCandidates,
    markAdopted: (key) => setAdopted((prev) => new Set(prev).add(key)),
    running,
    run: (query) => void run(query),
    cancel,
    remove: (id) => deleteMutation.mutate(id),
    error,
    emptyResult,
  }
}
