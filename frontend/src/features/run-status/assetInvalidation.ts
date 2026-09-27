/**
 * Run の状態遷移から、ストック(TanStack Query の `['assets']`)を invalidate すべきか判定する
 * 純粋関数。生成(Run)が succeeded になった瞬間に出力 Asset が増えるので、そのときだけストック
 * 一覧を invalidate したい(失敗・中止では Asset が増えないので不要)。
 */
export interface RunStatusSnapshot {
  id: string
  status: string
}

/**
 * 直前の status と現在の status を比べ、「succeeded に変わった」ときだけ true。
 * 既に succeeded だった(前回も succeeded)場合は false(二重に invalidate しない)。
 */
export function shouldInvalidateAssetsOnStatusChange(
  previousStatus: string | null,
  currentStatus: string | null,
): boolean {
  return currentStatus === 'succeeded' && previousStatus !== 'succeeded'
}

/**
 * 一覧のポーリングで、前回のスナップショット(Run ID → status)と比べて、
 * 新たに succeeded になった Run が1件でもあるか。
 */
export function hasNewlySucceededRun(
  previousStatusById: ReadonlyMap<string, string>,
  currentRuns: readonly RunStatusSnapshot[],
): boolean {
  return currentRuns.some((run) =>
    shouldInvalidateAssetsOnStatusChange(previousStatusById.get(run.id) ?? null, run.status),
  )
}
