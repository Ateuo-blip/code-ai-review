"""LangGraph state"""
from typing import TypedDict, Annotated
from operator import add


class ReviewState(TypedDict, total=False):
    # Input
    platform: str
    gitlab_url: str
    project_id: str
    access_token: str
    # Raw data
    branches: list[str]
    commits: list[dict]
    suspicious_shas: list[str]
    current_sha: str
    # Tag push data
    tag_name: str              # e.g. "v1.2.3"
    tag_message: str           # tag annotation
    # Parallel results
    branch_review: Annotated[list[str], add]
    commit_msg_review: Annotated[list[str], add]
    diff_reviews: Annotated[list[str], add]
    tag_review: Annotated[list[str], add]
    # Webhook context
    event_type: str            # 'push' / 'merge_request' / 'tag_push'
    merge_request_iid: int
    commit_sha: str
    # Output
    final_report: str
    error: str