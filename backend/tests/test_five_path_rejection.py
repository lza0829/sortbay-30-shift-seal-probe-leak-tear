"""封班五路拒证现场夹具。

口径（全局唯一）：以多路拒写可重放为准。
封班回执成功 ⇔ 装载∪裁决∪回流∪集包∪封袋五路写口全部被拒，
且该结论能由 shift_gate_events 事件流重放复现；任一路仍可写则整笔回滚封班宣称。

覆盖：
- 徽标亮仍过机成功（伪收口）不得出现；
- 裁决一路漏、回流可写而集包已拒等单路泄漏逐路钉死；
- 封班与过机对撞不留下幽灵扫描行；
- 班次列、条码目的地、分拣线路由审计、扫描册、总览、审计、半截回滚、并发重放对账同命。
"""
from __future__ import annotations

import threading

from app.db import connect, db_write
from app.services.gates import GATE_BAG, GATE_COLLECT, GATE_DIVERT, GATE_LOAD, GATE_ROUTE, replay_gates

from conftest import clear_seed_scan, drive_one_to_sealed_bag, seal_current

FIVE = (GATE_LOAD, GATE_ROUTE, GATE_DIVERT, GATE_COLLECT, GATE_BAG)
CLOSED_ALL = {g: "关" for g in FIVE}
OPEN_ALL = {g: "开" for g in FIVE}


# ---------- 工具：造一件已路由件 / 开袋 / 入袋 ----------

def _load(client, barcode, dest="110"):
    r = client.post("/api/扫描", json={"条码": barcode, "目的地码": dest})
    assert r.status_code == 200, r.text
    return r.json()


def _route(client, scan_id):
    r = client.post(f"/api/扫描/{scan_id}/路由")
    assert r.status_code == 200, r.text
    return r.json()


def _open_bag(client, chute_id):
    r = client.post("/api/封袋/开袋", json={"口门编号": chute_id})
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 封班回执：五路拒证逐路为“拒”，且可重放 ----------

def test_seal_receipt_has_five_paths_all_rejected(sealed_shift):
    client, shift, snap = sealed_shift
    rec = client.get("/api/班次/对账").json()
    paths = rec["封班回执"]["五路拒证"]
    assert [p["闸"] for p in paths] == list(FIVE)
    assert all(p["判决"] == "拒" and p["重放闸态"] == "关" and p["拒写依据"] == "闸关" for p in paths)
    assert rec["封班回执"]["五路全拒"] is True
    assert rec["全关"] is True and rec["封班撕裂"] is False
    assert rec["闸态"] == CLOSED_ALL
    # 回执与审计视图同命
    view = client.get(f"/api/审计/闸事件/{shift['id']}").json()
    assert view["重放与快照一致"] is True and view["重放撕裂"] is False
    assert view["五路全拒"] is True
    assert len(view["事件流"]) == 5
    assert {e["gate"] for e in view["事件流"]} == set(FIVE)


def test_seal_response_itself_carries_receipt(sealed_shift):
    """封班成功回执当场就带五路拒证，不靠事后补画。"""
    client, shift, snap = sealed_shift
    receipt = snap["封班回执"]
    assert receipt["五路全拒"] is True and receipt["回执可重放"] is True
    assert receipt["收口成立"] is True and receipt["五闸全关"] is True
    assert all(p["判决"] == "拒" and p["拒写依据"] == "闸关" for p in receipt["五路拒证"])


# ---------- 夹具：徽标亮仍过机成功（伪收口）必须不存在 ----------

def test_badge_lit_cannot_load(sealed_shift):
    """封班徽标亮（已封/全关）后，过机装载必须拒，不得再落行。"""
    client, shift, _ = sealed_shift
    snap = client.get("/api/班次/当前闸态").json()
    assert snap["已封班"] is True and snap["全关"] is True
    before = len(client.get("/api/扫描").json())
    r = client.post("/api/扫描", json={"条码": "BADGE01", "目的地码": "110"})
    assert r.status_code == 409 and "装载闸" in r.json()["detail"]
    rows = client.get("/api/扫描").json()
    assert len(rows) == before
    assert all(x["barcode"] != "BADGE01" for x in rows)
    # 快照没被偷偷改回未封班
    assert client.get("/api/班次").json()[0]["sealed"] == 1


def test_route_leak_is_rejected(sealed_shift):
    """裁决一路漏：封班后对既有件路由必须 409，且不改扫描行、不写 route_events。"""
    client, shift, _ = sealed_shift
    scan = client.get("/api/扫描").json()[0]
    conn = connect()
    try:
        before_events = conn.execute("SELECT COUNT(*) c FROM route_events").fetchone()["c"]
    finally:
        conn.close()
    r = client.post(f"/api/扫描/{scan['id']}/路由")
    assert r.status_code == 409 and "裁决闸" in r.json()["detail"]
    conn = connect()
    try:
        after_events = conn.execute("SELECT COUNT(*) c FROM route_events").fetchone()["c"]
        row = conn.execute("SELECT status FROM scans WHERE id=?", (scan["id"],)).fetchone()
    finally:
        conn.close()
    assert after_events == before_events          # 无路由落入的半截事件
    assert row["status"] == scan["status"]         # 扫描行状态原样


# ---------- 夹具：回流可写而集包已拒（单路独立，不搞连坐假象）----------

def test_divert_and_collect_are_independent_paths(sealed_shift):
    """回流闸、集包闸是两条独立写口：各自 409，且报的是各自的闸名。"""
    client, shift, _ = sealed_shift
    scan = client.get("/api/扫描").json()[0]
    r_div = client.post("/api/回流批次", json={"扫描编号": scan["id"]})
    assert r_div.status_code == 409 and "回流闸" in r_div.json()["detail"]
    r_col = client.post("/api/封袋/开袋", json={"口门编号": 1})
    assert r_col.status_code == 409 and "集包闸" in r_col.json()["detail"]
    # 两条都被拒，账上无新增批次/袋（种子清件流程自身开过的袋仍可查）
    assert client.get("/api/回流批次").json() == []
    bags_before = client.get("/api/封袋").json()
    r_col2 = client.post("/api/封袋/开袋", json={"口门编号": 1})
    assert r_col2.status_code == 409
    assert len(client.get("/api/封袋").json()) == len(bags_before)


def test_bag_add_and_seal_rejected_after_seal(sealed_shift):
    """封袋闸：入袋/封口都拒，已封袋内容不被改写。"""
    client, shift, _ = sealed_shift
    bag = client.get("/api/封袋").json()[0]
    scan = client.get("/api/扫描").json()[0]
    r_add = client.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": scan["id"]})
    assert r_add.status_code == 409 and "封袋闸" in r_add.json()["detail"]
    r_seal = client.post(f"/api/封袋/{bag['id']}/封口")
    assert r_seal.status_code == 409 and "封袋闸" in r_seal.json()["detail"]
    bag_after = next(b for b in client.get("/api/封袋").json() if b["id"] == bag["id"])
    assert bag_after["status"] == "已封袋"


# ---------- 夹具：封班 / 过机对撞不留幽灵扫描行 ----------

def test_seal_load_collision_leaves_no_ghost_scan(client):
    """封班键与过机键对撞：要么封班赢（无行、五事件），要么装载赢（整行、无事件）。

    任何结果都不得出现"扫描册有行而班次宣称已封"或"徽标已封而脏写仍通"。
    """
    clear_seed_scan(client)
    shift = client.get("/api/班次").json()[0]
    sid = shift["id"]
    out = {}

    def sealer():
        out["seal"] = client.post(f"/api/班次/{sid}/封班").status_code

    def loader():
        out["load"] = client.post("/api/扫描", json={"条码": "GHOST01", "目的地码": "110"}).status_code

    ts, tl = threading.Thread(target=sealer), threading.Thread(target=loader)
    ts.start(); tl.start(); ts.join(); tl.join()

    rows = client.get("/api/扫描").json()
    ghost = [x for x in rows if x["barcode"] == "GHOST01"]
    snap = client.get("/api/班次/当前闸态").json()
    view = client.get(f"/api/审计/闸事件/{sid}").json()

    if out["seal"] == 200:
        assert out["load"] == 409
        assert ghost == []                              # 无幽灵行
        assert snap["全关"] is True and snap["已封班"] is True
        assert len(view["事件流"]) == 5 and view["五路全拒"] is True
        assert view["重放撕裂"] is False
    else:
        assert out["seal"] == 400
        assert out["load"] == 200
        assert len(ghost) == 1 and ghost[0]["status"] == "待路由"
        assert snap["全关"] is False and snap["已封班"] is False
        assert view["事件流"] == [] and view["封班快照"] is False
        # 未封班：五路仍开，可在同班继续写
        assert snap["闸态"] == OPEN_ALL


# ---------- 半截回滚：写口中途失败不留脏行，也不动闸 ----------

def test_half_write_rolls_back_scan_and_bag(client):
    clear_seed_scan(client)
    out = drive_one_to_sealed_bag(client, 11)          # 落 A01 的袋
    other = _route(client, _load(client, "HALF02", "210")["id"])  # 落 A02
    bag_a01 = out["bag"]
    before = client.get("/api/扫描").json()
    r = client.post(f"/api/封袋/{bag_a01['id']}/入袋", json={"扫描编号": other["id"]})
    assert r.status_code == 400                         # 口门不一致
    after = client.get("/api/扫描").json()
    target = next(x for x in after if x["id"] == other["id"])
    assert target["status"] == "已路由" and target["bag_id"] is None
    assert len(after) == len(before)
    assert client.get("/api/班次/当前闸态").json()["闸态"] == OPEN_ALL


# ---------- 撕裂现场：老实暴露，不靠改大字 ----------

def _force_torn_snapshot(sid: int) -> None:
    """模拟脏写/半截留下的撕裂：快照说已封，但事件流没关全。"""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE shifts SET sealed=1, sealed_at='2026-01-01 00:00:00' WHERE id=?", (sid,))
        conn.commit()
    finally:
        conn.close()


def test_torn_snapshot_is_surfaced_and_still_rejects(client):
    """快照已封但无闸事件：总览/对账/审计必须标撕裂，且写口仍按快照拒。"""
    clear_seed_scan(client)
    shift = client.get("/api/班次").json()[0]
    sid = shift["id"]
    _force_torn_snapshot(sid)

    ov = client.get("/api/总览").json()
    assert ov["封班撕裂"] is True
    rec = client.get("/api/班次/对账").json()
    assert rec["封班撕裂"] is True and rec["全关"] is False
    # 写口确实被拒（sealed 快照仍挡），但事件流复现不出这个结论 → 回执不可重放。
    assert rec["封班回执"]["五路全拒"] is True
    assert rec["封班回执"]["五闸全关"] is False
    assert all(p["拒写依据"] == "仅快照" for p in rec["封班回执"]["五路拒证"])
    assert rec["封班回执"]["回执可重放"] is False
    assert rec["封班回执"]["收口成立"] is False
    view = client.get(f"/api/审计/闸事件/{sid}").json()
    assert view["重放撕裂"] is True and view["重放与快照一致"] is False
    listed = next(s for s in client.get("/api/班次").json() if s["id"] == sid)
    assert listed["封班撕裂"] is True

    # 撕裂下不得"已封可办"，但也不能放任脏写：快照 sealed 仍挡装载
    r = client.post("/api/扫描", json={"条码": "TORN01", "目的地码": "110"})
    assert r.status_code == 409


def test_ghost_close_events_without_seal_are_surfaced(client):
    """反向撕裂：事件流全关但快照未封 → 写口按闸态拒，页面标撕裂。"""
    clear_seed_scan(client)
    shift = client.get("/api/班次").json()[0]
    sid = shift["id"]
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for g in FIVE:
            conn.execute(
                "INSERT INTO shift_gate_events(shift_id, generation, gate, state, created_at, note) "
                "VALUES (?, 1, ?, '关', '2026-01-01 00:00:00', '脏写伪造')",
                (sid, g),
            )
        conn.commit()
    finally:
        conn.close()

    rec = client.get("/api/班次/对账").json()
    assert rec["全关"] is True and rec["封班撕裂"] is True
    assert client.get("/api/总览").json()["封班撕裂"] is True
    # 闸态全关：即使 sealed=0，装载也必须被拒（重放为准）
    r = client.post("/api/扫描", json={"条码": "GHOSTCLOSE", "目的地码": "110"})
    assert r.status_code == 409 and "装载闸" in r.json()["detail"]
    with db_write() as c:
        assert replay_gates(c, sid) == CLOSED_ALL


def test_partial_close_without_seal_is_torn_and_still_gates(client):
    """半截：未封班却已关掉某一路（例如只写了裁决闸事件）→ 标撕裂且该路拒写。"""
    clear_seed_scan(client)
    shift = client.get("/api/班次").json()[0]
    sid = shift["id"]
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO shift_gate_events(shift_id, generation, gate, state, created_at, note) "
            "VALUES (?, 1, '裁决闸', '关', '2026-01-01 00:00:00', '半截关闸')",
            (sid,),
        )
        conn.commit()
    finally:
        conn.close()

    snap = client.get("/api/班次/当前闸态").json()
    assert snap["封班撕裂"] is True and snap["全关"] is False
    assert snap["闸态"]["裁决闸"] == "关"
    # 半截关闸的那一路必须按拒证挡写
    scan = _load(client, "HALF-CLOSE-1")
    r = client.post(f"/api/扫描/{scan['id']}/路由")
    assert r.status_code == 409 and "裁决闸" in r.json()["detail"]
    rec = client.get("/api/班次/对账").json()
    assert rec["封班撕裂"] is True
    assert rec["封班回执"] is None          # 未封班不给回执
    assert client.get("/api/总览").json()["封班撕裂"] is True


# ---------- 条码目的地 / 分拣线 / 路由审计 与封班同命 ----------

def test_barcode_destination_line_route_audit_before_and_after_seal(client):
    """封班前：条码+目的地+分拣线走完，路由审计留痕；
    封班后：同一扫描册、班次列、审计流全部只读且与封班拒证同命。"""
    clear_seed_scan(client)
    shift = client.get("/api/班次").json()[0]
    sid = shift["id"]
    scan = _load(client, "DEST-LINE-01", "210")
    assert scan["shift_id"] == sid and scan["dest_code"] == "210" and scan["line_id"]
    routed = _route(client, scan["id"])
    assert routed["status"] == "已路由" and routed["chute_id"]

    # 收口并封班
    bag = _open_bag(client, routed["chute_id"])
    assert client.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": scan["id"]}).status_code == 200
    assert client.post(f"/api/封袋/{bag['id']}/封口").status_code == 200
    seal_current(client)

    # 扫描册里该件真值不变、目的地/分拣线可查
    row = next(x for x in client.get("/api/扫描").json() if x["id"] == scan["id"])
    assert row["dest_code"] == "210" and row["line_id"] == scan["line_id"] and row["status"] == "已封袋"
    # 班次列：五闸关、无撕裂、回执可重放
    listed = next(s for s in client.get("/api/班次").json() if s["id"] == sid)
    assert listed["闸态"] == CLOSED_ALL and listed["封班撕裂"] is False
    # 路由审计：扫描件的路由落入事件仍在，且审计流含封班联动
    audit = client.get("/api/审计").json()
    assert any(e["kind"] == "扫描" and e["action"] == "路由裁决" for e in audit)
    assert any(e["action"] == "封班" and "五闸" in e["note"] for e in audit)
    # 审计流里不再有伪装的成功宣称行
    assert all(e["kind"] != "表面" for e in audit)
    # 条码目的地写入后，封班后不得再装载同类件
    assert client.post("/api/扫描", json={"条码": "DEST-LINE-02", "目的地码": "210"}).status_code == 409


# ---------- 口门在口 / 总览刷新 / 审计查询：封班前后同刷 ----------

def test_chute_in_place_and_overview_refresh(client):
    """口门在口：路由落入后总览「在口件数」随之变化；口门关闭时裁决被拒；封班后总览刷新仍只读一致。"""
    clear_seed_scan(client)
    chute = next(c for c in client.get("/api/口门").json() if c["code"] == "A01")
    scan = _load(client, "CHUTE-IN-1", "110")
    _route(client, scan["id"])
    ov = client.get("/api/总览").json()
    a01 = next(c for c in ov["口门"] if c["id"] == chute["id"])
    assert a01["在口件数"] >= 1

    # 口门闭：同目的地再裁决必须被业务校验挡（400），且不写路由
    assert client.post(f"/api/口门/{chute['id']}/开闭", params={"状态": "口门闭"}).status_code == 200
    scan2 = _load(client, "CHUTE-IN-2", "110")
    r = client.post(f"/api/扫描/{scan2['id']}/路由")
    assert r.status_code == 400 and "口门已闭" in r.json()["detail"]
    client.post(f"/api/口门/{chute['id']}/开闭", params={"状态": "口门开"})

    # 收口封班后：总览刷新仍只读可用，且在口件数与封班回执同命
    bag = _open_bag(client, chute["id"])
    client.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": scan["id"]})
    client.post(f"/api/封袋/{bag['id']}/封口")
    # CHUTE-IN-2 仍是待路由 → 先清掉
    for s2 in [x for x in client.get("/api/扫描?状态=待路由").json()]:
        rr = client.post(f"/api/扫描/{s2['id']}/路由")
        assert rr.status_code == 200, rr.text
        b = _open_bag(client, rr.json()["chute_id"])
        client.post(f"/api/封袋/{b['id']}/入袋", json={"扫描编号": s2["id"]})
        client.post(f"/api/封袋/{b['id']}/封口")
    seal_current(client)

    ov2 = client.get("/api/总览").json()
    assert ov2["闸态"]["全关"] is True and ov2["封班撕裂"] is False
    assert ov2["封班回执"]["五路全拒"] is True
    # 审计查询：封班联动 + 路由裁决都在，且无伪装宣称
    audit = client.get("/api/审计").json()
    assert any(e["action"] == "封班" and "五闸" in e["note"] for e in audit)
    assert any(e["action"] == "路由裁决" for e in audit)
    assert all(e["kind"] != "表面" for e in audit)


# ---------- 并发重放对账：封班后多读者看到的必须一致 ----------

def test_concurrent_readers_agree_after_seal(sealed_shift):
    """封班提交后，多个读者并发取总览/对账/审计重放，结论必须同命。"""
    client, shift, _ = sealed_shift
    sid = shift["id"]
    results = []

    def reader():
        ov = client.get("/api/总览").json()
        rec = client.get("/api/班次/对账").json()
        view = client.get(f"/api/审计/闸事件/{sid}").json()
        results.append({
            "ov_closed": ov["闸态"]["全关"], "ov_torn": ov["封班撕裂"],
            "rec_closed": rec["全关"], "rec_torn": rec["封班撕裂"],
            "rec_all_rejected": rec["封班回执"]["五路全拒"],
            "view_consistent": view["重放与快照一致"], "view_torn": view["重放撕裂"],
            "events": len(view["事件流"]),
        })

    ts = [threading.Thread(target=reader) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(results) == 5
    for r in results:
        assert r["ov_closed"] is True and r["ov_torn"] is False
        assert r["rec_closed"] is True and r["rec_torn"] is False
        assert r["rec_all_rejected"] is True
        assert r["view_consistent"] is True and r["view_torn"] is False
        assert r["events"] == 5


# ---------- 封班前置失败：五路不得被半截关闸 ----------

def test_failed_seal_leaves_all_five_paths_open(client):
    clear_seed_scan(client)
    scan = _load(client, "PREFAIL01")          # 待路由 → 封班前置不满足
    shift = client.get("/api/班次").json()[0]
    sid = shift["id"]
    r = client.post(f"/api/班次/{sid}/封班")
    assert r.status_code == 400
    view = client.get(f"/api/审计/闸事件/{sid}").json()
    assert view["事件流"] == [] and view["封班快照"] is False
    snap = client.get("/api/班次/当前闸态").json()
    assert snap["闸态"] == OPEN_ALL and snap["全关"] is False
    # 五路仍可写（本件继续收口）
    assert client.post(f"/api/扫描/{scan['id']}/路由").status_code == 200
