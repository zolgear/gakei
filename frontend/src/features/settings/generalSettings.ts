/**
 * 設定の OpenAI のページ(moderation)と ComfyUI のページ(タイムアウト)が共有する
 * 表示・検証の純粋関数(ADR-0009、ADR-0013 7章)。API 呼び出しや下書きの管理は
 * `pages/OpenAiSettingsPage.tsx` / `pages/ComfyUISettingsPage.tsx` が行う。
 *
 * どちらの設定も優先順位は同じ3段階(画面で保存 > 環境変数 > 組み込みの既定値)で、
 * `ModerationSetting` / `ComfyUITimeoutSetting` は同じ形(`value`/`source`/`default`)を持つ
 * (`backend/app/domain/general_settings.py`)。API キーと違い、画面の値は環境変数より
 * 優先するので、環境変数由来(`source === 'env'`)でも入力欄はロックしない(注記だけ出す)。
 */

/** 画面から変えられる ComfyUI のタイムアウトの入力範囲(分)。API へは秒で送る。 */
export const TIMEOUT_MIN_MINUTES = 1
export const TIMEOUT_MAX_MINUTES = 180

interface SourcedSetting {
  source: 'setting' | 'env' | 'default'
}

/** 環境変数(.env)の値を使っているか。画面の入力欄はロックせず、注記だけ出す根拠に使う。 */
export function isFromEnv(setting: SourcedSetting): boolean {
  return setting.source === 'env'
}

/** 「既定値に戻す」ボタンを出してよいか(画面で保存した値があるときだけ)。 */
export function canResetToDefault(setting: SourcedSetting): boolean {
  return setting.source === 'setting'
}

/** 秒 → 分(表示用)。保存値は分の倍数のはずだが、念のため丸める。 */
export function secondsToDisplayMinutes(seconds: number): number {
  return Math.round(seconds / 60)
}

/** 分の入力値(整数)→ 秒(API へ送る値)。 */
export function minutesToSeconds(minutes: number): number {
  return Math.round(minutes) * 60
}

/**
 * 分入力欄の文字列が有効か(整数かつ {@link TIMEOUT_MIN_MINUTES}〜{@link TIMEOUT_MAX_MINUTES})。
 * 空欄・小数・符号付きの余分な文字は無効。保存ボタンの活性/非活性、保存前のクライアント側の
 * 簡易チェックに使う(最終判定は常にサーバー。422 は `ApiError.message` をそのまま表示する)。
 */
export function isValidTimeoutMinutesInput(input: string): boolean {
  const trimmed = input.trim()
  if (!/^\d+$/.test(trimmed)) return false
  const minutes = Number(trimmed)
  return minutes >= TIMEOUT_MIN_MINUTES && minutes <= TIMEOUT_MAX_MINUTES
}
