"""写锁与闸判定的服务层确定性夹具。

HTTP 层无法控制 SQLite 等待队列的唤醒顺序（非 FIFO），
因此"持锁后复查闸态"这一核心不变量在服务层用显式连接顺序验证：
两个真实 sqlite 连接，一个先持写锁，另一个进入 IMMEDIATE 排队，
放锁后断言后到者在自己的事务内读到的是已关闭闸态并被 409 挡回。
"""
from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

from app.db import connect, db_write
from app.services.gates import GATE_LOAD, assert_gate_open, replay_gates, seal_shift

from conftest import clear_seed_scan


def test_gate_check_runs_inside_write_lock_after_concurrent_seal(client):
    """核心不变量：封班提交后，排队中的写事务拿锁时复查闸态必为关。

    连接 A 先 BEGIN IMMEDIATE 持锁；连接 B 的 IMMEDIATE 阻塞排队；
    A 在持锁期间完成封班并提交；B 拿锁后执行 assert_gate_open → 409，
    且 B 未写入任何扫描行（无半截写）。
    """
    clear_seed_scan(client)
    shift = client.get("/api/班次").json()[0]
    sid = shift["id"]

    conn_a = connect()
    conn_a.execute("BEGIN IMMEDIATE")  # A 持写锁

    b_got_lock = threading.Event()
    b_result = {}

    def tx_b():
        conn_b = connect()
        try:
            conn_b.execute("BEGIN IMMEDIATE")  # 排队等 A
            b_got_lock.set()
            # 关键：拿锁之后才判闸，读到的必须是 A 封班后的关闸
            try:
                assert_gate_open(conn_b, sid, GATE_LOAD)
                b_result["verdict"] = "open"
            except HTTPException as e:
                b_result["verdict"] = "closed"
                b_result["status"] = e.status_code
            conn_b.rollback()  # 被挡，不写任何数据
        finally:
            conn_b.close()

    tb = threading.Thread(target=tx_b)
    tb.start()
    try:
        # B 确定在排队（未拿锁）
        assert not b_got_lock.wait(timeout=1)
        # A 在自己持有的写事务内封班并提交
        snap = seal_shift(conn_a, sid)
        assert snap["全关"] is True
        conn_a.commit()
        tb.join(timeout=10)
        assert b_got_lock.wait(timeout=10)
        assert b_result["verdict"] == "closed"
        assert b_result["status"] == 409
    finally:
        conn_a.close()

    # B 回滚后无半截行；权威重放为全关
    assert all(r["barcode"] != "LOCKED01" for r in client.get("/api/扫描").json())
    with db_write() as conn:
        assert replay_gates(conn, sid)["装载闸"] == "关"


def test_seal_rolls_back_atomically_when_precondition_fails(client):
    """封班前置不满足：seal_shift 抛错，事务回滚后无任何闸事件/封班痕迹。"""
    clear_seed_scan(client)
    shift = client.get("/api/班次").json()[0]
    sid = shift["id"]
    # 造一件待路由
    r = client.post("/api/扫描", json={"条码": "ROLL01", "目的地码": "110"})
    assert r.status_code == 200

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(HTTPException) as ei:
            seal_shift(conn, sid)
        assert ei.value.status_code == 400
        conn.rollback()
    finally:
        conn.close()

    view = client.get(f"/api/审计/闸事件/{sid}").json()
    assert view["事件流"] == [] and view["封班快照"] is False
    # 回滚后写口仍开，装载照常
    assert client.post("/api/扫描", json={"条码": "ROLL02", "目的地码": "110"}).status_code == 200


def test_concurrent_seal_then_queued_http_load_is_gated(client):
    """HTTP 层并发（容忍唤醒顺序）：封班与装载竞争写锁。

    封班前置已清空。两种合法结局都必须满足不变量：
    - 封班先拿锁：封班 200，装载 409，无新行；
    - 装载先拿锁：装载 200 且为完整待路由行，封班因未清件被 400 挡回且无闸事件。
    """
    clear_seed_scan(client)
    shift = client.get("/api/班次").json()[0]
    sid = shift["id"]
    errors, seal_code, load_code = {}, {}, {}

    def sealer():
        seal_code["v"] = client.post(f"/api/班次/{sid}/封班").status_code

    def loader():
        r = client.post("/api/扫描", json={"条码": "HTTPRACE", "目的地码": "110"})
        load_code["v"] = r.status_code
        if r.status_code not in (200, 409, 400):
            errors["load"] = r.text

    ts = threading.Thread(target=sealer)
    tl = threading.Thread(target=loader)
    ts.start(); tl.start()
    ts.join(); tl.join()
    assert not errors

    rows = client.get("/api/扫描").json()
    race_row = [r for r in rows if r["barcode"] == "HTTPRACE"]
    view = client.get(f"/api/审计/闸事件/{sid}").json()

    if seal_code["v"] == 200:
        # 封班赢：装载必被闸挡，无半截行
        assert load_code["v"] == 409
        assert race_row == []
        assert view["重放与快照一致"] is True
    else:
        # 装载赢：完整行落库，封班被前置挡回，无闸事件、班次仍开
        assert seal_code["v"] == 400
        assert load_code["v"] == 200
        assert len(race_row) == 1 and race_row[0]["status"] == "待路由"
        assert view["事件流"] == [] and view["封班快照"] is False
