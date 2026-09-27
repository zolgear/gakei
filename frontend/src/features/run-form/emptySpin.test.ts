import { describe, expect, it } from 'vitest'
import type { ParamDef } from '../../api/client'
import { directionFromProbeValue, spinFromDefault } from './emptySpin'

const floatDef: ParamDef = {
  name: 'strength',
  type: 'float',
  label: '強さ',
  default: 0.7,
  step: 0.1,
  required: false,
  description: '',
}

const intDef: ParamDef = {
  name: 'steps',
  type: 'int',
  label: 'ステップ数',
  default: 20,
  step: 5,
  required: false,
  description: '',
}

const intNoStepDef: ParamDef = {
  name: 'count',
  type: 'int',
  label: '枚数',
  default: 3,
  required: false,
  description: '',
}

const clampedDef: ParamDef = {
  name: 'clamped',
  type: 'float',
  label: 'クランプ',
  default: 0.95,
  step: 0.1,
  minimum: 0,
  maximum: 1,
  required: false,
  description: '',
}

const noDefaultDef: ParamDef = {
  name: 'nodef',
  type: 'float',
  label: '既定値なし',
  step: 0.1,
  required: false,
  description: '',
}

describe('spinFromDefault', () => {
  it('float の up で default + step を返す(小数の誤差を丸める)', () => {
    expect(spinFromDefault(floatDef, 'up')).toBe('0.8')
  })

  it('float の down で default - step を返す', () => {
    expect(spinFromDefault(floatDef, 'down')).toBe('0.6')
  })

  it('int の up/down で整数を返す', () => {
    expect(spinFromDefault(intDef, 'up')).toBe('25')
    expect(spinFromDefault(intDef, 'down')).toBe('15')
  })

  it('step が無ければ 1 刻み', () => {
    expect(spinFromDefault(intNoStepDef, 'up')).toBe('4')
    expect(spinFromDefault(intNoStepDef, 'down')).toBe('2')
  })

  it('maximum を超えるときはクランプする', () => {
    expect(spinFromDefault(clampedDef, 'up')).toBe('1')
  })

  it('minimum を下回るときはクランプする', () => {
    const def: ParamDef = { ...clampedDef, default: 0.05 }
    expect(spinFromDefault(def, 'down')).toBe('0')
  })

  it('default が無ければ null を返す', () => {
    expect(spinFromDefault(noDefaultDef, 'up')).toBeNull()
  })

  it('default が数値でなければ null を返す', () => {
    const def: ParamDef = { ...noDefaultDef, default: 'auto' }
    expect(spinFromDefault(def, 'up')).toBeNull()
  })
})

describe('directionFromProbeValue', () => {
  it('空欄から step=1 で上に回すと 1 が入るので up と判定する', () => {
    expect(directionFromProbeValue('1')).toBe('up')
  })

  it('下に回すと -1 が入るので down と判定する', () => {
    expect(directionFromProbeValue('-1')).toBe('down')
  })

  it('それ以外の値では判定しない', () => {
    expect(directionFromProbeValue('')).toBeNull()
    expect(directionFromProbeValue('2')).toBeNull()
    expect(directionFromProbeValue('0')).toBeNull()
  })
})
