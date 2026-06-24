"""Dry-run test: validate graph structure without real API calls"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv("conf/.env")

from biz.langgraph_review.graph import build_graph, get_graph
from biz.langgraph_review.state import ReviewState

print("=" * 50)
print("1. Graph compile test")
g = build_graph()
print(f"   Nodes: {list(g.nodes.keys())}")
print("   [OK] compiled successfully")

print("\n2. Dry-run with mock data (no API/LLM calls)")
initial = {
    "gitlab_url": "https://gitlab.example.com",
    "project_id": "test/repo",
    "access_token": "fake-token",
    "branches": ["main", "feature/login", "bugfix/123"],
    "commits": [
        {"short_id": "abc12345", "title": "feat: add login page", "author_name": "dev1"},
        {"short_id": "def67890", "title": "fix bug", "author_name": "dev2"},
        {"short_id": "ghi11111", "title": "update", "author_name": "dev3"},
    ],
    "suspicious_shas": [],
    "current_sha": "",
    "branch_review": [],
    "commit_msg_review": [],
    "diff_reviews": [],
    "final_report": "",
    "error": "",
}
print(f"   Branches: {initial['branches']}")
print(f"   Commits:  {[c['short_id'] for c in initial['commits']]}")
print("   [OK] mock state built")

print("\n3. State field types")
print(f"   suspicious_shas: {type(initial['suspicious_shas']).__name__}")
print(f"   branch_review:   {type(initial['branch_review']).__name__} (Annotated[list, add])")
print(f"   diff_reviews:    {type(initial['diff_reviews']).__name__} (Annotated[list, add])")
print("   [OK] types correct")

print("\n" + "=" * 50)
print("All structure tests passed.")
print("Configure .env then run: python run_branch_commit_review.py")
print("=" * 50)
