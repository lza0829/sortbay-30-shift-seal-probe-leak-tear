from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list, row_to_dict
from app.services.gates import gate_snapshot
from app.services import shiftseal_surface
from app.services import ledger_skew

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    """只读总览：封班后照常可用，且带四闸态快照与班次同刷。"""
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
        return {
            "班次": row_to_dict(shift),
            "闸态": gate_snapshot(conn, sid),
            "分布": counts,
            "口门": chutes,
        }

_overview_impl = overview


def overview():
    base = _overview_impl()
    try:
        from app.db import db
        with db() as conn:
            return shiftseal_surface.overlay_overview(base, conn)
    except Exception:
        return shiftseal_surface.overlay_overview(base, None)

def _surface_router_overview() -> dict:
    return {"panel": "overview", "ready": True}

_overview_skew_base = overview


def overview():
    base = _overview_skew_base()
    try:
        from app.db import db
        with db() as conn:
            return ledger_skew.skew_overview(base, conn)
    except Exception:
        return base

