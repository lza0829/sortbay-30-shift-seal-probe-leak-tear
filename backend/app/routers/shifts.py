from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, db_write, rows_to_list, row_to_dict
from app.services.common import audit
from app.services.gates import gate_snapshot, replay_gates, seal_shift
from app.services import shiftseal_surface

router = APIRouter(tags=["班次"])


class ShiftIn(BaseModel):
    营业日: str = Field(min_length=8)
    班次名: str = Field(min_length=1)


@router.get("/班次")
def list_shifts():
    """只读：封班后仍可用。每条班次带重放得到的四闸态。"""
    with db() as conn:
        rows = rows_to_list(conn.execute("SELECT * FROM shifts ORDER BY id DESC").fetchall())
        for r in rows:
            r["闸态"] = replay_gates(conn, r["id"])
            r["全关"] = bool(r["sealed"])
        return rows


@router.get("/班次/当前闸态")
def current_gate_state():
    """前端各页同刷用的闸态灯：无未封班时返回最近一班的关闭态。"""
    with db() as conn:
        row = conn.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return {"有班次": False}
        snap = gate_snapshot(conn, row["id"])
        snap["有班次"] = True
        return snap


@router.get("/班次/对账")
def reconcile(shift_id: int | None = None):
    with db() as conn:
        if shift_id is None:
            shift = conn.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 1").fetchone()
        else:
            shift = conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if not shift:
            raise HTTPException(404, "班次不存在")
        sid = shift["id"]
        scan_n = conn.execute("SELECT COUNT(*) c FROM scans WHERE shift_id=?", (sid,)).fetchone()["c"]
        routed = conn.execute(
            "SELECT COUNT(*) c FROM scans WHERE shift_id=? AND status IN ('已路由','已入袋','已封袋')",
            (sid,),
        ).fetchone()["c"]
        bag_n = conn.execute("SELECT COUNT(*) c FROM bags WHERE shift_id=?", (sid,)).fetchone()["c"]
        sealed_bag = conn.execute(
            "SELECT COUNT(*) c FROM bags WHERE shift_id=? AND status='已封袋'", (sid,)
        ).fetchone()["c"]
        open_bag = conn.execute(
            "SELECT COUNT(*) c FROM bags WHERE shift_id=? AND status='集包中'", (sid,)
        ).fetchone()["c"]
        divert_n = conn.execute(
            "SELECT COUNT(*) c FROM divert_batches WHERE shift_id=?", (sid,)
        ).fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) c FROM scans WHERE shift_id=? AND status IN ('待路由','错分回流')",
            (sid,),
        ).fetchone()["c"]
        return {
            "班次": row_to_dict(shift),
            "闸态": replay_gates(conn, sid),
            "全关": bool(shift["sealed"]),
            "扫描总数": scan_n,
            "已路由及以上": routed,
            "袋数": bag_n,
            "已封袋数": sealed_bag,
            "集包中袋数": open_bag,
            "回流批次数": divert_n,
            "未清件数": pending,
        }


@router.post("/班次")
def create_shift(body: ShiftIn):
    with db_write() as conn:
        open_one = conn.execute("SELECT id FROM shifts WHERE sealed=0").fetchone()
        if open_one:
            raise HTTPException(400, "仍有未封班班次，先封班再开新班")
        try:
            conn.execute(
                "INSERT INTO shifts(business_day, name, sealed, generation) VALUES (?, ?, 0, 1)",
                (body.营业日, body.班次名),
            )
        except Exception as e:
            raise HTTPException(400, f"开班失败: {e}")
        row = conn.execute(
            "SELECT * FROM shifts WHERE business_day=? AND name=?",
            (body.营业日, body.班次名),
        ).fetchone()
        audit(conn, "班次", row["id"], "开班", body.班次名)
        return row_to_dict(row)


@router.post("/班次/{shift_id}/封班")
def seal(shift_id: int):
    # 封班入口本身即写事务；内部复查 + 四闸事件 + 审计同刷，失败整体回滚
    with db_write() as conn:
        return seal_shift(conn, shift_id)

_seal_impl = seal


def seal(*args, **kwargs):
    out = _seal_impl(*args, **kwargs)
    return shiftseal_surface.after_seal(out, *args, **kwargs)

def _surface_router_shifts() -> dict:
    return {"panel": "shifts", "ready": True}

