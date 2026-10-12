"""合并服务测试：自动为主、人工修正保护、消失标记。"""
import json

from sqlalchemy.orm import sessionmaker

from app.db import Base, engine
from app.models import EdgeRecord, NodeRecord
from app.services.merge import EdgeDraft, NodeDraft, merge_drafts

Session = sessionmaker(bind=engine)


def _fresh_session():
    Base.metadata.create_all(engine)
    session = Session()
    yield session
    session.rollback()
    for cls in (NodeRecord, EdgeRecord):
        session.query(cls).delete()
    session.commit()
    session.close()


def test_add_and_remerge_updates_auto_fields():
    session = next(_fresh_session())
    drafts = [NodeDraft("tbl:a.t1", "table", "a.t1", attrs={"owner": "张三"})]
    r1 = merge_drafts(session, "src1", drafts, [])
    assert r1.added_nodes == ["tbl:a.t1"]

    drafts[0].attrs["owner"] = "李四"
    r2 = merge_drafts(session, "src1", drafts, [])
    assert r2.added_nodes == []
    assert "tbl:a.t1" in r2.updated_nodes
    rec = session.get(NodeRecord, "tbl:a.t1")
    assert json.loads(rec.attrs)["owner"] == "李四"


def test_manual_fields_survive_remerge():
    session = next(_fresh_session())
    merge_drafts(session, "src1", [NodeDraft("tbl:a.t1", "table", "a.t1", attrs={"owner": "张三"})], [])

    # 人工把负责人改成 王五
    rec = session.get(NodeRecord, "tbl:a.t1")
    prov = rec.provenance_dict
    prov.setdefault("manual_fields", []).append("owner")
    rec.attrs = json.dumps({**rec.attrs_dict, "owner": "王五"}, ensure_ascii=False)
    rec.provenance = json.dumps(prov, ensure_ascii=False)
    session.commit()

    # 采集又把 owner 报成 李四 → 不应覆盖
    merge_drafts(session, "src1", [NodeDraft("tbl:a.t1", "table", "a.t1", attrs={"owner": "李四"})], [])
    rec = session.get(NodeRecord, "tbl:a.t1")
    assert json.loads(rec.attrs)["owner"] == "王五"


def test_manual_node_not_touched():
    session = next(_fresh_session())
    session.add(NodeRecord(id="sys:自建", type="system", name="自建系统", origin="manual",
                           attrs=json.dumps({"owner": "人工"})))
    session.commit()
    merge_drafts(session, "src1", [NodeDraft("sys:自建", "system", "自动系统", attrs={"owner": "自动"})], [])
    rec = session.get(NodeRecord, "sys:自建")
    assert rec.name == "自建系统"
    assert json.loads(rec.attrs)["owner"] == "人工"
    assert rec.vanished is False


def test_vanished_marked_not_deleted():
    session = next(_fresh_session())
    merge_drafts(session, "src1", [NodeDraft("tbl:a.t1", "table", "a.t1")], [])
    merge_drafts(session, "src1", [], [])  # 下一轮源里没有了
    rec = session.get(NodeRecord, "tbl:a.t1")
    assert rec is not None          # 没删
    assert rec.vanished is True     # 打了标记

    # 换个来源不影响
    merge_drafts(session, "src2", [NodeDraft("tbl:b.t2", "table", "b.t2")], [])
    assert session.get(NodeRecord, "tbl:a.t1").vanished is True


def test_reappear_clears_vanished():
    session = next(_fresh_session())
    merge_drafts(session, "src1", [NodeDraft("tbl:a.t1", "table", "a.t1")], [])
    merge_drafts(session, "src1", [], [])
    merge_drafts(session, "src1", [NodeDraft("tbl:a.t1", "table", "a.t1")], [])
    assert session.get(NodeRecord, "tbl:a.t1").vanished is False


def test_position_kept_on_merge():
    session = next(_fresh_session())
    merge_drafts(session, "src1", [NodeDraft("tbl:a.t1", "table", "a.t1", x=1.0, y=2.0)], [])
    rec = session.get(NodeRecord, "tbl:a.t1")
    rec.x, rec.y = 100.0, 200.0  # 用户在画布上拖动
    session.commit()
    merge_drafts(session, "src1", [NodeDraft("tbl:a.t1", "table", "a.t1", x=9.0, y=9.0)], [])
    rec = session.get(NodeRecord, "tbl:a.t1")
    assert (rec.x, rec.y) == (100.0, 200.0)


def test_edge_merge_and_protection():
    session = next(_fresh_session())
    edge = EdgeDraft("lineage:a->b", "tbl:a", "tbl:b", type="lineage", label="job1",
                     attrs={"fields": ["x -> y"]})
    merge_drafts(session, "src1", [], [edge])
    rec = session.get(EdgeRecord, "lineage:a->b")
    prov = rec.provenance_dict
    prov.setdefault("manual_fields", []).append("fields")
    rec.attrs = json.dumps({"fields": ["手工映射"]}, ensure_ascii=False)
    rec.provenance = json.dumps(prov, ensure_ascii=False)
    session.commit()

    merge_drafts(session, "src1", [], [EdgeDraft("lineage:a->b", "tbl:a", "tbl:b",
                                                 type="lineage", label="job1",
                                                 attrs={"fields": ["自动映射"]})])
    rec = session.get(EdgeRecord, "lineage:a->b")
    assert json.loads(rec.attrs)["fields"] == ["手工映射"]
