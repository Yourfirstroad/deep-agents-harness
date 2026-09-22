"""端到端验证:让 harness agent 实际调用联网搜索工具。"""
import asyncio

import agent as agent_module

agent = agent_module.agent


async def main():
    result = await agent.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "先不用生成用例。请帮我联网查一下「医疗器械软件 IEC 62304 标准」"
                        "对软件测试的核心要求,用三五句话总结一下检索到的内容。"
                    ),
                }
            ]
        }
    )

    # 统计本次 run 中实际发生的工具调用
    called = []
    for m in result["messages"]:
        tool_calls = getattr(m, "tool_calls", None) or (
            m.additional_kwargs.get("tool_calls") if hasattr(m, "additional_kwargs") else None
        )
        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                if name:
                    called.append(name)

    print("=== 实际调用的工具 ===")
    for n in called:
        print(" -", n)

    print("\n=== 最终回复(前 800 字) ===")
    print(result["messages"][-1].content[:800])


asyncio.run(main())
