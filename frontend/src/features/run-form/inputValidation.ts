/** アップロード画像の事前チェック(サーバー側の 422 を待たずに分かる範囲だけ)。 */
import { fmt, msg } from '../../i18n'

export const ACCEPTED_IMAGE_MIME_TYPES = ['image/png', 'image/jpeg', 'image/webp']

export interface FileLike {
  type: string
  size: number
  name: string
}

export interface FileValidationResult {
  valid: boolean
  error?: string
}

export function validateInputFile(file: FileLike, maxBytes: number): FileValidationResult {
  if (!ACCEPTED_IMAGE_MIME_TYPES.includes(file.type)) {
    return {
      valid: false,
      error: fmt(msg().runForm.inputValidation.unsupportedFormat, { name: file.name }),
    }
  }
  if (file.size >= maxBytes) {
    const maxMb = Math.round((maxBytes / 1024 / 1024) * 10) / 10
    return { valid: false, error: fmt(msg().runForm.inputValidation.sizeExceeds, { name: file.name, maxMb }) }
  }
  return { valid: true }
}
