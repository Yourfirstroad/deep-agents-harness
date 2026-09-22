"""测试用例生成 Agent 入口。

运行:
    langgraph dev   # http://127.0.0.1:2024,Assistant ID = testcase

前端(deep-agents-ui)连接时填:
    Deployment URL: http://127.0.0.1:2024
    Assistant ID:   testcase
"""

from dotenv import load_dotenv

load_dotenv()

from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.chat_models import init_chat_model

from middleware.docling_parse import DoclingParseMiddleware
from middleware.file_upload import FileUploadMiddleware
from prompts import ANALYZER_PROMPT, BASE_PROMPT, DESIGNER_PROMPT, REVIEWER_PROMPT
from tools.mcp_tools import load_all_mcp_tools
from tools.search_tool import internet_search

PROJECT_ROOT = Path(__file__).resolve().parent

model = init_chat_model(
    model="deepseek-chat",
    model_provider="deepseek",
    temperature=0.2,  # 用例生成要稳定、格式守规矩
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
    tools=[internet_search, *mcp_tools],
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
