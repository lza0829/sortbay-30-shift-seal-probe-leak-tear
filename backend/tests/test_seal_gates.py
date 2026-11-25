"""封班写闸夹具：覆盖封后仍装载/裁决/回流/封袋、未封误挡、
封班与装载半截、封后仍开新袋、事件重放与同刷。"""
from __future__ import annotations

import threading

from app.db import db_write
from app.services.gates import GATE_BAG, GATE_DIVERT, GATE_LOAD, GATE_ROUTE, replay_gates

from conftest import drive_one_to_sealed_bag, clear_seed_scan, seal_current

GATES = (GATE_LOAD, GATE_ROUTE, GATE_DIVERT, GATE_BAG)


# ---------- 未封班：写口全开，不存在误挡 ----------

def test_unsealed_all_write_gates_open(client):
    snap = client.get("/api/班次/当前闸态").json()
    assert snap["已封班"] is False and snap["全关"] is False
    assert all(v == "开" for v in snap["闸态"].values())
    # 装载/裁决/回流/封袋全链路在未封班时不被挡
    out = drive_one_to_sealed_bag(client, 1)
    # 重复封口撞业务校验（400），证明未封班时挡它的是袋状态而不是闸
    assert client.post(f"/api/封袋/{out['bag']['id']}/封口").status_code == 400
    # 回流只接受已路由/已入袋件：单独造一件路由后入袋（不封口）再回流
    r = client.post("/api/扫描", json={"条码": "DIV01", "目的地码": "110"})
    dscan = r.json()
    client.post(f"/api/扫描/{dscan['id']}/路由")
    dbag = client.post("/api/封袋/开袋", json={"口门编号": 1}).json()
    assert client.post(f"/api/封袋/{dbag['id']}/入袋",
                       json={"扫描编号": dscan["id"]}).status_code == 200
    r = client.post("/api/回流批次", json={"扫描编号": dscan["id"]})
    assert r.status_code == 200, r.text


def test_unsealed_bag_close_not_blocked(client):
    """未封误挡夹具：未封班时封口必须放行。"""
    out = drive_one_to_sealed_bag(client, 3)
    bag_after = next(b for b in client.get("/api/封袋").json() if b["id"] == out["bag"]["id"])
    assert bag_after["status"] == "已封袋"
    assert client.get("/api/班次/对账").json()["全关"] is False


# ---------- 封班后：四写口全关 ----------

def test_sealed_rejects_new_load(sealed_shift):
    """封后仍装载夹具：装载闸关闭，皮带不收新件。"""
    client, shift, _ = sealed_shift
    r = client.post("/api/扫描", json={"条码": "AFTER01", "目的地码": "110"})
    assert r.status_code == 409
    assert "装载闸" in r.json()["detail"]
    assert client.get("/api/扫描?状态=待路由").json() == []


def test_sealed_rejects_route(sealed_shift):
    """封后仍裁决夹具：裁决闸关闭。先造一件已扫待裁决件再封班。"""
    client, shift, _ = sealed_shift
    # 封班后无新件可造；用封班前回流件验证裁决闸：改为另起场景
    # 这里直接对封班前已封袋件尝试路由，先撞闸（409）而非状态校验
    scan = client.get("/api/扫描").json()[0]
    r = client.post(f"/api/扫描/{scan['id']}/路由")
    assert r.status_code == 409
    assert "裁决闸" in r.json()["detail"]


def test_sealed_rejects_divert(sealed_shift):
    """封后仍回流夹具：回流闸关闭。"""
    client, shift, _ = sealed_shift
    scan = client.get("/api/扫描").json()[0]
    r = client.post("/api/回流批次", json={"扫描编号": scan["id"]})
    assert r.status_code == 409
    assert "回流闸" in r.json()["detail"]


def test_sealed_rejects_bag_open_add_seal(sealed_shift):
    """封后仍开新袋/入袋/封口夹具：封袋闸关闭。"""
    client, shift, _ = sealed_shift
    r_open = client.post("/api/封袋/开袋", json={"口门编号": 1})
    assert r_open.status_code == 409 and "封袋闸" in r_open.json()["detail"]
    bag = client.get("/api/封袋").json()[0]
    scan = client.get("/api/扫描").json()[0]
    r_add = client.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": scan["id"]})
    assert r_add.status_code == 409 and "封袋闸" in r_add.json()["detail"]
    r_seal = client.post(f"/api/封袋/{bag['id']}/封口")
    assert r_seal.status_code == 409 and "封袋闸" in r_seal.json()["detail"]


def test_sealed_readonly_surfaces_still_work(sealed_shift):
    """只读面封班后仍可用：总览/扫描册/口门页/班次页/对账/审计/封袋/回流。"""
    client, shift, _ = sealed_shift
    for path in ("/api/总览", "/api/扫描", "/api/口门", "/api/班次",
                 "/api/班次/对账", "/api/审计", "/api/封袋", "/api/回流批次",
                 "/api/路由表", f"/api/审计/闸事件/{shift['id']}"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text)


# ---------- 闸事件重放：判定权威可重放，且与封班快照一致 ----------

def test_gate_replay_matches_snapshot(sealed_shift):
    client, shift, snap = sealed_shift
    view = client.get(f"/api/审计/闸事件/{shift['id']}").json()
    assert view["封班快照"] is True
    assert view["重放与快照一致"] is True
    assert view["重放闸态"] == {g: "关" for g in GATES}
    # 四条闸事件，逐闸一条
    assert len(view["事件流"]) == 4
    assert {e["gate"] for e in view["事件流"]} == set(GATES)
    assert all(e["state"] == "关" for e in view["事件流"])
    # 服务层重放与 HTTP 视图一致
    with db_write() as conn:
        assert replay_gates(conn, shift["id"]) == {g: "关" for g in GATES}


def test_replay_open_before_seal(client):
    """未封班无关闭事件，重放为全开。"""
    shift = client.get("/api/班次").json()[0]
    view = client.get(f"/api/审计/闸事件/{shift['id']}").json()
    assert view["重放与快照一致"] is True
    assert view["重放闸态"] == {g: "开" for g in GATES}


def test_seal_blocked_while_pending_writes_no_gate_events(client):
    """封班前置不满足时整体失败：不得留下半截闸事件/封班痕迹。"""
    clear_seed_scan(client)
    # 再造一件只裁决不入袋 → 封班应被挡
    r = client.post("/api/扫描", json={"条码": "PEND01", "目的地码": "110"})
    scan = r.json()
    client.post(f"/api/扫描/{scan['id']}/路由")
    shift = client.get("/api/班次").json()[0]
    r = client.post(f"/api/班次/{shift['id']}/封班")
    assert r.status_code == 400
    view = client.get(f"/api/审计/闸事件/{shift['id']}").json()
    assert view["事件流"] == [] and view["封班快照"] is False
    # 班次仍未封，写口仍开
    assert client.post("/api/扫描", json={"条码": "PEND02", "目的地码": "110"}).status_code == 200


# ---------- 半截写：服务层事务回滚后闸态与写范围不拧花 ----------

def test_half_write_rollback_keeps_gates_and_scope(client):
    """写口在事务中途失败必须整体回滚，不得留半截数据，也不得动闸。"""
    clear_seed_scan(client)
    shift = client.get("/api/班次").json()[0]
    before = client.get("/api/扫描").json()
    # 入袋口门不一致：在同一写事务内，失败后扫描状态不得被改
    out = drive_one_to_sealed_bag(client, 9)
    other = client.post("/api/扫描", json={"条码": "HALF01", "目的地码": "210"}).json()
    client.post(f"/api/扫描/{other['id']}/路由")  # 落到 A02(chute 2)
    r = client.post(f"/api/封袋/{out['bag']['id']}/入袋", json={"扫描编号": other["id"]})
    assert r.status_code == 400
    refreshed = client.get("/api/扫描").json()
    target = next(x for x in refreshed if x["id"] == other["id"])
    assert target["status"] == "已路由" and target["bag_id"] is None
    # 闸态未受影响，仍全开
    snap = client.get("/api/班次/当前闸态").json()
    assert all(v == "开" for v in snap["闸态"].values())
    assert len(client.get("/api/扫描").json()) == len(before) + 2


# ---------- 封班与装载并发：写锁串行化，封后无半截装载 ----------

def test_concurrent_load_first_then_seal_rejects_pending(client):
    """并发夹具的另一面：装载先拿锁并提交了待路由件，封班必须被前置条件挡回，
    且失败不留任何闸事件/封班痕迹（无半截封班）。"""
    import time

    clear_seed_scan(client)
    shift = client.get("/api/班次").json()[0]
    loaded = threading.Event()

    def loader():
        r = client.post("/api/扫描", json={"条码": "CONCFIRST", "目的地码": "110"})
        loaded.set()
        return r.status_code

    tl = threading.Thread(target=loader)
    tl.start()
    assert loaded.wait(timeout=5)
    tl.join()
    time.sleep(0.05)
    r = client.post(f"/api/班次/{shift['id']}/封班")
    assert r.status_code == 400 and "未封袋/未路由件" in r.json()["detail"]
    view = client.get(f"/api/审计/闸事件/{shift['id']}").json()
    assert view["事件流"] == [] and view["封班快照"] is False
    # 装载件完整存在，班次仍开，写口仍可用
    assert any(x["barcode"] == "CONCFIRST" and x["status"] == "待路由"
               for x in client.get("/api/扫描").json())
    assert client.get("/api/班次").json()[0]["sealed"] == 0


# ---------- 各页面同刷：班次页/总览/对账/口门页数据与闸态一致 ----------

def test_all_surfaces_reflect_sealed_state(sealed_shift):
    client, shift, snap = sealed_shift
    ov = client.get("/api/总览").json()
    assert ov["闸态"]["全关"] is True
    assert all(v == "关" for v in ov["闸态"]["闸态"].values())
    assert ov["班次"]["sealed"] == 1
    rec = client.get("/api/班次/对账").json()
    assert rec["全关"] is True and rec["闸态"] == {g: "关" for g in GATES}
    listed = next(s for s in client.get("/api/班次").json() if s["id"] == shift["id"])
    assert listed["全关"] is True and listed["闸态"] == {g: "关" for g in GATES}
    # 审计含封班联动记录
    audit = client.get("/api/审计").json()
    assert any(e["action"] == "封班" and "四闸" in e["note"] for e in audit)


def test_destination_write_then_no_post_seal_load(client):
    """条码目的地写入后：未封可装载裁决，封班后禁止新装载。"""
    clear_seed_scan(client)
    r = client.post("/api/扫描", json={"条码": "DEST01", "目的地码": "110"})
    assert r.status_code == 200
    # DEST01 走完收口，满足封班前置
    d = r.json()
    assert client.post(f"/api/扫描/{d['id']}/路由").status_code == 200
    bag = client.post("/api/封袋/开袋", json={"口门编号": 1}).json()
    client.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": d["id"]})
    client.post(f"/api/封袋/{bag['id']}/封口")
    seal_current(client)
    r2 = client.post("/api/扫描", json={"条码": "DEST02", "目的地码": "110"})
    assert r2.status_code == 409
