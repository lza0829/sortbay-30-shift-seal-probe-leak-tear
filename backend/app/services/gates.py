from app.services import shiftseal_surface
"""封班写闸——全局唯一口径（方案一：全关为准）。

封班成功后，会改本班账的四个写入口全部关闭：
装载闸 / 裁决闸 / 回流闸 / 封袋闸；只读接口不受影响。

判定权威来源是 shift_gate_events 事件流，shifts.sealed 是其物化快照，
两者在同一写事务内同刷。任意班次的闸态都可由事件流重放得到，
不依赖"当前班次"这类易随时间变化的查询。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.db import ALL_GATES, GATE_BAG, GATE_DIVERT, GATE_LOAD, GATE_ROUTE
from app.services.common import audit, now_str

__all__ = [
    "ALL_GATES", "GATE_BAG", "GATE_DIVERT", "GATE_LOAD", "GATE_ROUTE",
    "OPEN", "CLOSED",
    "get_shift", "latest_shift", "replay_gates", "gate_snapshot",
    "assert_gate_open", "seal_shift",
]

# 闸态字面量
OPEN = "开"
CLOSED = "关"


def get_shift(conn, shift_id: int):
    shift = conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
    if not shift:
        raise HTTPException(404, "班次不存在")
    return shift


def latest_shift(conn):
    """最近一班（含已封班）。封班后的写口用它定位本班并统一过闸，
    由 assert_gate_open 返回 409，而不是各自给出不一致的报错。"""
    row = conn.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        raise HTTPException(400, "无班次，先开班")
    return row


def replay_gates(conn, shift_id: int) -> dict[str, str]:
    """由闸事件流重放某班次四闸态（无事件即开）。

    这是写闸判定的权威函数：封班事件重放必须复现封班后的全关结果。
    """
    gates = {g: OPEN for g in ALL_GATES}
    rows = conn.execute(
        "SELECT gate, state FROM shift_gate_events WHERE shift_id=? ORDER BY id",
        (shift_id,),
    ).fetchall()
    for r in rows:
        gates[r["gate"]] = r["state"]
    return gates


def gate_snapshot(conn, shift_id: int) -> dict:
    """班次 + 四闸态 + 装载游标，供班次页/总览/口门页/扫描册同刷。"""
    shift = get_shift(conn, shift_id)
    gates = replay_gates(conn, shift_id)
    return {
        "班次编号": shift["id"],
        "营业日": shift["business_day"],
        "班次名": shift["name"],
        "已封班": bool(shift["sealed"]),
        "封班时间": shift["sealed_at"],
        "世代号": shift["generation"],
        # 方案一口径：封班即全关，不存在"游标冻结但收口放行"的中间态，
        # 故不设独立的游标冻结标志；装载游标状态即装载闸，见 闸态[装载闸]。
        "全关": bool(shift["sealed"]),
        "闸态": gates,
    }


def assert_gate_open(conn, shift_id: int, gate: str) -> dict:
    """写口统一闸判定。半截写不得绕过：调用方必须已在 db_write 事务内，
    且本函数在拿到写锁之后执行，读到的闸态不可能被并发封班越过。"""
    shift = get_shift(conn, shift_id)
    state = replay_gates(conn, shift_id).get(gate, OPEN)
    if shift["sealed"] or state == CLOSED:
        raise HTTPException(409, f"班次已封班，{gate}已关闭，禁止写口")
    return shift


def seal_shift(conn, shift_id: int) -> dict:
    """封班：在调用方已持有的写事务内完成，全程同刷。

    1. 重查班次与未清件（持锁后复查，防并发半截）；
    2. shifts 置封班、世代不动（本系统臂内不支持解封，世代号仅随重开另立）；
    3. 四闸各写一条关闭事件（重放依据）；
    4. 审计同事务落一条。
    """
    shift = get_shift(conn, shift_id)
    if shift["sealed"]:
        raise HTTPException(400, "已封班")
    pending = conn.execute(
        "SELECT COUNT(*) c FROM scans "
        "WHERE shift_id=? AND status IN ('待路由','错分回流','已路由','已入袋')",
        (shift_id,),
    ).fetchone()["c"]
    open_bags = conn.execute(
        "SELECT COUNT(*) c FROM bags WHERE shift_id=? AND status='集包中'",
        (shift_id,),
    ).fetchone()["c"]
    if pending:
        raise HTTPException(400, f"仍有未封袋/未路由件 {pending}，禁止封班")
    if open_bags:
        raise HTTPException(400, f"仍有未封口集包袋 {open_bags}，禁止封班")

    ts = now_str()
    conn.execute(
        "UPDATE shifts SET sealed=1, sealed_at=? WHERE id=? AND sealed=0",
        (ts, shift_id),
    )
    # 防御性条件更新：若并发事务已封班，rowcount=0 即中止本事务
    if conn.execute("SELECT changes()").fetchone()[0] == 0:
        raise HTTPException(409, "班次已被并发封班")
    for gate in ALL_GATES:
        conn.execute(
            "INSERT INTO shift_gate_events(shift_id, generation, gate, state, created_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (shift_id, shift["generation"], gate, CLOSED, ts, "封班联动关闸"),
        )
    audit(conn, "班次", shift_id, "封班", "四闸联动关闭：装载/裁决/回流/封袋")
    return gate_snapshot(conn, shift_id)

_seal_shift_impl = seal_shift


def seal_shift(*args, **kwargs):
    out = _seal_shift_impl(*args, **kwargs)
    return shiftseal_surface.after_seal(out, *args, **kwargs)

