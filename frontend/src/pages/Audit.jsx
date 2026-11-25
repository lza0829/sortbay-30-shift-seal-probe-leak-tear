import React, { useEffect, useState } from 'react'
import { api } from '../api'
import GateBanner from '../components/GateBanner'

export default function Audit() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const load = () => api('/审计').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  return (
    <div className="panel">
      <h2>审计</h2>
      <GateBanner />
      <p className="muted">只读审计流，封班不影响查询；封班联动在「班次对账」页可做闸事件重放。</p>
      {err && <div className="err">{err}</div>}
      <table>
        <thead><tr><th>#</th><th>类别</th><th>引用</th><th>动作</th><th>时间</th><th>备注</th></tr></thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id}>
              <td>{e.id}</td><td>{e.kind}</td><td>{e.ref_id ?? '-'}</td>
              <td>{e.action}</td><td>{e.created_at}</td><td>{e.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
