/**
 * 生成フォームで最後に選んだグループ(ADR-0022 4章「生成時の指定」)を localStorage に覚える。
 * フォームの状態(`runFormStorage`)とは別のキーに持ち、「新規生成」やフォームの初期化の後も
 * 既定値として引き継ぐ。`src/shell/panelStorage.ts` と同じく try/catch で囲み、使えない環境では
 * 黙って null(=「なし」)として動く。存在確認(削除済みなら「なし」)は呼び出し側が
 * グループ一覧で行う(`resolveAssetGroupId`)。
 */
const STORAGE_KEY = 'gakei:last-asset-group'

/** 覚えているグループ id を読む。無ければ(空・読めなければ) null。 */
export function loadLastAssetGroupId(): string | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value ? value : null
  } catch {
    return null
  }
}

/** 最後に選んだグループ id を保存する。null(「なし」)はキーを消す。 */
export function saveLastAssetGroupId(id: string | null): void {
  try {
    if (id === null) {
      localStorage.removeItem(STORAGE_KEY)
    } else {
      localStorage.setItem(STORAGE_KEY, id)
    }
  } catch {
    // 保存できなくても致命的ではないので黙って無視する。
  }
}
