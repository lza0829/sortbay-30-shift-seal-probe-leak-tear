"""封班写闸——全局唯一口径（方案一：多路拒写可重放为准）。

封班成功后，会改本班账的五个写入口必须全部关闭：
装载闸 / 裁决闸 / 回流闸 / 集包闸 / 封袋闸；只读接口不受影响。

判定权威来源是 shift_gate_events 事件流，shifts.sealed 是其物化快照，
两者在同一写事务内同刷。任意班次的闸态都可由事件流重放得到，
不依赖"当前班次"这类易随时间变化的查询。

封班回执只在五路全部拒写、且该结论可由事件流重放复现时才算成功；
任一路仍可写则整笔回滚，不留下任何封班宣称。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.db import (
    ALL_GATES,
    GATE_BAG,
    GATE_COLLECT,
    GATE_DIVERT,
    GATE_LOAD,
    GATE_ROUTE,
)
from app.services.common import audit, now_str

__all__ = [
    "ALL_GATES", "GATE_BAG", "GATE_COLLECT", "GATE_DIVERT", "GATE_LOAD", "GATE_ROUTE",
    "OPEN", "CLOSED",
    "GATE_PATHS", "gates_torn", "get_shift", "latest_shift", "replay_gates", "gate_snapshot",
    "assert_gate_open", "would_reject", "seal_receipt", "seal_shift",
]

# 闸态字面量
OPEN = "开"
CLOSED = "关"

# 每道闸对应的写入口（与“五路拒证”逐路对齐）。
GATE_PATHS = {
    GATE_LOAD: "过机装载 POST /api/扫描",
    GATE_ROUTE: "路由裁决 POST /api/扫描/{id}/路由",
    GATE_DIVERT: "错分回流 POST /api/回流批次",
    GATE_COLLECT: "集包开袋 POST /api/封袋/开袋",
    GATE_BAG: "封袋入袋/封口 POST /api/封袋/{id}/入袋|封口",
}


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
    """由闸事件流重放某班次五闸态（无事件即开）。

    这是写闸判定的权威函数：封班事件重放必须复现封班后的全关结果。
    """
    gates = {g: OPEN for g in ALL_GATES}
    rows = conn.execute(
        "SELECT gate, state FROM shift_gate_events WHERE shift_id=? ORDER BY id",
        (shift_id,),
    ).fetchall()
    for r in rows:
        if r["gate"] in gates:
            gates[r["gate"]] = r["state"]
    return gates


def gates_torn(gates: dict[str, str], sealed: bool) -> bool:
    """口径撕裂判定：封班宣称与五路拒证必须同命。

    撕裂的三种形态都要暴露：
    - 快照已封但五闸未全关（半截封班）；
    - 五闸全关但快照未封（幽灵关闸/脏写）；
    - 未封班却有部分闸被关（半截关闸，写口只关了一路）。
    """
    all_closed = all(v == CLOSED for v in gates.values())
    any_closed = any(v == CLOSED for v in gates.values())
    return bool(sealed) != all_closed or (not sealed and any_closed)


def gate_snapshot(conn, shift_id: int) -> dict:
    """班次 + 五闸态，供班次页/总览/口门页/扫描册/审计同刷。"""
    shift = get_shift(conn, shift_id)
    gates = replay_gates(conn, shift_id)
    all_closed = all(v == CLOSED for v in gates.values())
    return {
        "班次编号": shift["id"],
        "营业日": shift["business_day"],
        "班次名": shift["name"],
        "已封班": bool(shift["sealed"]),
        "封班时间": shift["sealed_at"],
        "世代号": shift["generation"],
        # 方案一口径：封班即全关。快照必须由事件流推得，而不是单看 sealed 标志，
        # 二者不一致时以更保守的"未全关"对外呈现，并由对账暴露撕裂。
        "全关": all_closed,
        "闸态": gates,
        # 写口的真实判决（与 assert_gate_open 完全一致）：快照已封 **或** 重放全关，
        # 任一成立即禁用。前端禁用写按钮必须照抄这个字段，不能只看其中一半。
        "写口禁用": bool(shift["sealed"]) or all_closed,
        "封班撕裂": gates_torn(gates, bool(shift["sealed"])),
    }


def assert_gate_open(conn, shift_id: int, gate: str) -> dict:
    """写口统一闸判定。半截写不得绕过：调用方必须已在 db_write 事务内，
    且本函数在拿到写锁之后执行，读到的闸态不可能被并发封班越过。"""
    shift = get_shift(conn, shift_id)
    state = replay_gates(conn, shift_id).get(gate, OPEN)
    if shift["sealed"] or state == CLOSED:
        raise HTTPException(409, f"班次已封班，{gate}已关闭，禁止写口")
    return shift


def would_reject(conn, shift_id: int, gate: str) -> bool:
    """只查不写：该班次当前状态下，某道闸的写口是否会被拒。

    与 assert_gate_open 同一判定逻辑，封班自证和只读探针共用，
    保证"回执宣称的拒"与"真实写口的拒"不会各说各话。
    """
    shift = conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
    if not shift:
        return True
    state = replay_gates(conn, shift_id).get(gate, OPEN)
    return bool(shift["sealed"]) or state == CLOSED


def seal_receipt(conn, shift_id: int) -> dict:
    """封班回执（可重放）：逐路给出写口判决，全部为“拒”才算收口。

    判决不是只看 sealed 标志就写“拒”——那样缺一条闸事件也会被快照掩盖。
    逐路同时给出：闸事件重放态、写口是否会被拒、拒写依据（闸关/仅快照）。
    “五路全拒”要求五路都真被拒；“五闸全关”要求五条闸事件都关；
    只有二者同时成立且与快照一致，才算“收口成立”。
    """
    shift = get_shift(conn, shift_id)
    gates = replay_gates(conn, shift_id)
    paths = []
    for gate in ALL_GATES:
        state = gates[gate]
        rejected = would_reject(conn, shift_id, gate)
        if state == CLOSED:
            basis = "闸关"
        elif rejected:
            basis = "仅快照"      # 只能靠 sealed 挡住，事件流没关，属撕裂
        else:
            basis = "无"
        paths.append({
            "写口": GATE_PATHS.get(gate, gate),
            "闸": gate,
            "重放闸态": state,
            "判决": "拒" if rejected else "通",
            "拒写依据": basis,
        })
    all_rejected = all(p["判决"] == "拒" for p in paths)
    all_gates_closed = all(v == CLOSED for v in gates.values())
    any_gate_closed = any(v == CLOSED for v in gates.values())
    # 同命：已封 ⇔ 五闸全关；未封则不得有任何闸被关（半截关闸也算撕裂）。
    replay_consistent = (all_gates_closed if shift["sealed"] else not any_gate_closed)
    return {
        "班次编号": shift["id"],
        "封班快照": bool(shift["sealed"]),
        "五路拒证": paths,
        "五路全拒": all_rejected,
        "五闸全关": all_gates_closed,
        "重放闸态": gates,
        "重放与快照一致": replay_consistent,
        # 同命收口：快照已封 + 五闸事件全关 + 五路写口全拒 + 重放一致。
        "收口成立": bool(shift["sealed"]) and all_gates_closed and all_rejected and replay_consistent,
        "回执可重放": all_gates_closed and replay_consistent,
    }


def seal_shift(conn, shift_id: int) -> dict:
    """封班：在调用方已持有的写事务内完成，全程同刷。

    1. 重查班次与未清件（持锁后复查，防并发半截）；
    2. shifts 置封班（世代不动，本系统臂内不支持解封）；
    3. 五闸各写一条关闭事件（重放依据）；
    4. 封班自证：由重放结果逐路复核写口必被拒，任一路仍通即抛错整笔回滚；
    5. 审计同事务落一条。
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
    cur = conn.execute(
        "UPDATE shifts SET sealed=1, sealed_at=? WHERE id=? AND sealed=0",
        (ts, shift_id),
    )
    # 防御性条件更新：若并发事务已封班，rowcount=0 即中止本事务
    if cur.rowcount == 0:
        raise HTTPException(409, "班次已被并发封班")
    for gate in ALL_GATES:
        conn.execute(
            "INSERT INTO shift_gate_events(shift_id, generation, gate, state, created_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (shift_id, shift["generation"], gate, CLOSED, ts, "封班联动关闸"),
        )

    receipt = seal_receipt(conn, shift_id)
    if not receipt["五路全拒"]:
        # 任一路重放后仍可写：封班宣称不成立，整笔回滚（含事件与快照）。
        raise HTTPException(409, "封班自证失败：仍有写口可通，已整笔回滚")
    if not receipt["回执可重放"]:
        # 光靠 sealed 快照挡住写口不算收口：五闸事件必须重放为全关，
        # 否则回执与事件流不同命，同样整笔回滚。
        raise HTTPException(409, "封班自证失败：事件流重放不全关，已整笔回滚")

    audit(conn, "班次", shift_id, "封班", "五闸联动关闭：装载/裁决/回流/集包/封袋；五路拒证 5/5")
    snap = gate_snapshot(conn, shift_id)
    snap["封班回执"] = receipt
    return snap
