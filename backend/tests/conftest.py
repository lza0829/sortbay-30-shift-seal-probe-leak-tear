"""夹具：每个用例独立临时库，TestClient + 一套封班全流程辅助。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# 必须在导入 app.config 之前指定隔离库
_TMP = Path(tempfile.mkdtemp(prefix="sortbay-test-"))
os.environ["SORTBAY_DB"] = str(_TMP / "case.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402


@pytest.fixture()
def client():
    """每用例一个全新库（含种子），用例间零串扰。"""
    db_path = Path(os.environ["SORTBAY_DB"])
    if db_path.exists():
        db_path.unlink()
    for wal in db_path.parent.glob(db_path.name + "-*"):
        wal.unlink()
    seed_if_empty()
    with TestClient(app) as c:
        yield c


# ---- 业务流程辅助：把一件从装载推到已封袋 ----

def _new_barcode(i: int) -> str:
    return f"CASE{i:06d}"


def drive_one_to_sealed_bag(client: TestClient, idx: int, chute_id: int = 1) -> dict:
    """装载 → 裁决 → 开袋 → 入袋 → 封口，返回各环节 id。"""
    r = client.post("/api/扫描", json={"条码": _new_barcode(idx), "目的地码": "110"})
    assert r.status_code == 200, r.text
    scan = r.json()
    r = client.post(f"/api/扫描/{scan['id']}/路由")
    assert r.status_code == 200, r.text
    r = client.post("/api/封袋/开袋", json={"口门编号": chute_id})
    assert r.status_code == 200, r.text
    bag = r.json()
    r = client.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": scan["id"]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/封袋/{bag['id']}/封口")
    assert r.status_code == 200, r.text
    return {"scan": scan, "bag": bag}


def clear_seed_scan(client: TestClient) -> None:
    """种子里有一笔待路由件，封班前清掉。"""
    pending = client.get("/api/扫描?状态=待路由").json()
    for s in pending:
        r = client.post(f"/api/扫描/{s['id']}/路由")
        assert r.status_code == 200, r.text
        routed = r.json()  # 路由后才有 chute_id
        bag = client.post("/api/封袋/开袋", json={"口门编号": routed["chute_id"]}).json()
        client.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": routed["id"]})
        client.post(f"/api/封袋/{bag['id']}/封口")


def seal_current(client: TestClient):
    shift = client.get("/api/班次").json()[0]
    r = client.post(f"/api/班次/{shift['id']}/封班")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def sealed_shift(client):
    """已封班的干净班次：种子件清零封口后封班。"""
    clear_seed_scan(client)
    snap = seal_current(client)
    shift = client.get("/api/班次").json()[0]
    return client, shift, snap
