import React from 'react'
import { useGate, gateLabel } from '../gate'

const GATES = [
  ['装载闸', '皮带装载'],
  ['裁决闸', '路由裁决'],
  ['回流闸', '错分回流'],
  ['封袋闸', '开袋/入袋/封口'],
]

// 全局同刷的封班闸态灯：班次页、扫描册、口门页、封袋页、回流页、总览共用
export default function GateBanner() {
  const { gate } = useGate()
  if (!gate || gate.有班次 === false) {
    return <div className="gate-banner gate-none">当前无班次</div>
  }
  const locked = gate.全关
  return (
    <div className={`gate-banner ${locked ? 'gate-locked' : 'gate-open'}`}>
      <div className="gate-head">
        <span className={`gate-dot ${locked ? 'dot-red' : 'dot-green'}`} />
        <strong>{gate.营业日} {gate.班次名}</strong>
        <span className="gate-state">{locked ? '已封班 · 写口全关（只读）' : '未封班 · 写口全开'}</span>
        <span className="gate-gen">世代 {gate.世代号}</span>
      </div>
      <div className="gate-lamps">
        {GATES.map(([key, label]) => {
          const closed = gate.闸态?.[key] === '关'
          return (
            <span key={key} className={`lamp ${closed ? 'lamp-off' : 'lamp-on'}`}>
              <i className={`lamp-dot ${closed ? 'dot-red' : 'dot-green'}`} />
              {label}·{gateLabel(gate, key)}
            </span>
          )
        })}
      </div>
    </div>
  )
}
