/**
 * 推定の状態(ADR-0024 4章)に関する判定。ビューアのポーリング、再推定ボタンの出し分け、
 * 編集 API の応答を Asset 詳細のキャッシュへ反映する処理に使う。
 */
import type {
  AnnotationEngine,
  AnnotationStatusView,
  AssetAnnotationResponse,
  AssetDetail,
} from '../../api/client'

/** 推定が終わるまで Asset 詳細を取り直す間隔。 */
export const ANNOTATION_POLL_INTERVAL_MS = 2000

/** 待ち行列にあるか、実行中か。 */
export function isAnnotationPending(annotation: AnnotationStatusView | null | undefined): boolean {
  return annotation?.status === 'queued' || annotation?.status === 'running'
}

/** React Query の `refetchInterval` に渡す値。推定中だけポーリングする。 */
export function annotationPollInterval(asset: Pick<AssetDetail, 'annotation'> | undefined): number | false {
  return isAnnotationPending(asset?.annotation) ? ANNOTATION_POLL_INTERVAL_MS : false
}

/** 推定中から終わった状態へ移ったか(一覧のタイトルとタグの件数を取り直すきっかけ)。 */
export function annotationJustFinished(
  previous: AnnotationStatusView['status'] | null | undefined,
  next: AnnotationStatusView['status'] | null | undefined,
): boolean {
  const wasPending = previous === 'queued' || previous === 'running'
  const isPending = next === 'queued' || next === 'running'
  return wasPending && !isPending
}

/**
 * 情報欄でタイトル・タグを扱うか。マスクは対象外(ADR-0024 1章)。
 */
export function supportsAnnotation(asset: Pick<AssetDetail, 'kind'>): boolean {
  return asset.kind !== 'mask'
}

/** 編集できるか。削除済みの Asset はサーバーが 409 を返すので、表示だけにする。 */
export function canEditAnnotation(asset: Pick<AssetDetail, 'kind' | 'deleted_at'>): boolean {
  return supportsAnnotation(asset) && !asset.deleted_at
}

/** 「再推定」を出すか。使えるエンジンが 1 つも無ければ出さない(押しても 409 になる)。 */
export function canRequestAnnotation(
  asset: Pick<AssetDetail, 'kind' | 'deleted_at'>,
  usableEngines: readonly AnnotationEngine[] | undefined,
): boolean {
  return canEditAnnotation(asset) && (usableEngines?.length ?? 0) > 0
}

/** 編集 API の応答(更新後の注釈)を Asset 詳細へ重ねる。 */
export function mergeAnnotation(asset: AssetDetail, response: AssetAnnotationResponse): AssetDetail {
  return {
    ...asset,
    title: response.title ?? null,
    title_source: response.title_source ?? null,
    tags: response.tags ?? [],
    annotation: response.annotation ?? null,
  }
}
