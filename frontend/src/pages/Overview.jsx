import React, { useEffect, useState } from 'react'
import { api } from '../api'
import GateBanner from '../components/GateBanner'
import { useGate, isTorn } from '../gate'


export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const { gate } = useGate()
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中…</div>
  const d = data.分布 || {}
  // 写口判决以后端「写口禁用」为准（快照已封 或 重放全关），撕裂时仍按真实写口展示。
  const locked = data.闸态?.写口禁用 ?? data.闸态?.全关
  const torn = data.封班撕裂 || isTorn(gate)
  return (
    <div className="panel">
      <h2>总览</h2>
      <GateBanner />
      <p className="muted">
        当前班次：{data.班次?.business_day} {data.班次?.name} ·
        {locked ? ' 已封班（五路全关，本页只读）' : ' 未封班'}
      </p>
      {torn && (
        <div className="err">
          封班宣称与五路拒证不同命：总览不得显示"已封可办"，请以班次对账页的五路拒证为准。
        </div>
      )}
      {data.封班回执 && (
        <table>
          <thead><tr><th>写口</th><th>闸</th><th>重放闸态</th><th>判决</th><th>拒写依据</th></tr></thead>
          <tbody>
            {(data.封班回执.五路拒证 || []).map((p) => (
              <tr key={p.闸}><td>{p.写口}</td><td>{p.闸}</td><td>{p.重放闸态}</td><td>{p.判决}</td><td>{p.拒写依据}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>
      <table>
        <thead><tr><th>口门</th><th>标题</th><th>状态</th><th>在口件数</th></tr></thead>
        <tbody>
          {(data.口门 || []).map((c) => (
            <tr key={c.id}><td>{c.code}</td><td>{c.title}</td><td>{c.status}</td><td>{c.在口件数}</td></tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
