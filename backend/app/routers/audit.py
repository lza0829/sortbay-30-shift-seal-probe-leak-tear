from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.db import db, rows_to_list
from app.services.gates import get_shift, replay_gates, seal_receipt

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit():
    """只读审计流：封班不影响查询。"""
    with db() as conn:
        return rows_to_list(conn.execute(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT 200"
        ).fetchall())


@router.get("/审计/闸事件/{shift_id}")
def gate_events(shift_id: int):
    """封班事件重放：返回原始闸事件流，以及由该流重放出的五闸态。

    写闸判定的权威可重放视图——重放结果必须与 shifts.sealed 快照一致。
    """
    with db() as conn:
        shift = get_shift(conn, shift_id)
        events = rows_to_list(conn.execute(
            "SELECT id, shift_id, generation, gate, state, created_at, note "
            "FROM shift_gate_events WHERE shift_id=? ORDER BY id",
            (shift_id,),
        ).fetchall())
        replayed = replay_gates(conn, shift_id)
        receipt = seal_receipt(conn, shift_id)
        # 同命判定：已封班须"快照+事件流+五路拒证"三者同命；未封班只看快照与重放。
        consistent = receipt["收口成立"] if shift["sealed"] else receipt["重放与快照一致"]
        return {
            "班次编号": shift_id,
            "封班快照": bool(shift["sealed"]),
            "事件流": events,
            "重放闸态": replayed,
            "重放与快照一致": consistent,
            "重放撕裂": not consistent,
            "五路拒证": receipt["五路拒证"],
            "五路全拒": receipt["五路全拒"],
        }
