import React, { useEffect, useState } from 'react'
import { api } from '../api'
import GateBanner from '../components/GateBanner'
import { useGate } from '../gate'

function GateMini({ 闸态, 全关, 撕裂 }) {
  if (撕裂) return <span className="lamp lamp-warn">撕裂（宣布与拒证不同命）</span>
  if (全关) return <span className="lamp lamp-off">五闸全关</span>
  return (
    <span className="lamp lamp-on">
      {Object.entries(闸态 || {}).map(([k, v]) => `${k}${v}`).join(' / ')}
    </span>
  )
}

export default function Shifts() {
  const [rows, setRows] = useState([])
  const [rec, setRec] = useState(null)
  const [replay, setReplay] = useState(null)
  const [err, setErr] = useState('')
  const { refresh } = useGate()
  const load = async () => {
    setRows(await api('/班次'))
    setRec(await api('/班次/对账'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const seal = async (id) => {
    setErr(''); setReplay(null)
    try {
      await api(`/班次/${id}/封班`, { method: 'POST' })
      await refresh()  // 全局闸态灯同刷
      await load()
    } catch (e) { setErr(e.message) }
  }
  const showReplay = async (id) => {
    try { setReplay(await api(`/审计/闸事件/${id}`)) }
    catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>班次对账</h2>
      <GateBanner />
      {err && <div className="err">{err}</div>}
      {rec && (
        <div className="grid">
          <div className="stat"><div className="n">{rec.扫描总数}</div><div className="l">扫描总数</div></div>
          <div className="stat"><div className="n">{rec.已路由及以上}</div><div className="l">已路由及以上</div></div>
          <div className="stat"><div className="n">{rec.已封袋数}</div><div className="l">已封袋数</div></div>
          <div className="stat"><div className="n">{rec.集包中袋数}</div><div className="l">集包中袋数</div></div>
          <div className="stat"><div className="n">{rec.未清件数}</div><div className="l">未清件数</div></div>
        </div>
      )}
      {rec && rec.封班回执 && (
        <div className="panel" style={{ marginTop: '1rem' }}>
          <h2>封班回执 · 五路拒证</h2>
          {rec.封班撕裂 && <div className="err">封班宣称与五路拒证撕裂：以拒证为准，不得显示已封可办。</div>}
          <p className="muted">
            五路全拒：{rec.封班回执.五路全拒 ? '是（5/5）' : '否'} ·
            回执可重放：{rec.封班回执.回执可重放 ? '是' : '否'}
          </p>
          <table>
            <thead><tr><th>写口</th><th>闸</th><th>重放闸态</th><th>判决</th><th>拒写依据</th></tr></thead>
            <tbody>
              {(rec.封班回执.五路拒证 || []).map((p) => (
                <tr key={p.闸}><td>{p.写口}</td><td>{p.闸}</td><td>{p.重放闸态}</td><td>{p.判决}</td><td>{p.拒写依据}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <table>
        <thead><tr><th>编号</th><th>营业日</th><th>班次</th><th>封班</th><th>写闸</th><th>操作</th></tr></thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td>{s.id}</td><td>{s.business_day}</td><td>{s.name}</td>
              <td>{s.sealed ? `已封班 ${s.sealed_at || ''}` : '未封班'}</td>
              <td><GateMini 闸态={s.闸态} 全关={s.全关} 撕裂={s.封班撕裂} /></td>
              <td>
                {!s.sealed && <button type="button" onClick={() => seal(s.id)}>封班</button>}{' '}
                <button type="button" className="secondary" onClick={() => showReplay(s.id)}>闸事件重放</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {replay && (
        <div className="panel" style={{ marginTop: '1rem' }}>
          <h2>封班事件重放 · 班次 {replay.班次编号}</h2>
          <p className="muted">
            封班快照：{replay.封班快照 ? '已封班' : '未封班'} ·
            重放与快照一致：{replay.重放与快照一致 ? '是' : '否'}
          </p>
          <p>重放闸态：{Object.entries(replay.重放闸态).map(([k, v]) => `${k}=${v}`).join('，')}</p>
          {replay.重放撕裂 && <div className="err">封班宣称与五路拒证撕裂：以拒证为准。</div>}
          <table>
            <thead><tr><th>写口</th><th>闸</th><th>重放闸态</th><th>判决</th><th>拒写依据</th></tr></thead>
            <tbody>
              {(replay.五路拒证 || []).map((p) => (
                <tr key={p.闸}><td>{p.写口}</td><td>{p.闸}</td><td>{p.重放闸态}</td><td>{p.判决}</td><td>{p.拒写依据}</td></tr>
              ))}
            </tbody>
          </table>
          <table>
            <thead><tr><th>#</th><th>世代</th><th>闸</th><th>态</th><th>时间</th><th>备注</th></tr></thead>
            <tbody>
              {replay.事件流.map((e) => (
                <tr key={e.id}><td>{e.id}</td><td>{e.generation}</td><td>{e.gate}</td><td>{e.state}</td><td>{e.created_at}</td><td>{e.note}</td></tr>
              ))}
              {replay.事件流.length === 0 && <tr><td colSpan="6" className="muted">无闸事件（未封班，五闸默认开）</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
