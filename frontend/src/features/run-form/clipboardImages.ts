/**
 * クリップボードからの貼り付け(プロンプト欄への Ctrl+V)に含まれる画像を取り出す。
 * スクリーンショットや画像のコピーは `clipboardData.items` に kind=file / type=image/* で入る。
 * 画像が無ければ空配列を返し、呼び出し側は通常のテキスト貼り付けに任せる。
 *
 * 純粋関数のみ(副作用なし)。実際の ClipboardEvent は呼び出し側で読み取り、
 * ここにはプレーンなデータだけを渡す。
 */

export interface ClipboardItemLike<TFile> {
  kind: string
  type: string
  getAsFile: () => TFile | null
}

export function pickPastedImageFiles<TFile>(items: ClipboardItemLike<TFile>[]): TFile[] {
  const files: TFile[] = []
  for (const item of items) {
    if (item.kind !== 'file' || !item.type.startsWith('image/')) continue
    const file = item.getAsFile()
    if (file) files.push(file)
  }
  return files
}
