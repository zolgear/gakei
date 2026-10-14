/**
 * レーティング(ADR-0049)の段階とマスクの判定の純粋関数。画面の部品(`AssetThumb`、`RatingBadge`、
 * ビューアの「レーティング」の節)と、ユーザー設定の「表示」が使う。
 *
 * - 段階は弱い順に `g`(グリーン)、`r`、`x`、`xx`、`xxx`。未判定は null。
 * - マスクするかはユーザーごとの設定(`content_rating.mask_levels` と `content_rating.mask_unrated`。
 *   ADR-0049 6章)で決める。設定を読み込む前は既定(R 以上、未判定はマスクしない)で判定する。
 * - 一覧の応答に段階の欄が無い(`undefined`)ときは、判定のしようがないのでマスクしない。
 */
import type {
  AssetContentRatingResponse,
  AssetDetail,
  ContentRatingDetail,
  ContentRatingLevel,
  UserPreferencesResponse,
} from '../../api/client'

/** 弱い順。選ぶ操作とチェックボックスもこの順に並べる。 */
export const CONTENT_RATING_LEVELS: readonly ContentRatingLevel[] = ['g', 'r', 'x', 'xx', 'xxx']

export function isContentRatingLevel(value: unknown): value is ContentRatingLevel {
  return typeof value === 'string' && (CONTENT_RATING_LEVELS as readonly string[]).includes(value)
}

/** マスクの設定。サーバーのキー(`content_rating.*`)を画面の中で扱いやすい形にしたもの。 */
export interface ContentRatingMaskPrefs {
  maskLevels: readonly ContentRatingLevel[]
  maskUnrated: boolean
}

/** 既定(ADR-0049 5章): R 以上をマスクし、未判定はマスクしない。 */
export const DEFAULT_MASK_PREFS: ContentRatingMaskPrefs = {
  maskLevels: ['r', 'x', 'xx', 'xxx'],
  maskUnrated: false,
}

/** サーバーの設定からマスクの設定を取り出す。読み込み前・欠け・壊れた値は既定に倒す。 */
export function maskPrefsFrom(prefs: UserPreferencesResponse | undefined): ContentRatingMaskPrefs {
  if (!prefs) return DEFAULT_MASK_PREFS
  const raw = prefs['content_rating.mask_levels']
  const maskLevels = Array.isArray(raw) ? raw.filter(isContentRatingLevel) : DEFAULT_MASK_PREFS.maskLevels
  const unrated = prefs['content_rating.mask_unrated']
  return { maskLevels, maskUnrated: typeof unrated === 'boolean' ? unrated : DEFAULT_MASK_PREFS.maskUnrated }
}

/**
 * この段階の画像をマスクするか。`undefined`(応答に段階の欄が無い)はマスクしない。
 * `null`(未判定)は「未判定もマスクする」のときだけ。
 */
export function shouldMaskLevel(
  level: ContentRatingLevel | null | undefined,
  prefs: ContentRatingMaskPrefs,
): boolean {
  if (level === undefined) return false
  if (level === null) return prefs.maskUnrated
  return prefs.maskLevels.includes(level)
}

/** 段階の集合を、弱い順に並べ直して重複を除く(チェックボックスの切り替えで送る値)。 */
export function toggleMaskLevel(
  levels: readonly ContentRatingLevel[],
  level: ContentRatingLevel,
  checked: boolean,
): ContentRatingLevel[] {
  const set = new Set(levels)
  if (checked) set.add(level)
  else set.delete(level)
  return CONTENT_RATING_LEVELS.filter((l) => set.has(l))
}

/** 0〜1 の確率を百分率の文字列にする(例 0.4567 → "45.7%")。壊れた値は "—"。 */
export function formatProbability(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${(Math.round(Math.min(1, Math.max(0, value)) * 1000) / 10).toFixed(1)}%`
}

/** rating の4つの確率のキー(WD のラベル名)。弱い順。 */
export const RATING_SCORE_KEYS = ['general', 'sensitive', 'questionable', 'explicit'] as const
export type RatingScoreKey = (typeof RATING_SCORE_KEYS)[number]

// -- ビューアの「レーティング」の節(ADR-0049 4章) -------------------------------------

/** 判定が終わるまで Asset 詳細を取り直す間隔(タイトル・タグの推定と同じ)。 */
export const CONTENT_RATING_POLL_INTERVAL_MS = 2000

/** 待ち行列にあるか、判定中か。 */
export function isContentRatingPending(detail: Pick<ContentRatingDetail, 'status'> | null | undefined): boolean {
  return detail?.status === 'queued' || detail?.status === 'running'
}

/** React Query の `refetchInterval` に渡す値。判定中だけポーリングする。 */
export function contentRatingPollInterval(
  asset: Pick<AssetDetail, 'content_rating_detail'> | undefined,
): number | false {
  return isContentRatingPending(asset?.content_rating_detail) ? CONTENT_RATING_POLL_INTERVAL_MS : false
}

/**
 * 情報欄に「レーティング」の節を出すか。マスクは対象外。管理者設定で判定が無効なら出さない
 * (ADR-0049 4章)。ただし、もう段階がある画像(人が選んだもの、無効にする前に判定したもの)は出す。
 * `enabled` が undefined(設定の読み込み前)なら、段階があるときだけ出す。
 */
export function shouldShowContentRatingSection(
  asset: Pick<AssetDetail, 'kind' | 'content_rating_detail'>,
  enabled: boolean | undefined,
): boolean {
  if (asset.kind === 'mask') return false
  if (enabled) return true
  return Boolean(asset.content_rating_detail?.level)
}

/** 段階を選べるか(削除済みはサーバーが 409 を返すので表示だけにする)。 */
export function canEditContentRating(asset: Pick<AssetDetail, 'kind' | 'deleted_at'>): boolean {
  return asset.kind !== 'mask' && !asset.deleted_at
}

/** 「再判定」を出すか。判定が使えない(無効、モデルが無い)なら押しても 409 になるので出さない。 */
export function canRequestContentRating(
  asset: Pick<AssetDetail, 'kind' | 'deleted_at'>,
  usable: boolean | undefined,
): boolean {
  return canEditContentRating(asset) && usable === true
}

/** 変更・再判定の応答を Asset 詳細へ重ねる。 */
export function mergeContentRating(asset: AssetDetail, response: AssetContentRatingResponse): AssetDetail {
  return {
    ...asset,
    content_rating: response.content_rating ?? null,
    content_rating_detail: response.content_rating_detail ?? null,
  }
}

/**
 * サムネイルに渡す段階。マスク(マスク画像)は判定の対象外なので、「未判定もマスクする」でも
 * マスクしないように undefined にする(ADR-0049 3章)。
 */
export function thumbRating(asset: {
  kind?: string | null
  content_rating?: ContentRatingLevel | null
}): ContentRatingLevel | null | undefined {
  if (asset.kind === 'mask') return undefined
  return asset.content_rating ?? null
}
