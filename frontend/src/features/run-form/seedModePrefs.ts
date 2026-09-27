/**
 * seed 専用入力欄(`ParamDef.widget === 'seed'`。ADR-0013 フォローアップ)の
 * ランダム/固定モードを localStorage に保存する。ワークフローには依存させない: チームでは
 * admin が用意したワークフロー/モデルを複数人が使い回すが、ランダムにするか固定にするかは
 * 各自の作業内容で決まるものなので、ブラウザ単位で最後に選んだモードを記憶し、モデルや
 * ワークフローが変わっても次にフォームを開いたときの初期モードとして使う。
 * `src/context/runFormStorage.ts` と同じ方針(try/catch で囲み、使えない環境では黙って
 * 既定値(固定)で動く)。
 */

export type SeedMode = 'random' | 'fixed'

const STORAGE_KEY = 'gakei.runForm.seedMode'

/** 記憶が無い・壊れている・localStorage が使えないときの既定モード。 */
export const DEFAULT_SEED_MODE: SeedMode = 'fixed'

/** 記憶されているモードを読む。無効な値(旧バージョンの残骸等)は既定値(固定)にする。 */
export function loadSeedMode(): SeedMode {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'random' ? 'random' : DEFAULT_SEED_MODE
  } catch {
    return DEFAULT_SEED_MODE
  }
}

/** モードを保存する。失敗しても(容量超過・プライベートブラウジング等)黙って無視する。 */
export function saveSeedMode(mode: SeedMode): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    // 保存できなくても致命的ではない。
  }
}
