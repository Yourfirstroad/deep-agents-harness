"""联网搜索工具:基于 Tavily SDK 的同步搜索工具。

与 tools/mcp_tools.py 中 Tavily MCP 工具的分工:
- internet_search(SDK,同步):日常联网检索的主力工具,稳定、快速;
- tavily_search / tavily_extract(MCP,异步):需要更新的结果或抓取指定 URL
  正文时使用。

TAVILY_API_KEY 缺失时工具仍可被注册,调用时返回友好错误,
不阻断 agent 启动(与 MCP 工具的降级策略一致)。
"""

from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

_api_key = os.environ.get("TAVILY_API_KEY")
tavily_client = TavilyClient(api_key=_api_key) if _api_key else None


def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
) -> dict | str:
    """联网搜索。用于查询需求背景知识:行业术语、业务规则、同类产品、
    技术标准等,辅助需求分析与用例设计。

    Args:
        query: 搜索关键词(中英文均可,尽量具体)。
        max_results: 返回结果条数,默认 5。
        topic: 搜索主题,general / news / finance,默认 general。
        include_raw_content: 是否返回网页正文全文(内容较长,仅在需要深读时开启)。

    Returns:
        Tavily 搜索结果字典(含 answer 与 results 列表);
        未配置 TAVILY_API_KEY 时返回错误说明字符串。
    """
    if tavily_client is None:
        return (
            "Error: TAVILY_API_KEY 未配置,无法联网搜索。"
            "请在 .env 中设置 TAVILY_API_KEY 后重启服务。"
        )
    try:
        return tavily_client.search(
            query,
            max_results=max_results,
            include_raw_content=include_raw_content,
            topic=topic,
        )
    except Exception as e:  # 网络错误/限流等,不中断 run,让模型换策略重试
        return f"Error: 联网搜索失败: {e}"
