/** 契約の条件の入力の値(ADR-0059 2章)。用途は1行に1つで書き、空行と重複を落として送る。 */
import type { IpTermsInput, IpTermsView } from '../../api/client'

export type ExternalAi = IpTermsInput['external_ai']
export const EXTERNAL_AI_OPTIONS: readonly ExternalAi[] = ['deny', 'local_only', 'allow']

export interface TermsDraft {
  contractNumber: string
  validFrom: string
  validUntil: string
  purposes: string
  externalAi: ExternalAi
  notes: string
}

export function termsDraftFrom(terms: IpTermsView | null | undefined): TermsDraft {
  return {
    contractNumber: terms?.contract_number ?? '',
    validFrom: terms?.valid_from ?? '',
    validUntil: terms?.valid_until ?? '',
    purposes: (terms?.purposes ?? []).join('\n'),
    externalAi: terms?.external_ai ?? 'deny',
    notes: terms?.notes ?? '',
  }
}

/** 入力から API の値を作る。用途は空行と重複を落とす。 */
export function termsInputFrom(draft: TermsDraft): IpTermsInput {
  const purposes = [...new Set(draft.purposes.split('\n').map((line) => line.trim()).filter(Boolean))]
  return {
    contract_number: draft.contractNumber.trim() || null,
    valid_from: draft.validFrom || null,
    valid_until: draft.validUntil || null,
    purposes,
    external_ai: draft.externalAi,
    notes: draft.notes.trim() || null,
  }
}
