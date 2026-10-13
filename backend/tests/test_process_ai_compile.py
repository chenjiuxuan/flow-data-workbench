"""编译器规则：不调用模型，对应验收 2、3、4、5、7。"""
import pytest

from app.process_ai.assets import apply_preselect, catalog_rows, prefilter_assets
from app.process_ai.compile import PENDING_PREFIX, NotAProcess, compile_gen_draft
from app.process_ai.prompt import render_catalog
from app.process_ai.schema import GEN_DRAFT_JSON_SCHEMA, GenDraft
from app.process_schemas import ProcessEdge, ProcessNode

SOURCE = "张三提交申请后，判断是否通过。通过则由李四执行办理，驳回则结束。"


def _step(key, kind="task", **kwargs):
    return {"key": key, "kind": kind, "name": kwargs.pop("name", key), **kwargs}


def _compile(steps, source=SOURCE, **kwargs):
    gen = GenDraft.model_validate({"isProcess": True, "suggestedName": "请假审批", "summary": "请假", "steps": steps})
    return compile_gen_draft(
        gen,
        source_text=source,
        requested_name=kwargs.get("requested_name"),
        assets=kwargs.get("assets", []),
        ref_map=kwargs.get("ref_map", {}),
        match_assets=kwargs.get("match_assets", True),
    )


def _approval_steps():
    return [
        _step("s1", "start", name="开始", next="s2"),
        _step("s2", name="提交申请", owner="张三", next="s3"),
        _step("s3", "decision", name="是否通过",
              yes={"to": "s4", "label": "通过"}, no={"to": "s5", "label": "驳回"}),
        _step("s4", name="执行办理", owner="李四", next="s2"),
        _step("s5", "end", name="结束"),
    ]


def _reachable(draft):
    start = next(node.id for node in draft.nodes if node.type == "start")
    adjacent: dict[str, list[str]] = {}
    for edge in draft.edges:
        adjacent.setdefault(edge.source, []).append(edge.target)
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(adjacent.get(current, []))
    return seen


def test_schema_has_no_meta_field():
    assert set(ProcessNode.model_fields) == {
        "id", "type", "name", "x", "y", "owner", "description", "input", "output", "assetIds",
    }
    assert set(ProcessEdge.model_fields) == {
        "id", "source", "target", "label", "sourceHandle", "targetHandle",
    }


def test_approval_draft_satisfies_graph_rules():
    compiled = _compile(_approval_steps())
    draft = compiled.draft
    assert draft.name == "请假审批"
    assert len([node for node in draft.nodes if node.type == "start"]) == 1
    assert len([node for node in draft.nodes if node.type == "end"]) >= 1
    assert _reachable(draft) == {node.id for node in draft.nodes}
    assert {node.type for node in draft.nodes} <= {"start", "task", "decision", "end"}
    for node in draft.nodes:
        assert set(node.model_dump()) == set(ProcessNode.model_fields)
        assert node.x == 0 and node.y == 0
        assert node.assetIds == []
        assert node.id.startswith("ai-n-")
        assert PENDING_PREFIX not in node.name
        assert "AI 备注：" not in node.description
    by_id = {node.id: node for node in draft.nodes}
    for edge in draft.edges:
        assert edge.id.startswith("ai-e-")
        assert edge.source != edge.target
        assert edge.targetHandle == "in"
        source = by_id[edge.source]
        if source.type == "decision":
            assert edge.sourceHandle in {"yes", "no"}
            assert edge.label
        else:
            assert edge.sourceHandle == "out"
        assert source.type != "end"
        assert by_id[edge.target].type != "start"
    owners = {node.owner for node in draft.nodes}
    assert "张三" in owners and "李四" in owners
    cycle = [edge for edge in draft.edges if by_id[edge.source].name == "执行办理" and by_id[edge.target].name == "提交申请"]
    assert cycle and cycle[0].sourceHandle == "out"


def test_missing_decision_branch_is_filled_and_listed():
    steps = _approval_steps()
    steps[2] = _step("s3", "decision", name="是否通过", yes={"to": "s4", "label": ""}, no=None)
    steps[3] = _step("s4", name="执行办理", owner="李四", next="s5")
    compiled = _compile(steps)
    decision = next(node for node in compiled.draft.nodes if node.type == "decision")
    outgoing = [edge for edge in compiled.draft.edges if edge.source == decision.id]
    assert sorted(edge.sourceHandle for edge in outgoing) == ["no", "yes"]
    assert all(edge.label for edge in outgoing)
    yes = next(edge for edge in outgoing if edge.sourceHandle == "yes")
    assert yes.label == "是"
    placeholder = next(node for node in compiled.draft.nodes if "未说明的处理" in node.name)
    assert placeholder.name == f"{PENDING_PREFIX}未说明的处理"
    assert placeholder.type == "task"
    assert any(edge.target == placeholder.id and edge.sourceHandle == "no" for edge in outgoing)
    ends = [node for node in compiled.draft.nodes if node.type == "end"]
    assert len(ends) == 1
    assert any(edge.source == placeholder.id and edge.target == ends[0].id and edge.sourceHandle == "out"
               for edge in compiled.draft.edges)
    assert any(item["nodeId"] == placeholder.id and item["kind"] == "missing_branch" for item in compiled.uncertainties)
    assert "AI 备注：" in placeholder.description and "原文依据：" in placeholder.description


def test_unspecified_branch_adds_an_end_when_none_exists():
    compiled = _compile([
        _step("s1", "start", name="开始", next="s2"),
        _step("s2", "decision", name="是否通过", yes={"to": "s3", "label": "通过"}, no=None),
        _step("s3", name="办理"),
    ], source="判断是否通过。通过则办理。")
    placeholder = next(node for node in compiled.draft.nodes if "未说明的处理" in node.name)
    ends = [node for node in compiled.draft.nodes if node.type == "end"]
    assert ends
    assert any(edge.source == placeholder.id and edge.target in {node.id for node in ends}
               for edge in compiled.draft.edges)
    assert _reachable(compiled.draft) == {node.id for node in compiled.draft.nodes}


def test_owner_must_be_a_literal_substring():
    source = "提交后由张三审批，最后归档。"
    compiled = _compile([
        _step("s0", "start", name="开始", next="s1"),
        _step("s1", name="审批", owner="张三", next="s2"),
        _step("s2", name="备案", owner="王五", next="s3"),
        _step("s3", name="归档", owner="", next="s4"),
        _step("s4", "end", name="结束"),
    ], source=source)
    by_name = {node.name.removeprefix(PENDING_PREFIX): node for node in compiled.draft.nodes}
    assert by_name["审批"].owner == "张三"
    assert not by_name["审批"].name.startswith(PENDING_PREFIX)
    assert by_name["备案"].owner == ""
    assert by_name["备案"].name.startswith(PENDING_PREFIX)
    assert by_name["归档"].owner == ""
    assert "王五" not in {node.owner for node in compiled.draft.nodes}
    assert any(item["kind"] == "owner" and item["nodeId"] == by_name["备案"].id for item in compiled.uncertainties)


def test_multiple_owner_candidates_stay_empty():
    source = "张三和李四都提到了负责人。"
    compiled = _compile([
        _step("s0", "start", name="开始", next="s1"),
        _step("s1", name="审批", owner="", ownerCandidates=["张三", "李四"], next="s2"),
        _step("s2", name="归档", next="s3"),
        _step("s3", "end", name="结束"),
    ], source=source)
    approval = next(node for node in compiled.draft.nodes if node.name.endswith("审批"))
    assert approval.owner == ""
    assert any(item["kind"] == "owner" and "张三" in item["reason"] and "李四" in item["reason"]
               for item in compiled.uncertainties)


def test_missing_start_and_end_are_added():
    compiled = _compile([
        _step("s1", name="提交申请", next="s2"),
        _step("s2", name="归档"),
    ])
    draft = compiled.draft
    assert len([node for node in draft.nodes if node.type == "start"]) == 1
    assert len([node for node in draft.nodes if node.type == "end"]) >= 1
    assert _reachable(draft) == {node.id for node in draft.nodes}
    start = next(node for node in draft.nodes if node.type == "start")
    end = next(node for node in draft.nodes if node.type == "end")
    assert start.name.startswith(PENDING_PREFIX)
    assert end.name.startswith(PENDING_PREFIX)
    kinds = {item["kind"] for item in compiled.uncertainties}
    assert "missing_start" in kinds and "missing_end" in kinds


def test_extra_start_self_edges_and_illegal_ends_are_removed():
    compiled = _compile([
        _step("s1", "start", name="开始", next="s1"),
        _step("s2", "start", name="另一个开始", next="s3"),
        _step("s3", name="提交", next="s1"),
        _step("s4", name="复核", next="s5"),
        _step("s5", "end", name="结束", next="s4"),
    ])
    draft = compiled.draft
    assert len([node for node in draft.nodes if node.type == "start"]) == 1
    by_id = {node.id: node for node in draft.nodes}
    for edge in draft.edges:
        assert edge.source != edge.target
        assert by_id[edge.source].type != "end"
        assert by_id[edge.target].type != "start"
    assert _reachable(draft) == set(by_id)


def test_unreachable_node_is_linked_from_start():
    compiled = _compile([
        _step("s1", "start", name="开始", next="s2"),
        _step("s2", name="提交", next="s3"),
        _step("s3", "end", name="结束"),
        _step("s4", name="孤立复核"),
    ])
    draft = compiled.draft
    assert _reachable(draft) == {node.id for node in draft.nodes}
    orphan = next(node for node in draft.nodes if "孤立复核" in node.name)
    start = next(node for node in draft.nodes if node.type == "start")
    assert any(edge.source == start.id and edge.target == orphan.id and edge.sourceHandle == "out"
               for edge in draft.edges)
    assert any(item["nodeId"] == orphan.id and item["kind"] == "unreachable" for item in compiled.uncertainties)
    assert orphan.name.startswith(PENDING_PREFIX)


def test_quote_must_appear_in_source_and_prefix_survives_truncation():
    source = "先提交，再审批。"
    compiled = _compile([
        _step("s0", "start", name="开始", next="s1"),
        _step("s1", name="步" * 250, uncertain=True, reason="步骤顺序不明",
              sourceQuote="先提交", next="s2"),
        _step("s2", name="归档", uncertain=True, reason="步骤顺序不明",
              sourceQuote="这句话原文里没有", next="s3"),
        _step("s3", "end", name="结束"),
    ], source=source)
    long_node = next(node for node in compiled.draft.nodes if node.name.startswith(PENDING_PREFIX + "步"))
    assert len(long_node.name) <= 200
    assert not long_node.name.startswith(PENDING_PREFIX + PENDING_PREFIX)
    quoted = next(item for item in compiled.uncertainties if item["nodeId"] == long_node.id)
    assert quoted["kind"] == "order"
    assert quoted["sourceQuote"] == "先提交"
    assert "原文依据：先提交" in long_node.description
    invented = next(node for node in compiled.draft.nodes if "归档" in node.name)
    invented_note = next(item for item in compiled.uncertainties if item["nodeId"] == invented.id and item["kind"] == "order")
    assert invented_note["sourceQuote"] == ""


def test_asset_suggestions_never_enter_the_draft():
    assets = [
        {"id": "tbl:orders.orders", "name": "orders.orders", "type": "table", "attrs": {}},
        {"id": "sys:订单中心", "name": "订单中心", "type": "system", "attrs": {"desc": "订单主系统"}},
    ]
    compiled = _compile([
        _step("s0", "start", name="开始", next="s1"),
        _step("s1", name="核对", description="核对订单中心和 orders.orders", next="s2",
              assetRefs=[{"ref": "a1", "mention": "订单中心", "confidence": 0.95},
                         {"ref": "a999", "mention": "虚构系统", "confidence": 0.99}],
              unmatchedMentions=["外部短信网关"]),
        _step("s2", name="归档", next="s3"),
        _step("s3", "end", name="结束"),
    ], assets=assets, ref_map={"a1": assets[1]}, match_assets=True)
    assert all(node.assetIds == [] for node in compiled.draft.nodes)
    ids = {item["assetId"] for item in compiled.asset_suggestions}
    assert ids <= {"tbl:orders.orders", "sys:订单中心"}
    assert "a1" not in ids and "a999" not in ids
    order_center = next(item for item in compiled.asset_suggestions if item["assetId"] == "sys:订单中心")
    assert order_center["mention"] == "订单中心"
    assert order_center["preselect"] is True
    assert order_center["confidence"] == 0.95
    mentions = {item["mention"] for item in compiled.unmatched_assets}
    assert "虚构系统" in mentions and "外部短信网关" in mentions
    task = next(node for node in compiled.draft.nodes if node.name == "核对")
    assert "未匹配资产：" in task.input


def test_match_assets_disabled_leaves_ids_empty_and_skips_suggestions():
    assets = [{"id": "sys:订单中心", "name": "订单中心", "type": "system", "attrs": {}}]
    compiled = _compile([
        _step("s0", "start", name="开始", next="s1"),
        _step("s1", name="核对", description="订单中心", input="原始输入", next="s2",
              assetRefs=[{"ref": "a1", "mention": "订单中心", "confidence": 0.99}],
              unmatchedMentions=["外部短信网关"]),
        _step("s2", name="归档", next="s3"),
        _step("s3", "end", name="结束"),
    ], assets=assets, ref_map={"a1": assets[0]}, match_assets=False)
    assert compiled.asset_suggestions == []
    assert all(node.assetIds == [] for node in compiled.draft.nodes)
    assert {item["mention"] for item in compiled.unmatched_assets} == {"外部短信网关"}
    task = next(node for node in compiled.draft.nodes if node.name == "核对")
    assert task.input == "原始输入"


def test_short_flows_are_accepted_and_zero_tasks_are_not():
    with pytest.raises(NotAProcess) as not_process:
        compile_gen_draft(
            GenDraft.model_validate({"isProcess": False, "notProcessReason": "这是产品介绍", "steps": []}),
            source_text="产品介绍", requested_name=None, assets=[], ref_map={}, match_assets=False,
        )
    assert not_process.value.reason == "这是产品介绍"
    with pytest.raises(NotAProcess):
        _compile([
            _step("s1", "start", name="开始", next="s2"),
            _step("s2", "end", name="结束"),
        ])
    one = _compile([
        _step("s1", "start", name="开始", next="s2"),
        _step("s2", name="审批", next="s3"),
        _step("s3", "end", name="结束"),
    ], source="审批后结束。")
    assert any(node.name == "审批" for node in one.draft.nodes)
    short = _compile([
        _step("s1", "start", name="开始", next="s2"),
        _step("s2", name="提交", next="s3"),
        _step("s3", name="审批", next="s4"),
        _step("s4", "end", name="结束"),
    ], source="提交后进入审批。")
    names = {node.name for node in short.draft.nodes}
    assert "提交" in names and "审批" in names


def test_model_pending_prefix_is_listed_once():
    compiled = _compile([
        _step("s0", "start", name="开始", next="s1"),
        _step("s1", name="【待确认】审批", owner="张三", next="s2"),
        _step("s2", name="归档", next="s3"),
        _step("s3", "end", name="结束"),
    ], source="张三提交申请后进入审批，然后归档。")
    node = next(item for item in compiled.draft.nodes if "审批" in item.name)
    assert node.name == "【待确认】审批"
    assert node.name.count(PENDING_PREFIX) == 1
    assert any(item["nodeId"] == node.id for item in compiled.uncertainties)


def test_requested_name_wins_and_extra_fields_are_ignored():
    compiled = _compile(_approval_steps(), requested_name="  自定义名称  ")
    assert compiled.draft.name == "自定义名称"
    gen = GenDraft.model_validate({
        "isProcess": True, "mystery": True,
        "steps": [_step("s1", name="甲", next="s2", mystery=1), _step("s2", name="乙")],
    })
    assert gen.steps[0].name == "甲"


def test_catalog_hides_real_ids_and_prefilter_caps_assets():
    secret = {"id": "tbl:orders.orders", "name": "orders.orders", "type": "table",
              "attrs": {"password": "super-secret", "table": "orders"}}
    rows, mapping = catalog_rows([secret])
    rendered = render_catalog(rows)
    assert "tbl:orders.orders" not in rendered
    assert "super-secret" not in rendered
    assert "orders" in rendered
    assert mapping["a1"]["id"] == "tbl:orders.orders"
    assets = [{"id": f"sys:{index}", "name": f"系统{index}", "type": "system", "attrs": {}} for index in range(250)]
    assets.append({"id": "sys:特殊资产", "name": "特殊资产名称", "type": "system", "attrs": {}})
    chosen = prefilter_assets(assets, "特殊资产名称")
    assert len(chosen) == 200
    assert chosen[0]["id"] == "sys:特殊资产"


def test_preselect_requires_unique_high_confidence():
    single = apply_preselect([_suggestion(0.9)])
    assert single[0]["preselect"] is True
    low = apply_preselect([_suggestion(0.4)])
    assert low[0]["preselect"] is False
    many = apply_preselect([_suggestion(0.95, asset_id="a"), _suggestion(0.91, asset_id="b")])
    assert [item["preselect"] for item in many] == [False, False]


def test_strict_schema_closes_objects():
    def walk(schema):
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                assert schema.get("additionalProperties") is False
            for value in schema.values():
                walk(value)
        elif isinstance(schema, list):
            for value in schema:
                walk(value)
    walk(GEN_DRAFT_JSON_SCHEMA)
    assert "isProcess" in GEN_DRAFT_JSON_SCHEMA["required"]


def _suggestion(confidence, asset_id="asset"):
    return {
        "nodeId": "n1", "assetId": asset_id, "assetName": "名称",
        "mention": "订单", "confidence": confidence, "preselect": False,
    }
