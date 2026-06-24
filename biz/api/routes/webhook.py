"""
Webhook 路由模块（GitLab 专用版）
"""
import json
import os
from multiprocessing import Process
from urllib.parse import urlparse
from flask import Blueprint, request, jsonify

from biz.platforms.gitlab.webhook_handler import slugify_url
from biz.queue.worker import (
    handle_merge_request_event,
    handle_push_event,
)
from biz.utils.log import logger
from biz.utils.queue import handle_queue

# LangGraph parallel review (branch + commit convention check)
from biz.langgraph_review.graph import get_graph as get_langgraph_graph

webhook_bp = Blueprint('webhook', __name__)


def _run_langgraph_review(state: dict):
    """后台进程：跑 LangGraph 审查流程，结果自动贴 GitLab 评论。"""
    try:
        logger.info(f"LangGraph background review started: {state.get('event_type')} on {state.get('project_id')}")
        graph = get_langgraph_graph()
        final = graph.invoke(state)
        report = final.get("final_report", "")
        error = final.get("error", "")
        if error:
            logger.error(f"LangGraph review error: {error}")
        else:
            logger.info(f"LangGraph review completed, report length={len(report)}")
    except Exception as e:
        logger.error(f"LangGraph background review failed: {e}")


def _extract_from_gitlab_payload(data):
    """从标准 GitLab webhook payload 中提取审查所需字段。"""
    project = data.get("project", {})
    project_id = str(project.get("id", "") or project.get("path_with_namespace", ""))
    
    web_url = project.get("web_url", "")
    if web_url:
        parsed = urlparse(web_url)
        gitlab_url = f"{parsed.scheme}://{parsed.netloc}/"
    else:
        gitlab_url = os.getenv("GITLAB_URL", "http://localhost:8083")
    
    access_token = os.getenv("GITLAB_ACCESS_TOKEN") or request.headers.get("X-Gitlab-Token", "")
    event_type = data.get("object_kind", "push")
    commit_sha = data.get("checkout_sha", "")
    
    merge_request_iid = None
    if event_type == "merge_request":
        merge_request_iid = data.get("object_attributes", {}).get("iid")
    
    return project_id, gitlab_url, access_token, event_type, commit_sha, merge_request_iid


@webhook_bp.route("/review/langgraph-webhook", methods=["POST"])
def handle_langgraph_webhook():
    """LangGraph 并行审查 webhook（GitLab）。
    收到后立即返回 202，审查在后台异步执行。"""
    if not request.is_json:
        return jsonify({"error": "Invalid JSON"}), 400

    data = request.get_json()
    if not data:
        return jsonify({"error": "Empty body"}), 400

    if data.get("object_kind"):
        project_id, gitlab_url, access_token, event_type, commit_sha, merge_request_iid = \
            _extract_from_gitlab_payload(data)
    else:
        gitlab_url = data.get("gitlab_url") or os.getenv("GITLAB_URL", "http://localhost:8083")
        project_id = str(data.get("project_id") or os.getenv("GITLAB_PROJECT_ID", ""))
        access_token = data.get("access_token") or os.getenv("GITLAB_ACCESS_TOKEN", "")
        event_type = data.get("event_type", "push")
        commit_sha = data.get("commit_sha", "")
        merge_request_iid = data.get("merge_request_iid")

    if not project_id or not access_token:
        return jsonify({"error": "Missing project_id or access_token"}), 400

    # 从 webhook payload 提取本次事件相关的分支和提交
    event_branches = []
    event_commits = []
    if event_type == "push":
        ref = data.get("ref", "")
        branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref
        if branch:
            event_branches = [branch]
        # push 事件的 commits 直接从 payload 拿
        event_commits = data.get("commits", [])
    elif event_type == "merge_request":
        obj_attrs = data.get("object_attributes", {})
        source_branch = obj_attrs.get("source_branch", "")
        if source_branch:
            event_branches = [source_branch]
        # MR 的 commits 由 fetch_data 从 API 拉

    state = {
        "platform": "gitlab",
        "gitlab_url": gitlab_url,
        "project_id": project_id,
        "access_token": access_token,
        "event_type": event_type,
        "commit_sha": commit_sha,
        "merge_request_iid": merge_request_iid,
        "branches": event_branches,
        "commits": event_commits,
        "suspicious_shas": [],
        "current_sha": "",
        "branch_review": [],
        "commit_msg_review": [],
        "diff_reviews": [],
        "final_report": "",
        "error": "",
    }

    logger.info(f"LangGraph review forking: {event_type} on {project_id}")
    Process(target=_run_langgraph_review, args=(state,), daemon=True).start()

    return jsonify({
        "status": "accepted",
        "message": f"LangGraph review started for {event_type} on {project_id}",
    }), 202


@webhook_bp.route('/review/webhook', methods=['POST'])
def handle_webhook():
    """处理 GitLab Webhook（原系统）"""
    if not request.is_json:
        return jsonify({"error": "Invalid JSON"}), 400

    data = request.get_json()
    if not data:
        return jsonify({"error": "Empty body"}), 400

    return handle_gitlab_webhook(data)


def handle_gitlab_webhook(data):
    """处理 GitLab Webhook"""
    object_kind = data.get("object_kind")

    gitlab_url = os.getenv('GITLAB_URL') or request.headers.get('X-Gitlab-Instance')
    if not gitlab_url:
        repository = data.get('repository')
        if not repository:
            return jsonify({'message': 'Missing GitLab URL'}), 400
        homepage = repository.get("homepage")
        if not homepage:
            return jsonify({'message': 'Missing GitLab URL'}), 400
        try:
            parsed_url = urlparse(homepage)
            gitlab_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"
        except Exception as e:
            return jsonify({"error": f"Failed to parse homepage URL: {str(e)}"}), 400

    gitlab_token = os.getenv('GITLAB_ACCESS_TOKEN') or request.headers.get('X-Gitlab-Token')
    if not gitlab_token:
        return jsonify({'message': 'Missing GitLab access token'}), 400

    gitlab_url_slug = slugify_url(gitlab_url)

    logger.info(f'Received event: {object_kind}')
    logger.info(f'Payload: {json.dumps(data)}')

    if object_kind == "merge_request":
        handle_queue(handle_merge_request_event, data, gitlab_token, gitlab_url, gitlab_url_slug)
        return jsonify({'message': f'Request received(object_kind={object_kind}), will process asynchronously.'}), 200
    elif object_kind == "push":
        handle_queue(handle_push_event, data, gitlab_token, gitlab_url, gitlab_url_slug)
        return jsonify({'message': f'Request received(object_kind={object_kind}), will process asynchronously.'}), 200
    else:
        return jsonify({'message': f'Unsupported event type: {object_kind}. Only merge_request and push are supported.'}), 400