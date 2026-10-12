"""演示数据：订单业务场景。首次启动且库为空时自动灌入，也可从界面手动重灌（幂等合并）。
带初始坐标，三个视图（系统/业务/字段共用一套节点坐标）开箱即有合理布局。"""
from .merge import EdgeDraft, NodeDraft


def _node(id_: str, type_: str, name: str, x: float = 0, y: float = 0, **attrs) -> NodeDraft:
    return NodeDraft(id=id_, type=type_, name=name, attrs=attrs, x=x, y=y)


def _edge(id_: str, src: str, dst: str, type_: str, label: str, **attrs) -> EdgeDraft:
    return EdgeDraft(id=id_, source_id=src, target_id=dst, type=type_, label=label, attrs=attrs)


def demo_drafts() -> tuple[list[NodeDraft], list[EdgeDraft]]:
    nodes = [
        # 系统与外部方（系统视图主行 y≈320；业务视图里步骤在 y=60 一行，系统在下方）
        _node("sys:订单中心", "system", "订单中心", 100, 320, owner="电商平台组", desc="订单主系统"),
        _node("sys:支付中心", "system", "支付中心", 420, 160, owner="支付组"),
        _node("sys:库存系统", "system", "库存系统", 420, 520, owner="供应链组"),
        _node("sys:数据仓库", "system", "数据仓库", 780, 300, owner="数据组"),
        _node("sys:BI平台", "system", "BI 平台", 1100, 300, owner="数据组"),
        _node("sys:顺丰物流", "external", "顺丰物流（外部）", 780, 540),
        _node("db:mysql:orders-host:orders", "database", "orders@orders-host", 100, 100,
              db_type="mysql", host="orders-host", database="orders"),
        # 数据表（字段视图，从左到右的血缘链）
        _node("tbl:orders.orders", "table", "orders.orders", 80, 60,
              database="orders", table="orders",
              columns=[{"name": "order_id", "type": "BIGINT"}, {"name": "user_id", "type": "BIGINT"},
                       {"name": "amount", "type": "DECIMAL"}, {"name": "status", "type": "VARCHAR"},
                       {"name": "created_at", "type": "DATETIME"}]),
        _node("tbl:orders.order_detail", "table", "orders.order_detail", 80, 240,
              database="orders", table="order_detail"),
        _node("tbl:payments.payment", "table", "payments.payment", 80, 420,
              database="payments", table="payment"),
        _node("tbl:dw.ods_orders", "table", "dw.ods_orders", 420, 240, database="dw", table="ods_orders"),
        _node("tbl:dw.dwd_order", "table", "dw.dwd_order", 740, 240, database="dw", table="dwd_order"),
        _node("tbl:dw.dws_order_summary", "table", "dw.dws_order_summary", 1040, 240,
              database="dw", table="dws_order_summary"),
        _node("tbl:dw.ads_sales_daily", "table", "dw.ads_sales_daily", 1340, 240,
              database="dw", table="ads_sales_daily"),
        # 业务环节（业务视图顶部一行）
        _node("step:下单", "process_step", "① 下单", 100, 60),
        _node("step:支付", "process_step", "② 支付", 400, 60),
        _node("step:扣减库存", "process_step", "③ 扣减库存", 700, 60),
        _node("step:发货", "process_step", "④ 发货", 1000, 60),
    ]

    edges = [
        # 系统间流转
        _edge("flow:sys:订单中心->sys:支付中心:创建支付单", "sys:订单中心", "sys:支付中心",
              "flow", "接口", method="接口", frequency="实时", content="创建支付单", owner="张三"),
        _edge("flow:sys:支付中心->sys:订单中心:支付结果回调", "sys:支付中心", "sys:订单中心",
              "flow", "接口", method="接口", frequency="实时", content="支付结果回调", owner="张三"),
        _edge("flow:sys:订单中心->sys:库存系统:扣减库存指令", "sys:订单中心", "sys:库存系统",
              "flow", "接口", method="接口", frequency="实时", content="扣减库存指令", owner="王五"),
        _edge("flow:sys:订单中心->sys:顺丰物流:发货单", "sys:订单中心", "sys:顺丰物流",
              "flow", "接口", method="接口", frequency="实时", content="发货单", owner="赵六"),
        _edge("flow:sys:订单中心->sys:数据仓库:orders.orders", "sys:订单中心", "sys:数据仓库",
              "flow", "库表同步", method="库表同步", frequency="日", content="orders.orders", owner="李四"),
        _edge("flow:sys:支付中心->sys:数据仓库:payments.payment", "sys:支付中心", "sys:数据仓库",
              "flow", "库表同步", method="库表同步", frequency="日", content="payments.payment", owner="李四"),
        _edge("flow:sys:数据仓库->sys:BI平台:dw.ads_sales_daily", "sys:数据仓库", "sys:BI平台",
              "flow", "库表同步", method="库表同步", frequency="日", content="dw.ads_sales_daily", owner="李四"),
        # 产出/消费挂到表
        _edge("produces:sys:订单中心->tbl:orders.orders", "sys:订单中心", "tbl:orders.orders",
              "flow", "产出", relation="produces"),
        _edge("produces:sys:支付中心->tbl:payments.payment", "sys:支付中心", "tbl:payments.payment",
              "flow", "产出", relation="produces"),
        _edge("consumes:tbl:orders.orders->sys:库存系统", "tbl:orders.orders", "sys:库存系统",
              "flow", "消费", relation="consumes"),
        # 数仓表级血缘（含字段映射）
        _edge("lineage:tbl:orders.orders->tbl:dw.ods_orders", "tbl:orders.orders", "tbl:dw.ods_orders",
              "lineage", "ODS 同步", method="DataX 全量+增量",
              fields=["orders.order_id -> dw.ods_orders.id", "orders.amount -> dw.ods_orders.amount",
                      "orders.status -> dw.ods_orders.status"]),
        _edge("lineage:tbl:orders.order_detail->tbl:dw.ods_orders", "tbl:orders.order_detail",
              "tbl:dw.ods_orders", "lineage", "ODS 同步"),
        _edge("lineage:tbl:dw.ods_orders->tbl:dw.dwd_order", "tbl:dw.ods_orders", "tbl:dw.dwd_order",
              "lineage", "DWD 清洗", method="Hive 每日调度",
              fields=["dw.ods_orders.id -> dw.dwd_order.order_id", "dw.ods_orders.amount -> dw.dwd_order.pay_amount"]),
        _edge("lineage:tbl:dw.dwd_order->tbl:dw.dws_order_summary", "tbl:dw.dwd_order",
              "tbl:dw.dws_order_summary", "lineage", "DWS 汇总", method="Hive 每日调度",
              fields=["dw.dwd_order.pay_amount -> dw.dws_order_summary.total_amount"]),
        _edge("lineage:tbl:dw.dws_order_summary->tbl:dw.ads_sales_daily", "tbl:dw.dws_order_summary",
              "tbl:dw.ads_sales_daily", "lineage", "ADS 应用", method="Hive 每日调度"),
        # 业务环节与流转
        _edge("step:下单->step:支付", "step:下单", "step:支付", "flow", "订单数据"),
        _edge("step:支付->step:扣减库存", "step:支付", "step:扣减库存", "flow", "支付完成事件"),
        _edge("step:扣减库存->step:发货", "step:扣减库存", "step:发货", "flow", "出库单"),
        _edge("uses:step:下单->sys:订单中心", "step:下单", "sys:订单中心", "uses", "承载系统"),
        _edge("uses:step:支付->sys:支付中心", "step:支付", "sys:支付中心", "uses", "承载系统"),
        _edge("uses:step:扣减库存->sys:库存系统", "step:扣减库存", "sys:库存系统", "uses", "承载系统"),
        _edge("uses:step:发货->sys:顺丰物流", "step:发货", "sys:顺丰物流", "uses", "外部对接"),
    ]
    return nodes, edges
