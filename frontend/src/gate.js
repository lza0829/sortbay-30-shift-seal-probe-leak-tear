import { useEffect, useState, useCallback } from 'react'
import { api } from './api'

// 全局唯一口径（方案一：全关为准）。所有页面共用同一份闸态，杜绝各亮一盏。
let cache = null
let inflight = null
const listeners = new Set()

async function fetchGate() {
  if (!inflight) {
    inflight = api('/班次/当前闸态')
      .then((d) => { cache = d; return d })
      .finally(() => { inflight = null })
  }
  const d = await inflight
  listeners.forEach((fn) => fn(d))
  return d
}

export function useGate() {
  const [gate, setGate] = useState(cache)
  useEffect(() => {
    listeners.add(setGate)
    if (!cache) fetchGate().catch(() => {})
    else fetchGate().then(setGate).catch(() => {})
    return () => listeners.delete(setGate)
  }, [])
  const refresh = useCallback(() => fetchGate().then(setGate), [])
  return { gate, refresh }
}

// 封班后任意写口是否禁用。后端给出唯一判决「写口禁用」（快照已封 或 重放全关），
// 前端照抄，避免只盯一半而出现「徽标亮仍可写/徽标灭写口已关」。
export function isWriteLocked(gate) {
  if (!gate || gate.有班次 === false) return false
  if (gate.写口禁用 !== undefined) return !!gate.写口禁用
  return !!gate.全关
}

// 封班宣称与五路拒证不同命：页面必须醒目拒办，而不是照常显示"已封可办"。
export function isTorn(gate) {
  if (!gate || gate.有班次 === false) return false
  if (gate.封班撕裂 !== undefined) return !!gate.封班撕裂
  const allClosed = gate.闸态 && Object.values(gate.闸态).every((v) => v === '关')
  return !!gate.已封班 !== !!allClosed
}

// 某道闸的中文状态
export function gateLabel(gate, name) {
  if (!gate || gate.有班次 === false) return '无班次'
  const s = gate.闸态?.[name]
  return s === '关' ? '关（已封班）' : '开'
}
