# AI 代码审查 Agent — LangGraph 版（GitLab）

> 基于 LangGraph 的 Git 分支命名与提交规范审查 Agent，为 GitLab 提供自动化代码审查服务。

## 项目来源

Fork 并重构自 [ai-codereview-gitlab](https://github.com/example/ai-codereview-gitlab)，核心改造如下：

| 维度 | 原项目 | LangGraph 版 |
|------|--------|-------------|
| 架构模式 | 线性 Pipeline：A → B → C | 声明式 DAG：节点+边并行执行 |
| 控制流 | 函数调用链硬编码 | StateGraph 显式声明 |
| 并行审查 | 单线程逐条审查 | Send 动态扇出，N 提交 = N 并行 |
| 状态管理 | 函数参数+局部变量 | 集中式 TypedDict + Annotated[list, add] |
| 提示词 | 硬编码在代码中 | 统一在 langgraph_prompts.yml，改了就生效 |

## 架构

`
GitLab Webhook (push / merge_request)
        │
        ▼
   Flask API (:5001)
        │
   ┌────┴────┐
   │ 原系统    │  LangGraph 并行审查引擎
   │ MR/Push  │  ┌────────────────────┐
   │ 常规审查  │  │ fetch → branch ∥ commit_msg
   │          │  │              │
   │          │  │         diff × N (并行)
   │          │  │              │
   │          │  │         aggregate → GitLab 评论
   │          │  └────────────────────┘
   └────┬────┘
        ▼
   GitLab API（贴评论）
`

## 快速开始

### 1. 配置

`ash
cp conf/.env.dist conf/.env
# 编辑 conf/.env，填入你的 DeepSeek/OpenAI API Key 和 GitLab Access Token
`

### 2. 安装依赖

`ash
pip install -r requirements.txt
`

### 3. 启动服务

`ash
python api.py
`

### 4. 在 GitLab 配置 Webhook

在 GitLab 项目 Settings → Webhooks 中添加：
- URL: http://your-host:5001/review/webhook（原系统）或 http://your-host:5001/review/langgraph-webhook（LangGraph 并行审查）
- Trigger: Push events, Merge Request events

## LangGraph 审查流程

| 节点 | 功能 |
|------|------|
| etch_data | 从 GitLab API 拉取分支和提交列表 |
| ranch_review | LLM 审查分支命名是否符合规范（feature/YYYYMMDD-名称 等） |
| commit_msg_review | LLM 审查提交信息格式（Conventional Commits） |
| diff_review × N | **全部提交并行**审查代码变更（安全、逻辑、规范） |
| ggregate | 汇总报告并贴到 GitLab Commit/MR 评论 |

### 并行能力

diff_review 通过 LangGraph Send 动态扇出，N 条提交 = N 个并行审查节点，不串行等待。

## 目录结构

`
├── api.py                  # Flask 入口
├── biz/
│   ├── api/routes/         # Webhook 路由
│   ├── langgraph_review/   # ★ LangGraph 审查引擎（原创）
│   │   ├── graph.py        # 图定义（7节点 DAG）
│   │   ├── nodes.py        # 节点实现
│   │   └── state.py        # 状态定义
│   ├── llm/                # LLM 客户端（DeepSeek/OpenAI）
│   ├── platforms/gitlab/   # GitLab API 适配
│   ├── queue/              # 异步队列
│   ├── service/            # 审查服务
│   └── utils/              # 工具函数
├── conf/
│   ├── .env.dist           # 配置模板
│   └── langgraph_prompts.yml  # ★ 审查提示词（原创）
└── requirements.txt
`

## 提示词配置

所有审查的 LLM system prompt 集中在 conf/langgraph_prompts.yml，修改后重启服务即可生效，无需改代码。

规范依据：中国华能集团有限公司 GIT分支管理指南（2021年11月）

## License

MIT