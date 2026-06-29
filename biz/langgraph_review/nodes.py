"""
LangGraph 节点 —— 支持逐提交并行审查

图流程（边硬控）：
  fetch_data ──┬── branch_review ──────┐
               └── commit_msg_review   │
                       │               │
                  ┌────┴────┐ (Send 扇出)
                  ▼    ▼    ▼
                diff  diff  diff  ← 每条提交并行审查！
                  │    │    │
                  └────┴────┘
                       │               │
                       └───────┬───────┘
                               ▼
                           aggregate
"""

import os
import requests
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

import yaml
from biz.langgraph_review.state import ReviewState

def _load_prompts():
    """Load all review prompts from conf/langgraph_prompts.yml"""
    with open("conf/langgraph_prompts.yml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

_PROMPTS = _load_prompts()

# SSL workaround for corporate networks / Windows
import ssl
import urllib3
from requests.adapters import HTTPAdapter
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class SSLAdapter(HTTPAdapter):
    """Custom adapter that creates a permissive SSL context"""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)

_session = None

def _get_session():
    """Get or create a requests session with SSL workaround"""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.mount("https://", SSLAdapter())
    return _session


def _get_llm():
    provider = os.getenv("LLM_PROVIDER", "openai")
    if provider == "deepseek":
        return ChatOpenAI(
            model="deepseek-chat",
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
            temperature=0.1,
        )
    elif provider == "openai":
        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            temperature=0.1,
        )
    else:
        return ChatOpenAI(
            model=os.getenv("LLM_MODEL", "gpt-4o"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
            temperature=0.1,
        )


def _call_llm(system: str, user: str) -> str:
    llm = _get_llm()
    return llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=user),
    ]).content


# ============================================================
# 节点 1: 拉取数据（GitLab）
# 节点 1: 拉取数据（GitLab）— 只拉本次事件相关的分支和提交
# ============================================================
def fetch_data(state: ReviewState) -> dict:
    event_type = state.get("event_type", "push")
    branches = state.get("branches", [])
    commits = state.get("commits", [])

    # push 事件：webhook 已带 commit 数据，直接跳过
    if event_type == "push" and branches and commits:
        print(f"[fetch_data] push mode: {len(branches)} branch(es), {len(commits)} commit(s)")
        return {}

    # merge_request 事件：拉取 MR 专属的提交
    if event_type == "merge_request":
        return _fetch_mr_data(state)

    # 手动模式 / 无事件上下文：拉全量（兼容旧逻辑）
    return _fetch_gitlab_data(state["gitlab_url"], state["project_id"], state["access_token"])




def _fetch_gitlab_data(gitlab_url, project_id, access_token):
    encoded = requests.utils.quote(project_id, safe="")
    base = f"{gitlab_url}/api/v4/projects/{encoded}"
    headers = {"Private-Token": access_token}
    branches, commits = [], []
    try:
        resp = requests.get(f"{base}/repository/branches?per_page=50", headers=headers, verify=False, timeout=30)
        branches = [b["name"] for b in resp.json()]
    except Exception as e:
        print(f"[fetch_data] branch fetch failed: {e}")
    try:
        resp = requests.get(f"{base}/repository/commits?per_page=30", headers=headers, verify=False, timeout=30)
        commits = [{"short_id": c.get("short_id", c["id"][:8]), "title": c["title"], "author_name": c.get("author_name", "unknown")} for c in resp.json()]
    except Exception as e:
        print(f"[fetch_data] commit fetch failed: {e}")
    return {"branches": branches, "commits": commits}
def _fetch_mr_data(state):
    """拉取 MR 的源分支名 + MR 中的提交列表"""
    gitlab_url = state["gitlab_url"]
    project_id = state["project_id"]
    token = state["access_token"]
    merge_request_iid = state.get("merge_request_iid")
    encoded = requests.utils.quote(project_id, safe="")
    base = f"{gitlab_url}/api/v4/projects/{encoded}"
    headers = {"Private-Token": token}

    branches = state.get("branches", [])  # webhook 已填 source_branch

    try:
        if merge_request_iid:
            cr = requests.get(
                f"{base}/merge_requests/{merge_request_iid}/commits",
                headers=headers, verify=False, timeout=30)
            cr.raise_for_status()
            commits = cr.json()
        else:
            commits = []
        print(f"[fetch_data:MR] branch={branches}, {len(commits)} commits")
        return {"branches": branches, "commits": commits}
    except requests.RequestException as e:
        return {"error": f"MR API failed: {e}"}



# ============================================================
# 节点 2: 分支命名审查（并行分支 A）
# ============================================================
BRANCH_SYSTEM = """你是资深软件架构师，审查分支命名规范。

审查标准：
1. 命名一致性：是否小写、连字符分隔、统一风格
2. 可读性：是否清晰表达目的
3. 类型前缀：feature/ bugfix/ hotfix/ release/ 等
4. 唯一性：是否重复或过于相似
5. 长度：是否过长

输出 Markdown：
- 统计（总数、规范数、不规范数）
- 优点
- 问题分支（列出具体分支名）
- 改进建议"""


def branch_review_node(state: ReviewState) -> dict:
    branches = state.get("branches", [])
    if not branches:
        return {"branch_review": ["⚠️ 无分支数据"]}
    user = "分支列表：\n" + "\n".join(f"- {b}" for b in branches)
    try:
        result = _call_llm(_PROMPTS["branch_review"]["system_prompt"], user)
        print("[branch_review] 完成")
        return {"branch_review": [result]}
    except Exception as e:
        return {"branch_review": [f"❌ 分支审查失败: {e}"]}


# ============================================================
# 节点 3: 提交信息审查 → 输出可疑 SHA 列表
# ============================================================
# COMMIT_MSG_SYSTEM loaded from conf/langgraph_prompts.yml


def commit_msg_review_node(state: ReviewState) -> dict:
    """审查提交信息格式，输出 Markdown 报告"""
    commits = state.get("commits", [])
    if not commits:
        return {"commit_msg_review": ["⚠️ 无提交数据"]}

    summary_lines = []
    for c in commits[:30]:
        short = c.get("short_id", c.get("id", "")[:8])
        title = c.get("title", "").strip()
        author = c.get("author_name", "?")
        summary_lines.append(f"[{short}] {title} — {author}")

    user = "提交列表：\n" + "\n".join(summary_lines) + "\n\n请按规范格式输出审查报告。"
    result = _call_llm(_PROMPTS["commit_msg_review"]["system_prompt"], user)
    print(f"[commit_msg_review] 完成")

    # 从报告中提取违规 SHA（用于统计，不影响 diff 路由）
    suspicious = []
    for c in commits:
        short = c.get("short_id", c.get("id", "")[:8])
        if short in result:
            suspicious.append(short)

    print(f"[commit_msg_review] 违规提交: {suspicious if suspicious else '全部规范'}")
    return {
        "suspicious_shas": suspicious,
        "commit_msg_review": [result],
    }

# ============================================================
# 节点 4: 单条提交的 diff 审查（并行 fan-out 目标）
# ============================================================
# DIFF_REVIEW_SYSTEM loaded from conf/langgraph_prompts.yml


def diff_review_node(state: ReviewState) -> dict:
    """审查单条提交的 diff（并行实例）"""
    short_sha = state.get("current_sha", "")
    commits = state.get("commits", [])
    gitlab_url = state.get("gitlab_url", "")
    project_id = state.get("project_id", "")
    access_token = state.get("access_token", "")

    # 找到完整 SHA
    full_sha = short_sha
    for c in commits:
        if c.get("short_id", c.get("id", "")[:8]) == short_sha:
            full_sha = c.get("id", short_sha)
            break

    # 拉取 diff（GitLab）
    # 拉取 diff（GitLab）
    encoded = requests.utils.quote(project_id, safe="")
    url = f"{gitlab_url}/api/v4/projects/{encoded}/repository/commits/{full_sha}/diff"
    headers = {"Private-Token": access_token}
    try:
        resp = _get_session().get(url, headers=headers, verify=False, timeout=30)
        resp.raise_for_status()
        diffs = resp.json()
    except requests.RequestException as e:
        return {"diff_reviews": [f"### [{short_sha}] diff failed: {e}"]}

    if not diffs:
        return {"diff_reviews": [f"### [{short_sha}] 无文件变更"]}

    # 构建 diff 文本
    parts = []
    for d in diffs[:5]:
        path = d.get("new_path", d.get("old_path", "?"))
        text = d.get("diff", "")
        if len(text) > 2000:
            text = text[:2000] + "\n...(截断)"
        parts.append(f"### {path}\n```diff\n{text}\n```")

    diff_text = "\n\n".join(parts)

    # LLM 审查
    user = f"提交 [{short_sha}] 的代码变更：\n\n{diff_text}"
    try:
        result = _call_llm(_PROMPTS["diff_review"]["system_prompt"], user)
        print(f"[diff_review] {short_sha} 完成")
        return {"diff_reviews": [f"## [{short_sha}] 代码审查\n{result}"]}
    except Exception as e:
        return {"diff_reviews": [f"### [{short_sha}] 审查失败: {e}"]}


# ============================================================
# 节点 5: 汇总
# ============================================================


# ============================================================
# 节点 5: Tag Push 审查
# ============================================================
TAG_REVIEW_SYSTEM = """你是资深软件架构师，审查 Git 标签规范。

## 标签规范
- 正式发布版本必须打标签
- 标签格式：vX.Y.Z（如 v1.2.3）
- 标签需添加描述信息，说明该版本的主要变更
- 标签创建后应推送到远程仓库

审查点：
1. 标签名是否 vX.Y.Z 格式
2. 标签是否有描述（annotated tag）
3. 版本号是否合理（非 0.0.0）

输出简短 Markdown 报告。"""


def tag_review_node(state: ReviewState) -> dict:
    tag_name = state.get("tag_name", "")
    tag_message = state.get("tag_message", "")
    if not tag_name:
        return {"tag_review": ["No tag data"]}
    user = f"Tag: {tag_name}\nDescription: {tag_message or '(none)'}"
    try:
        result = _call_llm(TAG_REVIEW_SYSTEM, user)
        print(f"[tag_review] done: {tag_name}")
        return {"tag_review": [result]}
    except Exception as e:
        return {"tag_review": [f"Tag review failed: {e}"]}
def _post_to_gitlab(event_type, gitlab_url, project_id, token, report, state):
    """Post review report as a comment on GitLab MR or commit."""
    from urllib.parse import quote

    encoded_project = quote(project_id, safe="")
    api_base = f"{gitlab_url}/api/v4/projects/{encoded_project}"
    headers = {"Private-Token": token, "Content-Type": "application/json"}
    note_body = f"LangGraph 审查报告:\n{report}"

    try:
        if event_type == "merge_request":
            iid = state.get("merge_request_iid")
            if not iid:
                print("[aggregate] no MR iid, skip comment")
                return
            url = f"{api_base}/merge_requests/{iid}/notes"
            resp = requests.post(url, headers=headers, json={"body": note_body}, verify=False, timeout=30)
            if resp.status_code == 201:
                print(f"[aggregate] comment posted to MR !{iid}")
            else:
                print(f"[aggregate] MR comment failed: {resp.status_code}")

        elif event_type == "push":
            sha = state.get("commit_sha", "")
            if not sha:
                print("[aggregate] no commit SHA, skip comment")
                return
            url = f"{api_base}/repository/commits/{sha}/comments"
            resp = requests.post(url, headers=headers, json={"note": note_body}, verify=False, timeout=30)
            if resp.status_code == 201:
                print(f"[aggregate] comment posted to commit {sha[:8]}")
            else:
                print(f"[aggregate] commit comment failed: {resp.status_code}")
    except Exception as e:
        print(f"[aggregate] post comment exception: {e}")

def aggregate_node(state: ReviewState) -> dict:
    """汇总分支和提交规范审查结果（不 post GitLab，仅收集）"""
    br = state.get("branch_review", [])
    cr = state.get("commit_msg_review", [])

    parts = ["# 代码规范审查报告\n"]

    if br:
        parts.append("## 一、分支命名规范\n" + br[0] + "\n")

    if cr:
        parts.append("## 二、提交信息规范\n" + cr[0] + "\n")

    if not br and not cr:
        parts.append("> 无审查结果\n")

    parts.append("\n---\n*LangGraph 并行图自动生成*")
    report = "\n".join(parts)
    print(f"[aggregate] 汇总完成（分支审查 + 提交信息审查）")

    # 返回报告，供 final_aggregate 使用
    return {"partial_report": report}


def check_shas_node(state: ReviewState) -> dict:
    """检查是否有可疑提交，决定是否触发 diff_review fan-out"""
    suspicious_shas = state.get("suspicious_shas", [])
    if suspicious_shas:
        print(f"[check_shas] 触发 diff_review，{len(suspicious_shas)} 条可疑提交")
    else:
        print("[check_shas] 无可疑提交，跳过 diff_review")
    return {}


def final_aggregate_node(state: ReviewState) -> dict:
    """最终汇总：收集所有审查结果，一次 post 到 GitLab"""
    br = state.get("branch_review", [])
    cr = state.get("commit_msg_review", [])
    dr = state.get("diff_reviews", [])

    parts = ["# 代码规范审查报告\n"]

    if br:
        parts.append("## 一、分支命名规范\n" + br[0] + "\n")

    if cr:
        parts.append("## 二、提交信息规范\n" + cr[0] + "\n")

    if dr:
        parts.append("## 三、代码 Diff 审查\n" + "\n".join(dr) + "\n")

    if not br and not cr and not dr:
        parts.append("> 无审查结果\n")

    parts.append("\n---\n*LangGraph 并行图自动生成*")
    report = "\n".join(parts)
    print(f"[final_aggregate] 汇总完成（分支 + 提交 + Diff）")

    event_type = state.get("event_type", "")
    gitlab_url = state.get("gitlab_url", "")
    project_id = state.get("project_id", "")
    access_token = state.get("access_token", "")
    platform = state.get("platform", "gitlab")

    if event_type and gitlab_url and project_id and access_token and platform == "gitlab":
        _post_to_gitlab(event_type, gitlab_url, project_id, access_token, report, state)

    return {"final_report": report}
