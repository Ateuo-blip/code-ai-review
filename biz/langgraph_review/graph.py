"""
LangGraph 审查图 — 分支 + 提交规范并行审查

图结构：

                 START
                   │
              fetch_data
                   │
            ┌──────┴──────┐
            ▼              ▼
      branch_review   commit_msg_review
            │              │
            └──────┬───────┘
                   ▼
              aggregate
                   │
                  END

diff_review 节点保留但暂不启用（领导指示：先不急）。
"""
from langgraph.graph import StateGraph, START, END

from biz.langgraph_review.state import ReviewState
from biz.langgraph_review.nodes import (
    fetch_data,
    branch_review_node,
    commit_msg_review_node,
    aggregate_node,
)


def build_graph():
    graph = StateGraph(ReviewState)

    graph.add_node("fetch_data", fetch_data)
    graph.add_node("branch_review", branch_review_node)
    graph.add_node("commit_msg_review", commit_msg_review_node)
    graph.add_node("aggregate", aggregate_node)

    graph.add_edge(START, "fetch_data")
    graph.add_edge("fetch_data", "branch_review")
    graph.add_edge("fetch_data", "commit_msg_review")

    # 两条并行路径都汇聚到 aggregate
    graph.add_edge("branch_review", "aggregate")
    graph.add_edge("commit_msg_review", "aggregate")

    graph.add_edge("aggregate", END)

    return graph.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph