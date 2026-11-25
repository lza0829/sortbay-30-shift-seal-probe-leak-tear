from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list, row_to_dict
from app.services.gates import gate_snapshot, gates_torn, replay_gates, seal_receipt

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    """只读总览：封班后照常可用，且带五闸态快照与班次同刷。"""
    with db() as conn:
        shift = conn.execute(
            "SELECT * FROM shifts ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not shift:
            return {"班次": None, "闸态": None, "分布": {}, "口门": []}
        sid = shift["id"]
        counts = {
            "待路由": conn.execute(
                "SELECT COUNT(*) c FROM scans WHERE shift_id=? AND status='待路由'", (sid,)
            ).fetchone()["c"],
            "已路由": conn.execute(
                "SELECT COUNT(*) c FROM scans WHERE shift_id=? AND status='已路由'", (sid,)
            ).fetchone()["c"],
            "错分回流": conn.execute(
                "SELECT COUNT(*) c FROM scans WHERE shift_id=? AND status='错分回流'", (sid,)
            ).fetchone()["c"],
            "已入袋": conn.execute(
                "SELECT COUNT(*) c FROM scans WHERE shift_id=? AND status='已入袋'", (sid,)
            ).fetchone()["c"],
            "已封袋": conn.execute(
                "SELECT COUNT(*) c FROM scans WHERE shift_id=? AND status='已封袋'", (sid,)
            ).fetchone()["c"],
        }
        chutes = rows_to_list(conn.execute(
            """
            SELECT c.*,
              (SELECT COUNT(*) FROM scans s
                 WHERE s.chute_id=c.id AND s.shift_id=?
                   AND s.status IN ('已路由','已入袋')) AS 在口件数
            FROM chutes c ORDER BY c.code
            """,
            (sid,),
        ).fetchall())
        gates = replay_gates(conn, sid)
        return {
            "班次": row_to_dict(shift),
            "闸态": gate_snapshot(conn, sid),
            "分布": counts,
            "口门": chutes,
            # 撕裂显式暴露：封班宣称与五路拒证不同命时必须为真，供总览醒目拒办。
            "封班撕裂": gates_torn(gates, bool(shift["sealed"])),
            "封班回执": seal_receipt(conn, sid) if shift["sealed"] else None,
        }

@router.get("/总览/探针")
def probe():
    """只读探针：逐路核对写口当前判决，与封班回执同一判定，不得各说各话。"""
    with db() as conn:
        shift = conn.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 1").fetchone()
        if not shift:
            return {"有班次": False}
        receipt = seal_receipt(conn, shift["id"])
        receipt["有班次"] = True
        return receipt
