"""测试用例生成 Agent 入口。

运行:
    langgraph dev   # http://127.0.0.1:2024,Assistant ID = testcase

前端(deep-agents-ui)连接时填:
    Deployment URL: http://127.0.0.1:2024
    Assistant ID:   testcase
"""

from dotenv import load_dotenv

load_dotenv()

import os
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.chat_models import init_chat_model

from middleware.docling_parse import DoclingParseMiddleware
from middleware.file_upload import FileUploadMiddleware
from prompts import ANALYZER_PROMPT, BASE_PROMPT, DESIGNER_PROMPT, REVIEWER_PROMPT
from tools.approval_tool import request_approval
from tools.mcp_tools import load_all_mcp_tools
from tools.search_tool import internet_search

PROJECT_ROOT = Path(__file__).resolve().parent

# 文本主模型:供应商可切换(.env 的 LLM_PROVIDER=deepseek|qwen)。
# 2026-09-23 实测:本机到阿里云 DashScope 链路 TLS 反复失败(运营商路由问题),
# 而 DeepSeek 端点 10/10 稳定;默认用 deepseek-flash(非高峰 ¥1/¥4 每百万 token,
# 与 qwen-flash 同价)。阿里链路恢复后可切回 qwen。
_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").lower()
if _PROVIDER == "qwen":
    model = init_chat_model(
        model=os.getenv("QWEN_MODEL", "qwen-flash"),
        model_provider="openai",
        base_url=os.environ["QWEN_BASE_URL"],
        api_key=os.environ["QWEN_API_KEY"],
        temperature=0.2,  # 用例生成要稳定、格式守规矩
        stream_chunk_timeout=300,  # 长流式偶发断流的容忍
        max_retries=3,
    )
else:
    model = init_chat_model(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        model_provider="deepseek",
        api_key=os.environ["DEEPSEEK_API_KEY"],
        temperature=0.2,
        timeout=300,
        max_retries=3,
    )

# 组合后端:/skills/ 路由到项目磁盘上的 skills/ 目录(技能定义随项目版本管理,
# 由 SkillsMiddleware 启动时发现,read_file 按需加载),
# 其余路径(/uploads、/outputs 等)仍走默认 StateBackend(文件存对话状态,
# 与前端上传注入和预览机制保持兼容)。
backend = CompositeBackend(
    default=StateBackend(),
    routes={
        "/skills/": FilesystemBackend(
            root_dir=PROJECT_ROOT / "skills", virtual_mode=True
        ),
    },
)

# 子代理:需求分析 -> 用例设计 -> 用例评审。
# 与主 agent 共享同一个虚拟文件系统(/uploads、/analysis、/testcases、/review),
# 主 agent 通过 task 工具按 description 委派任务。
# tools=[]:子代理只用内置文件工具(ls/read_file/write_file/edit_file/glob/grep),
# 导出动作(generate_testcase_excel/generate_xmind)统一由主 agent 执行。
# skills=["/skills/"]:子代理挂载同一套技能库,按提示词指引 read_file 对应 SKILL.md。
SUBAGENTS = [
    {
        "name": "requirement-analyzer",
        "description": "需求分析专家:阅读 /uploads/ 下解析后的需求文档,提取功能点与测试点清单,写入 /analysis/<doc>-test-points.md",
        "system_prompt": ANALYZER_PROMPT,
        "model": model,
        "tools": [],
        "skills": ["/skills/"],
    },
    {
        "name": "testcase-designer",
        "description": "用例设计专家:基于 /analysis/<doc>-test-points.md 设计完整测试用例(等价类/边界值/场景法等),写入 /testcases/<doc>-testcases.md;也可根据评审意见修订用例",
        "system_prompt": DESIGNER_PROMPT,
        "model": model,
        "tools": [],
        "skills": ["/skills/"],
    },
    {
        "name": "testcase-reviewer",
        "description": "用例评审专家:审查 /testcases/<doc>-testcases.md 的覆盖率、正确性与可执行性,评审意见写入 /review/<doc>-review.md",
        "system_prompt": REVIEWER_PROMPT,
        "model": model,
        "tools": [],
        "skills": ["/skills/"],
    },
]

# MCP 工具:模块导入期同步拉取(独立线程 asyncio.run 桥接),
# 服务不可用时降级为空列表,不阻断启动。详见 tools/mcp_tools.py。
mcp_tools = load_all_mcp_tools()

agent = create_deep_agent(
    model=model,
    # 联网检索与图表生成只挂在主 agent:检索到的背景知识由主 agent
    # 写进委派 description 或落盘文件,供子代理使用;图表用于交付阶段的统计可视化。
    # request_approval 是人工审核闸门(见 tools/approval_tool.py),由 interrupt_on 拦截。
    tools=[internet_search, request_approval, *mcp_tools],
    # 人工审核:调用 request_approval 时流程暂停,前端弹出 批准/编辑/驳回,
    # 批准后继续,驳回意见回给模型修订(由 FileUploadMiddleware 注入的流程指令
    # 规定在两个节点调用:测试点清单产出后、导出交付前)
    interrupt_on={"request_approval": True},
    # 顺序有意义:DoclingParseMiddleware 先把 /uploads/ 下的原始文档解析成
    # Markdown 写回 files,FileUploadMiddleware 再登记解析结果并注入处理流程
    middleware=[DoclingParseMiddleware(), FileUploadMiddleware()],
    system_prompt=BASE_PROMPT,
    backend=backend,
    subagents=SUBAGENTS,
    # 渐进式加载:启动时注入各技能的 name/description,
    # 模型在需求分析/用例设计/评审时 read_file 对应 SKILL.md 获取完整方法
    skills=["/skills/"],
)
