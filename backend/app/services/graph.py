"""图查询服务：三视图过滤、血缘追溯。"""
from sqlalchemy.orm import Session

from ..models import EdgeRecord, NodeRecord

# 视图 -> 可见节点类型
VIEW_TYPES: dict[str, list[str]] = {
    "system": ["system", "database", "api", "file", "external"],
    "business": ["process_step", "system"],
    "field": ["table"],
}
ALL_TYPES = sorted({t for types in VIEW_TYPES.values() for t in types})


def get_graph(session: Session, view: str = "system", include_vanished: bool = False):
    types = VIEW_TYPES.get(view, ALL_TYPES)
    node_q = session.query(NodeRecord).filter(NodeRecord.type.in_(types))
    if not include_vanished:
        node_q = node_q.filter(NodeRecord.vanished == False)  # noqa: E712
    nodes = node_q.all()
    ids = {n.id for n in nodes}

    edge_q = session.query(EdgeRecord).filter(EdgeRecord.source_id.in_(ids),
                                              EdgeRecord.target_id.in_(ids))
    if not include_vanished:
        edge_q = edge_q.filter(EdgeRecord.vanished == False)  # noqa: E712
    return nodes, edge_q.all()


def trace(session: Session, node_id: str, direction: str = "both", max_depth: int = 20):
    """从 node_id 出发做 BFS，返回相关的节点与边（跨视图，整张图）。"""
    edges = session.query(EdgeRecord).filter(EdgeRecord.vanished == False).all()  # noqa: E712
    out_adj: dict[str, list[EdgeRecord]] = {}
    in_adj: dict[str, list[EdgeRecord]] = {}
    for e in edges:
        out_adj.setdefault(e.source_id, []).append(e)
        in_adj.setdefault(e.target_id, []).append(e)

    hit_edge_ids: set[str] = set()
    hit_node_ids: set[str] = {node_id}

    def bfs(adj: dict[str, list[EdgeRecord]], start: str) -> None:
        frontier, depth = [start], 0
        visited = {start}
        while frontier and depth < max_depth:
            nxt: list[str] = []
            for current in frontier:
                for edge in adj.get(current, []):
                    hit_edge_ids.add(edge.id)
                    other = edge.target_id if adj is out_adj else edge.source_id
                    if other not in visited:
                        visited.add(other)
                        hit_node_ids.add(other)
                        nxt.append(other)
            frontier, depth = nxt, depth + 1

    if direction in ("upstream", "both"):
        bfs(in_adj, node_id)
    if direction in ("downstream", "both"):
        bfs(out_adj, node_id)

    nodes = session.query(NodeRecord).filter(NodeRecord.id.in_(hit_node_ids)).all()
    return nodes, [e for e in edges if e.id in hit_edge_ids]
