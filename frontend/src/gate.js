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

// 封班后任意写口是否禁用
export function isWriteLocked(gate) {
  return !!gate && gate.有班次 !== false && !!gate.全关
}

// 某道闸的中文状态
export function gateLabel(gate, name) {
  if (!gate || gate.有班次 === false) return '无班次'
  const s = gate.闸态?.[name]
  return s === '关' ? '关（已封班）' : '开'
}
