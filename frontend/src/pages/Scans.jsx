import React, { useEffect, useState } from 'react'
import { api } from '../api'
import GateBanner from '../components/GateBanner'
import { useGate, isWriteLocked } from '../gate'

export default function Scans() {
  const [rows, setRows] = useState([])
  const [barcode, setBarcode] = useState('')
  const [dest, setDest] = useState('110')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const { gate, refresh } = useGate()
  const locked = isWriteLocked(gate)
  const load = () => api('/扫描').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const create = async () => {
    setErr(''); setMsg('')
    try {
      await api('/扫描', { method: 'POST', body: JSON.stringify({ 条码: barcode, 目的地码: dest }) })
      setMsg('过机装载成功'); setBarcode(''); load()
    } catch (e) { setErr(e.message) }
  }
  const routeOne = async (id) => {
    setErr(''); setMsg('')
    try {
      await api(`/扫描/${id}/路由`, { method: 'POST' })
      setMsg('路由成功'); load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>过机扫描（扫描册）</h2>
      <GateBanner />
      {locked && <div className="lock-note">已封班：装载闸、裁决闸均已关闭，皮带不收新件、禁止裁决，仅可查询。</div>}
      <div className="row">
        <label>条码<input value={barcode} onChange={(e) => setBarcode(e.target.value)} disabled={locked} /></label>
        <label>目的地码<input value={dest} onChange={(e) => setDest(e.target.value)} disabled={locked} /></label>
        <button type="button" onClick={create} disabled={locked || !barcode}>
          {locked ? '装载闸已关' : '装载扫描'}
        </button>
        <button type="button" className="secondary" onClick={() => { load(); refresh() }}>刷新</button>
      </div>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <table>
        <thead><tr><th>编号</th><th>条码</th><th>目的地</th><th>状态</th><th>口门</th><th>操作</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td><td>{r.barcode}</td><td>{r.dest_code}</td><td>{r.status}</td><td>{r.chute_id || '-'}</td>
              <td>
                {(r.status === '待路由' || r.status === '错分回流') && (
                  <button type="button" disabled={locked} onClick={() => routeOne(r.id)}>
                    {locked ? '裁决闸已关' : '路由'}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
