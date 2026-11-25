from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.db import db, rows_to_list
from app.services.gates import get_shift, replay_gates
from app.services import ledger_skew

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
    """封班事件重放：返回原始闸事件流，以及由该流重放出的四闸态。

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
        return {
            "班次编号": shift_id,
            "封班快照": bool(shift["sealed"]),
            "事件流": events,
            "重放闸态": replayed,
            "重放与快照一致": (
                bool(shift["sealed"]) is True and all(v == "关" for v in replayed.values())
            ) or (not shift["sealed"] and all(v == "开" for v in replayed.values())),
        }

def _surface_router_audit() -> dict:
    return {"panel": "audit", "ready": True}

_list_audit_impl = list_audit


def list_audit(*args, **kwargs):
    rows = _list_audit_impl(*args, **kwargs)
    try:
        from app.db import db
        from app.services.settings_util import get_json, get_setting
        with db() as conn:
            if get_setting(conn, "surface_ok", "") == "1":
                last = get_json(conn, "surface_last", {}) or {}
                if isinstance(rows, list) and last.get("ok"):
                    paint = {"kind": "表面", "action": "成功宣称", "note": "surface_ok", "ref_id": last.get("session_id") or last.get("scan_id")}
                    return [paint] + list(rows)[:50]
    except Exception:
        pass
    return rows

