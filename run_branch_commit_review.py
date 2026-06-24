"""
分支 & 提交规范审查 —— CLI 入口（LangGraph 并行图版）

用法:
    python run_branch_commit_review.py

流程由 LangGraph 图的边严格保证（不靠 prompt）：
  fetch_data → branch_review ∥ commit_review → aggregate
"""

import os
import sys
import re
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv("conf/.env")

from biz.langgraph_review.graph import get_graph


def parse_gitlab_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("无效的 GitLab URL")
    gitlab_url = f"{parsed.scheme}://{parsed.netloc}"
    project_path = parsed.path.strip("/").removesuffix(".git")
    match = re.match(r"^([^/]+/[^/]+)", project_path)
    if not match:
        raise ValueError("无法从 URL 中提取项目路径")
    return gitlab_url, match.group(1)


def mask_token(token: str, show: int = 4) -> str:
    if len(token) <= show * 2:
        return "*" * len(token)
    return token[:show] + "*" * (len(token) - show * 2) + token[-show:]


def main():
    print("=" * 60)
    print("  分支 & 提交规范审查（LangGraph 并行图）")
    print("=" * 60)
    print()

    while True:
        url = input("GitLab 项目 URL\n(如 https://gitlab.example.com/group/repo): ").strip()
        try:
            gitlab_url, project_id = parse_gitlab_url(url)
            print(f"  GitLab: {gitlab_url}")
            print(f"  项目:   {project_id}")
            break
        except ValueError as e:
            print(f"  {e}\n")

    access_token = os.getenv("GITLAB_ACCESS_TOKEN") or input("Access Token: ").strip()
    if not access_token:
        print("Token 不能为空。")
        sys.exit(1)
    print(f"  Token:  {mask_token(access_token)}")

    provider = os.getenv("LLM_PROVIDER", "openai")
    print(f"  LLM:    {provider}")

    # 初始状态
    initial = {
        "gitlab_url": gitlab_url,
        "project_id": project_id,
        "access_token": access_token,
        "branches": [],
        "commits": [],
        "branch_review": [],
        "commit_review": [],
        "final_report": "",
        "error": "",
    }

    print("\n" + "-" * 60)
    print("图启动: fetch → branch ∥ commit → aggregate")
    print("-" * 60 + "\n")

    graph = get_graph()
    # StateGraph.invoke 严格按边走完所有节点
    final = graph.invoke(initial)

    if final.get("error"):
        print(f"\n❌ 流程出错: {final['error']}")
        sys.exit(1)

    report = final.get("final_report", "")
    print("\n" + "=" * 60)
    print(report)

    os.makedirs("outputs", exist_ok=True)
    path = os.path.join("outputs", "review_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已保存: {path}")


if __name__ == "__main__":
    main()
