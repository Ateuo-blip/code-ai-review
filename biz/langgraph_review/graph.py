"""
LangGraph 审查图 — 分支 + 提交规范 + Diff 代码并行审查

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
              check_shas
                   │  (conditional: suspicious_shas ? diff_review : final_aggregate)
         ┌─────────┴─────────┐
         ▼                   ▼
   diff_review (fan-out)  (skip)
         │
         └─────────────────┘
                   │
            final_aggregate
                   │
                  END
"""
from langgraph.graph import StateGraph, START, END
from langgraph.constants import Send

from biz.langgraph_review.state import ReviewState
from biz.langgraph_review.nodes import (
    fetch_data,
    branch_review_node,
    commit_msg_review_node,
    diff_review_node,
    aggregate_node,
    check_shas_node,
    final_aggregate_node,
)


def build_graph():
    graph = StateGraph(ReviewState)

    graph.add_node("fetch_data", fetch_data)
    graph.add_node("branch_review", branch_review_node)
    graph.add_node("commit_msg_review", commit_msg_review_node)
    graph.add_node("aggregate", aggregate_node)
    graph.add_node("check_shas", check_shas_node)
    graph.add_node("diff_review", diff_review_node)
    graph.add_node("final_aggregate", final_aggregate_node)

    graph.add_edge(START, "fetch_data")
    graph.add_edge("fetch_data", "branch_review")
    graph.add_edge("fetch_data", "commit_msg_review")

    # branch + commit 两条并行路径汇聚到 aggregate
    graph.add_edge("branch_review", "aggregate")
    graph.add_edge("commit_msg_review", "aggregate")

    # aggregate → check_shas：收集结果后检查是否有可疑提交
    graph.add_edge("aggregate", "check_shas")

    # check_shas 是普通节点，它运行完后根据 state 决定下一步路由
    # 路由函数返回 Send 列表则 fan-out，返回字符串则走普通边
    def route_from_check(state):
        suspicious_shas = state.get("suspicious_shas", [])
        if suspicious_shas:
            return [Send("diff_review", {"current_sha": s}) for s in suspicious_shas]
        return "final_aggregate"

    graph.add_conditional_edges(
        "check_shas",
        route_from_check,
        {
            "diff_review": "diff_review",
            "final_aggregate": "final_aggregate",
        }
    )

    # diff_review fan-out 完成后汇聚到 final_aggregate
    graph.add_edge("diff_review", "final_aggregate")
    graph.add_edge("final_aggregate", END)

    return graph.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph