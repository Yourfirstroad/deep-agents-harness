"""MCP 工具加载:在模块导入期同步拉取 MCP 服务器的工具列表。

接入两个 MCP 服务:
- Tavily 远程 MCP:联网检索(tavily_search)与网页正文抽取(tavily_extract);
- AntV mcp-server-chart(本地):generate_bar_chart / generate_line_chart /
  generate_pie_chart 等 25+ 种图表生成工具。

工程要点(与旧 deep agents 项目 research_agent.py 一致):
1. langgraph.json 以模块级方式导入 agent.py,而 MCPAdapter 只有 async 接口,
   因此在独立线程中用 asyncio.run 做同步桥接,避免与外层事件循环冲突。
2. 必须把真正的工具对象列表传给 create_deep_agent;若传入 async 函数,
   deepagents 会把函数本身注册成工具,模型调用时只能拿到"工具定义文本"。
3. 任一 MCP 服务连接失败仅打印警告并降级为空列表,不阻断 agent 启动。

MCP StructuredTool 仅支持异步调用,langgraph dev / astream 链路本身是异步的,
无需额外处理;若在同步脚本中直接 invoke,需改用 ainvoke。
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from langchain.mcp import MCPAdapter

load_dotenv()

# Tavily 远程 MCP:API key 以查询参数形式拼在 URL 里(官方用法),从环境变量读取
TAVILY_MCP_URL = (
    f"https://mcp.tavily.com/mcp/?tavilyApiKey={os.environ['TAVILY_API_KEY']}"
    if os.environ.get("TAVILY_API_KEY")
    else None
)

# AntV mcp-server-chart:本地部署的图表绘制 MCP 服务
# 可用 CHART_MCP_URL 环境变量覆盖(默认 docker 启动在 1122 端口)
CHART_MCP_URL = os.environ.get("CHART_MCP_URL", "http://localhost:1122/mcp")


def _load_mcp_tools(url: str, server_name: str) -> list:
    """同步拉取一个 MCP 服务器的工具列表;失败降级为空列表并告警。"""
    async def _fetch():
        async with MCPAdapter(url) as adapter:
            return await adapter.list_tools()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            tools = pool.submit(asyncio.run, _fetch()).result()
        print(f"[INFO] MCP 服务 {server_name} 已接入,共 {len(tools)} 个工具")
        return tools
    except Exception as e:
        print(f"[WARN] 无法连接 MCP 服务 {server_name} ({url}): {e}")
        return []


def load_all_mcp_tools() -> list:
    """加载全部 MCP 服务的工具列表(在 agent.py 模块级调用一次)。"""
    tools: list = []
    if TAVILY_MCP_URL:
        tools.extend(_load_mcp_tools(TAVILY_MCP_URL, "tavily"))
    else:
        print("[WARN] TAVILY_API_KEY 未配置,跳过 Tavily MCP 接入")
    tools.extend(_load_mcp_tools(CHART_MCP_URL, "mcp-server-chart"))
    return tools
