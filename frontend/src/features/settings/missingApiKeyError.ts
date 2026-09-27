/**
 * `POST /api/runs` が 409 で返す「API キーが未設定」のエラーかどうかを、メッセージ文字列から
 * 判定する(ADR-0012 Decision 4)。バックエンドのメッセージ文言に依存するので、変わったら
 * ここも合わせて直す。判定できれば、設定画面へのリンクを添えて案内できる。
 * ADR-0015: バックエンドは `Accept-Language` に応じて日本語/英語どちらかを返すので、両方の
 * 言い回しを見る。
 */
export function isMissingApiKeyError(message: string): boolean {
  const isJa = message.includes('API キー') && message.includes('設定')
  const isEn = message.toLowerCase().includes('no openai api key')
  return isJa || isEn
}
