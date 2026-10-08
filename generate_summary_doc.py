"""生成《测试用例生成 Agent 项目全景》Word 文档。

汇总前面讨论的技术栈、功能架构、子代理设计、Skills 系统、MCP 集成、
lint 工具规则、优化方向等内容,排版成结构化的 .docx 文件。
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor, Cm, Inches


OUTPUT_PATH = Path(__file__).resolve().parent / "outputs" / "项目全景说明.docx"


# ---------------------------------------------------------------------------
# 通用样式工具
# ---------------------------------------------------------------------------
def set_cn_font(run, name: str = "Microsoft YaHei", size: int = None,
                bold: bool = None, color: RGBColor = None) -> None:
    """给 Run 设置中文字体(Word 默认对中文不生效,需要 rFonts/eastAsia)。"""
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)


def add_h1(doc: Document, text: str) -> None:
    """一级标题(章)。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(18)
    p.paragraph_format.space_after = Pt(8)
    run = p.add_run(text)
    set_cn_font(run, size=18, bold=True, color=RGBColor(0x1F, 0x36, 0x64))


def add_h2(doc: Document, text: str) -> None:
    """二级标题(节)。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(6)
    run = p.add_run(text)
    set_cn_font(run, size=14, bold=True, color=RGBColor(0x2E, 0x5C, 0x8A))


def add_h3(doc: Document, text: str) -> None:
    """三级标题(小节)。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text)
    set_cn_font(run, size=12, bold=True, color=RGBColor(0x4A, 0x4A, 0x4A))


def add_para(doc: Document, text: str, size: int = 11, indent: bool = True) -> None:
    """正文段落。"""
    p = doc.add_paragraph()
    if indent:
        p.paragraph_format.first_line_indent = Cm(0.74)  # 2 字符
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.4
    run = p.add_run(text)
    set_cn_font(run, size=size)


def add_bullet(doc: Document, text: str, level: int = 0, size: int = 11) -> None:
    """项目符号条目。"""
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.left_indent = Cm(0.74 + 0.74 * level)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.35
    run = p.add_run(text)
    set_cn_font(run, size=size)


def add_code_block(doc: Document, code: str) -> None:
    """代码块(浅灰底,等宽字体)。"""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.5)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(code)
    run.font.name = "Consolas"
    run.font.size = Pt(9.5)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), "Consolas")
    rfonts.set(qn("w:hAnsi"), "Consolas")
    rfonts.set(qn("w:eastAsia"), "Consolas")
    # 浅灰底
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F4F4F4")
    pPr = p._element.get_or_add_pPr()
    pPr.append(shd)


def shade_cell(cell, color_hex: str) -> None:
    """给单元格设置背景色。"""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color_hex)
    tcPr.append(shd)


def add_table(doc: Document, headers: list[str], rows: list[list[str]],
              col_widths: list[float] | None = None) -> None:
    """添加一个表格,带表头浅蓝底。"""
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    table.autofit = False
    if col_widths:
        for i, w in enumerate(col_widths):
            for cell in table.columns[i].cells:
                cell.width = Cm(w)

    # 表头
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        shade_cell(cell, "2E5C8A")
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(h)
        set_cn_font(run, size=10.5, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))

    # 数据行
    for r_idx, row in enumerate(rows, start=1):
        for c_idx, value in enumerate(row):
            cell = table.rows[r_idx].cells[c_idx]
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            if r_idx % 2 == 0:
                shade_cell(cell, "F4F7FA")
            p = cell.paragraphs[0]
            run = p.add_run(value)
            set_cn_font(run, size=10)


def add_quote(doc: Document, text: str) -> None:
    """引用段落(浅灰斜体)。"""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.74)
    p.paragraph_format.right_indent = Cm(0.74)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run("「" + text + "」")
    set_cn_font(run, size=11, color=RGBColor(0x55, 0x55, 0x55))


def add_divider(doc: Document) -> None:
    """分隔线段落。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    pPr = p._element.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "BFBFBF")
    pBdr.append(bottom)
    pPr.append(pBdr)


# ---------------------------------------------------------------------------
# 文档主体
# ---------------------------------------------------------------------------
def build() -> None:
    doc = Document()

    # 全局默认样式
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(11)

    # 页面边距
    section = doc.sections[0]
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.2)

    # ===== 封面 =====
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(120)
    title.paragraph_format.space_after = Pt(12)
    run = title.add_run("测试用例生成 Agent 项目全景")
    set_cn_font(run, size=28, bold=True, color=RGBColor(0x1F, 0x36, 0x64))

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.paragraph_format.space_after = Pt(40)
    run = sub.add_run("deep-agents-harness · 技术栈与架构梳理")
    set_cn_font(run, size=14, color=RGBColor(0x55, 0x55, 0x55))

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.paragraph_format.space_after = Pt(4)
    run = meta.add_run("基于 deepagents + LangGraph 的多智能体测试用例生成系统")
    set_cn_font(run, size=12, color=RGBColor(0x33, 0x33, 0x33))

    info = doc.add_paragraph()
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    info.paragraph_format.space_before = Pt(40)
    info.paragraph_format.space_after = Pt(4)
    run = info.add_run("文档定位:技术栈总览 · 架构思路 · 子代理设计 · Skills 系统")
    set_cn_font(run, size=11, color=RGBColor(0x77, 0x77, 0x77))

    info2 = doc.add_paragraph()
    info2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    info2.paragraph_format.space_after = Pt(4)
    run = info2.add_run("内容范围:MCP 集成 · Lint 规则详解 · 优化方向建议")
    set_cn_font(run, size=11, color=RGBColor(0x77, 0x77, 0x77))

    doc.add_page_break()

    # ===== 一、项目概述 =====
    add_h1(doc, "一、项目概述")

    add_para(doc,
        "deep-agents-harness 是一个基于 deepagents + LangGraph 的多智能体协作系统,"
        "目标是根据用户上传的需求文档(PDF / Word / 图片等)自动产出 Markdown、Excel、"
        "XMind 三种形式的测试用例交付物。该项目本身是纯后端 Agent 服务,前端 UI 作为"
        "独立仓库(deep-agents-ui)与之对接。")

    add_h2(doc, "1.1 核心能力")
    add_bullet(doc, "需求文档自动解析:支持 PDF、DOCX、PPTX、XLSX、PNG、JPG、HTML 等格式")
    add_bullet(doc, "需求分析与测试点提取:基于深度优先 + 模块化的方法论")
    add_bullet(doc, "测试用例设计:覆盖等价类、边界值、判定表、状态迁移、场景法、正交、错误推测等 7 大技术")
    add_bullet(doc, "用例评审与追溯:覆盖度 / 可执行性 / 数据具体化 / P0 占比等量化指标")
    add_bullet(doc, "多形态交付:Markdown 终稿 + 带样式 Excel + XMind + markmap 在线预览")
    add_bullet(doc, "人工审阅闸门:关键节点支持批准 / 编辑 / 驳回的 HITL 流程")

    add_h2(doc, "1.2 启动方式")
    add_para(doc, "项目通过 langgraph dev 启动本地开发服务器(默认端口 2024):")
    add_code_block(doc,
        "# 安装依赖\n"
        "pip install -r requirements.txt\n\n"
        "# 启动(LangGraph API + 自定义 HTTP 路由同时暴露)\n"
        "langgraph dev   # Assistant ID = testcase")

    # ===== 二、技术栈总览 =====
    add_h1(doc, "二、技术栈总览")

    add_h2(doc, "2.1 后端核心")
    add_table(doc,
        ["组件", "选型", "作用"],
        [
            ["Agent 框架", "deepagents(基于 LangGraph)", "主 Agent + 4 个子代理编排、虚拟文件系统、子代理委派"],
            ["图执行", "langgraph-cli[inmem]", "langgraph dev 本地开发服务器(端口 2024)"],
            ["LLM", "可切换 DeepSeek(默认 deepseek-flash)/ Qwen(qwen-flash)", ".env 的 LLM_PROVIDER 切换"],
            ["LangChain", "langchain-openai", "init_chat_model 抽象供应商"],
            ["文档解析", "docling-serve(本地 Docker)", "PDF/Word/PPT/图片 → Markdown + 图片多模态描述"],
            ["表格生成", "openpyxl", "带样式 xlsx(优先级配色、表头、列宽、冻结)"],
            ["联网检索", "tavily-python", "internet_search 工具(SDK 同步)"],
            ["MCP", "fastmcp + MCPAdapter", "Tavily MCP + AntV mcp-server-chart"],
            ["HTTP 路由", "starlette", "自定义 /api/upload、/api/download 路由"],
            ["Tracing", "LangSmith", "全链路追踪(LANGSMITH_TRACING=true)"],
        ],
        col_widths=[3.0, 5.5, 8.0])

    add_h2(doc, "2.2 文档解析链路(路线 A)")
    add_para(doc, "docling-serve 标准管线 + 图片描述:")
    add_bullet(doc, "标准版面分析 + OCR,处理 PDF、Word、PPT、图片、HTML")
    add_bullet(doc, "图片描述首选 DashScope 千问 VL(qwen-vl-flash),回退自定义 VLM 端点")
    add_bullet(doc, "解析后自动调用 tools/denoise.py 清洗:页眉页脚、重复水印、孤立页码、空图片占位")
    add_bullet(doc, "降噪产物:outputs/<doc>.raw.md(原始未清洗)+ outputs/<doc>.denoise.md(审计明细)")

    add_h2(doc, "2.3 前端契约(前后端分离)")
    add_para(doc, "前端只需 2 个自定义 HTTP 接口,通过 langgraph.json 的 http.app 挂载合并到 LangGraph API 同端口(2024):")
    add_table(doc,
        ["接口", "方法", "用途"],
        [
            ["/api/upload", "POST(multipart)", "前端上传文件;字段 file + thread_id;落盘 uploads/ 并以 FileData 注入 LangGraph 线程状态"],
            ["/api/download", "GET", "从 outputs/ 下载交付物(Excel / XMind / Markdown / HTML),与 Agent 回复中的下载链接对应"],
        ],
        col_widths=[4.0, 3.5, 9.0])

    add_h2(doc, "2.4 配套目录结构")
    add_table(doc,
        ["目录", "用途"],
        [
            ["uploads/", "原始上传文件留档"],
            ["outputs/", "交付物(xlsx / xmind / html / md / 降噪审计)"],
            ["middleware/", "DoclingParseMiddleware(自动解析)+ FileUploadMiddleware(登记 + 流程注入)"],
            ["tools/", "docling / excel / xmind / lint / denoise / approval / scope_selection / search / mcp"],
            ["skills/", "按 deepagents Skills 规范的渐进式技能库(3 个技能)"],
            ["tests/", "pytest 单元测试 + e2e"],
            [".langgraph_api/", "LangGraph API 缓存"],
        ],
        col_widths=[4.5, 12.0])

    doc.add_page_break()

    # ===== 三、核心架构 =====
    add_h1(doc, "三、核心架构思路")

    add_h2(doc, "3.1 主 Agent + 4 个子代理的多层编排")
    add_para(doc, "项目采用主 Agent + 4 个子代理的多层编排模式:")
    add_code_block(doc,
        "用户上传需求文档\n"
        "      ↓\n"
        "  主 Agent(编排 + 用户交互 + 导出)\n"
        "      ├─ requirement-rough-scanner   粗扫(广度优先列候选模块)\n"
        "      ├─ requirement-analyzer         详细分析(只做 scope 内)\n"
        "      ├─ testcase-designer            用例设计(等价类/边界值/场景法…)\n"
        "      └─ testcase-reviewer            评审(覆盖率/正确性/可执行性)")

    add_bullet(doc, "共享虚拟文件系统:/uploads/、/analysis/、/testcases/、/review/,子代理之间通过文件传递")
    add_bullet(doc, "子代理 tools=[]:只用 ls/read_file/write_file/edit_file/glob/grep,导出动作由主 agent 统一执行")
    add_bullet(doc, "技能库共享:skills=['/skills/'] 全部子代理挂载同一套")

    add_h2(doc, "3.2 CompositeBackend 组合存储")
    add_code_block(doc,
        "backend = CompositeBackend(\n"
        "    default=StateBackend(),                 # /uploads/、/outputs/ 走对话状态\n"
        "    routes={\"/skills/\": FilesystemBackend(   # /skills/ 路由到磁盘 skills/\n"
        "        root_dir=PROJECT_ROOT / \"skills\",\n"
        "        virtual_mode=True,\n"
        "    )},\n"
        ")")

    add_para(doc, "技能定义随代码版本走,避免污染对话状态。")

    add_h2(doc, "3.3 Middleware 链")
    add_table(doc,
        ["中间件", "职责"],
        [
            ["DoclingParseMiddleware",
             "before_model 扫描 /uploads/ 新文件 → 调 docling 解析 → 降噪 → 写入 /uploads/<doc>.md;parsed_uploads 状态去重;失败注 SystemMessage 自愈"],
            ["FileUploadMiddleware",
             "before_model 登记新文档到 uploads 状态;wrap_model_call 动态注入文档清单 + 阶段动作;wrap_tool_call 异常兜底;自身注册 3 个工具(generate_testcase_excel / generate_xmind / lint_testcases)"],
        ],
        col_widths=[5.0, 11.5])

    add_h2(doc, "3.4 状态机推进")
    add_para(doc,
        "主 Agent 根据产物文件存在性判断每份文档处于哪个阶段,FileUploadMiddleware 动态注入"
        "「文档清单 + 当前阶段 + 下一步动作」。多份文档逐份独立推进,互不干扰。")
    add_table(doc,
        ["阶段", "判定", "下一步"],
        [
            ["A 粗扫", "/analysis/<doc>-rough-scan.md 不存在", "读文档 + 委派 rough-scanner"],
            ["B scope 确认", "rough-scan 存在,test-points 不存在", "呈现候选模块给用户,明确询问 scope"],
            ["C 深度分析", "test-points 存在,testcases 不存在", "委派 analyzer(scope 内)"],
            ["D 用例设计 + lint", "testcases 存在,review 不存在", "designer 产出 + lint 通过 → 评审"],
            ["E 评审 + 导出", "review 存在", "request_approval → 导出 Excel + XMind + HTML"],
        ],
        col_widths=[3.0, 6.0, 7.5])

    add_h2(doc, "3.5 关键设计模式:Human-in-the-Loop")
    add_para(doc, "通过 interrupt_on 配置拦截两个工具,模型调用即暂停:")
    add_table(doc,
        ["工具", "用途", "用户操作"],
        [
            ["request_approval", "硬节点闸门(测试点清单、用例终稿)", "批准 / 编辑后批准 / 驳回(驳回意见回给模型继续修订)"],
            ["request_scope_selection", "scope 多选弹窗(粗扫后用户回复含糊时触发)", "多选 / 驳回"],
        ],
        col_widths=[5.0, 6.5, 5.0])

    add_para(doc, "被驳回 → 驳回意见回给模型 → 自动重新委派子代理修订 → 重新提交,最多 2 轮。")

    add_h2(doc, "3.6 Markdown 用例表契约")
    add_para(doc, "tools/excel_tool.py 强制六列契约,贯穿全栈:")
    add_code_block(doc,
        "| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 | 优先级 |\n"
        "|----------|----------|----------|----------|----------|--------|\n"
        "| TC001    | ...      | ...      | ...      | ...      | P0     |")

    add_para(doc,
        "lint_tool.py 做机械质量门禁:表头校验、编号连续性、模糊词检测、优先级占比。"
        "Excel 输出带样式:优先级 P0/P1/P2/P3 配色、表头蓝底白字、加边框、列宽、对齐、自动换行、冻结首行。")

    doc.add_page_break()

    # ===== 四、实现的功能 =====
    add_h1(doc, "四、实现的功能")

    add_h2(doc, "4.1 上传与解析")
    add_para(doc, "支持格式:")
    add_bullet(doc, "PDF、DOC/DOCX、PPT/PPTX、XLSX、HTML、PNG/JPG/TIFF/BMP/WEBP,以及 md/txt 纯文本")
    add_bullet(doc, "全自动流程:上传 → 落盘 → 调 docling → 降噪 → 写入对话状态 → 下次响应时模型自动读到 <doc>.md")
    add_bullet(doc, "二进制以 base64 注入 files 状态,文本走 utf-8(FileData 统一契约)")
    add_bullet(doc, "解析失败不中断:以 SystemMessage 告知模型,让模型提示用户重试")

    add_h2(doc, "4.2 需求分析三件套")
    add_bullet(doc, "粗扫:广度优先列候选模块,写入 /analysis/<doc>-rough-scan.md")
    add_bullet(doc, "scope 确认:用户明确 → 直接采用;说「全部」→ 反问确认;含糊 → 弹多选面板")
    add_bullet(doc, "详细分析:按选定 scope 提取功能点 + 测试点 + 业务规则 + 数据约束 + 非功能维度,输出含「未覆盖模块」节")

    add_h2(doc, "4.3 测试用例生成")
    add_bullet(doc, "多种用例设计方法:等价类、边界值、判定表、状态迁移、场景法、正交、错误推测")
    add_bullet(doc, "6 列 Markdown 表格:编号、名称、前置、步骤、预期、优先级(P0–P3)")
    add_bullet(doc, "scope 内模块自动设计,scope 外在「未覆盖模块」段注明")
    add_bullet(doc, "规模三档:smoke(≤30)/ standard / full,通过 .env 的 TESTCASE_SCOPE 控制")

    add_h2(doc, "4.4 质量门禁")
    add_bullet(doc, "lint_testcases:表头 / 编号连续性 / 模糊词 / 优先级占比,FAIL 时让 designer 修订至 PASS")
    add_bullet(doc, "用例评审:覆盖率、正确性、可执行性,「需修订」则回 designer,最多 2 轮")

    add_h2(doc, "4.5 人工审阅")
    add_bullet(doc, "两次硬节点 approval(测试点清单、用例终稿)")
    add_bullet(doc, "一次 scope 多选(粗扫后用户含糊时)")

    add_h2(doc, "4.6 多形态交付物")
    add_para(doc, "主 Agent 评审通过后导出 4 类产物到 outputs/:")
    add_table(doc,
        ["产物文件", "生成工具"],
        [
            ["<doc>-testcases.md", "generate_testcase_excel(同时写 md)"],
            ["<doc>-testcases.xlsx", "generate_testcase_excel(openpyxl)"],
            ["<doc>-mindmap.xmind", "generate_xmind"],
            ["<doc>-mindmap.html", "generate_xmind(markmap 在线预览)"],
        ],
        col_widths=[6.0, 10.5])

    add_para(doc,
        "最后输出「交付物下载」小节(绝对 URL,前端可直接点击下载),"
        "并通过 Command(update={'files': ...}) 同步写入对话 state,前端文件面板可见。")

    add_h2(doc, "4.7 其他能力")
    add_bullet(doc, "internet_search(Tavily):联网检索背景知识")
    add_bullet(doc, "MCP 工具:启动时通过 asyncio.run 同步拉取,服务不可用时降级为空列表")
    add_bullet(doc, "run_watchdog.py:进程监控脚本")

    doc.add_page_break()

    # ===== 五、子代理设计详解 =====
    add_h1(doc, "五、子代理(Subagent)设计思路")

    add_h2(doc, "5.1 编排模型:主 Agent + 4 个子代理")
    add_para(doc,
        "四个子代理对应完整的「瀑布式流水线」,外加粗扫先做。注意子代理 tools=[] 是关键设计决策:"
        "所有破坏性写操作(导出/批准/落盘)都收敛到主 agent,避免子代理之间互相污染。")

    add_h2(doc, "5.2 三个核心设计取舍")
    add_h3(doc, "取舍 1:共享文件系统 vs 子代理私有上下文")
    add_para(doc,
        "故意把四份产物的目录(/analysis/、/testcases/、/review/、/outputs/)放到主 agent 与所有"
        "子代理共享的同一虚拟文件系统里。原因不是技术做不到隔离,而是这条流水线本质就是阶段化交付"
        "——子代理之间通过文件传递而非消息传递,避免每轮把上一阶段的几万 token 重新塞进 prompt。")
    add_para(doc, "代价:子代理「误写」会污染共享空间 —— 所以 tools=[]。")

    add_h3(doc, "取舍 2:子代理 tools=[]:只允许读/写虚拟文件")
    add_para(doc, "三个好处:")
    add_bullet(doc, "产物命名集中管理(<doc_name> 由主 agent 统一拼)")
    add_bullet(doc, "导出时机由主 agent 控制(评审通过 + 用户批准双重门禁)")
    add_bullet(doc, "失败自愈更可控(主 agent Command 写入 /outputs/,子代理挂了也不丢产物)")

    add_h3(doc, "取舍 3:task description 即契约")
    add_para(doc,
        "主 agent 通过 LangGraph task 工具委派,但关键约束写在 description 里:"
        "「用户选定的 scope:<模块列表或'全部'>」。"
        "子代理拿到的是结构化 contract,而不是自然语言的含糊指令。")

    add_h2(doc, "5.3 粗扫与详细分析的「二段式拆分」")
    add_para(doc,
        "很多智能体失败在一开始就让模型去做完整的需求分析——模型为了「全面」会忽略用户真正"
        "关心的范围。本项目把分析拆成两段:")
    add_bullet(doc, "rough-scan:广度优先,只列模块,禁止展开 F-points/TP")
    add_bullet(doc, "详细分析:深度优先,只做 scope 内模块")
    add_para(doc,
        "让「选择测什么」这件事显式可对话——粗扫产物是给用户做决策用的,详细分析按用户决策走。"
        "同时给「突然增删 scope」留了缓冲。")

    add_h2(doc, "5.4 评审闭环设计")
    add_quote(doc,
        "评审者只审不改 —— 发现问题写入评审报告并给出可执行的修订意见,"
        "修订动作由用例设计者完成;修订后需对修订点重新评审,形成闭环。")
    add_para(doc, "为什么不让 reviewer 直接改?会让 reviewer 变成 reviewer + designer,导致:")
    add_bullet(doc, "reviewer 失去独立视角(评估者同时是改造者)")
    add_bullet(doc, "改完之后没人再评一遍,形成「自我审批」")
    add_bullet(doc, "修改过程绕过了主 agent 的 approval 闸门")

    add_h2(doc, "5.5 提示词模板化")
    add_para(doc,
        "四个 *_PROMPT 字符串按「角色 / 输入 / 输出契约 / 关键约束 / 工作流步骤 / 反例」模板写死。"
        "这是把质量门禁前置到提示词层的典型做法:与其等模型产完用 lint 工具打回,"
        "不如在提示词里就把容易踩的坑堵住。")

    doc.add_page_break()

    # ===== 六、Skills 系统 =====
    add_h1(doc, "六、Skills 系统(渐进式技能库)")

    add_h2(doc, "6.1 什么是 deepagents Skills 规范")
    add_para(doc,
        "按 deepagents Skills 渐进式加载:启动时只注入各技能的 name/description,"
        "模型在对应阶段 read_file 加载完整方法。")
    add_code_block(doc,
        "backend = CompositeBackend(\n"
        "    default=StateBackend(),\n"
        "    routes={\"/skills/\": FilesystemBackend(\n"
        "        root_dir=PROJECT_ROOT / \"skills\",\n"
        "        virtual_mode=True,\n"
        "    )},\n"
        ")\n"
        "agent = create_deep_agent(..., skills=[\"/skills/\"])")

    add_h2(doc, "6.2 三个技能的角色分工")
    add_table(doc,
        ["技能", "能力定位", "强制触发点"],
        [
            ["requirement-analysis",
             "需求分析 + 测试点提取(含 SKILL.rough-scan.md 处理粗扫分支)",
             "粗扫与深度分析阶段,主 agent 委派时强制要求遵守"],
            ["test-design",
             "7 大用例设计技术 + 选型矩阵 + 测试数据 + 优先级",
             "designer 子代理产用例前自读"],
            ["testcase-review",
             "5 维量化评估(覆盖度 / 异常占比 / 步骤-预期对应率 / 数据具体化率 / P0 占比)",
             "reviewer 子代理必读"],
        ],
        col_widths=[4.5, 7.0, 5.0])

    add_para(doc,
        "requirement-analysis 比较特别——它内部有两份 SKILL.md 与 SKILL.rough-scan.md,"
        "主 agent 通过 task description 指定加载哪一份。这是渐进式加载的高级用法。")

    add_h2(doc, "6.3 与「放在 system prompt 硬编码」的对比")
    add_table(doc,
        ["对比维度", "传统做法(塞 system prompt)", "本项目做法(Skills 系统)"],
        [
            ["上下文成本", "主 agent 上下文被撑大(每条 token 都要付钱)", "主 agent 只看到 name + description,token 成本几乎为零"],
            ["子代理可用性", "子代理拿不到这些方法论", "子代理自己 read_file 加载,保证执行一致性"],
            ["变更成本", "改一处要全文重新发版", "改 SKILL.md → 重启生效,不需要改 prompts.py"],
        ],
        col_widths=[3.0, 6.5, 7.0])

    add_h2(doc, "6.4 SKILL 红线与 lint 的呼应(软约束 + 硬约束)")
    add_table(doc,
        ["技能 SKILL 的红线", "lint_testcases 的对应规则"],
        [
            ["test-design:禁止「正常」「正确」类词", "_VAGUE_BANNED + _VAGUE_CORRECT_RE"],
            ["test-design:测试数据禁止抽象", "_ABSTRACT_RE 模式匹配"],
            ["test-design:一条用例只验证一个测试点", "_check_duplicates 检测名称 / 步骤+预期重复"],
            ["test-design:优先级定级矩阵(资损/安全 P0)", "_P0_RATIO_RANGE 占比告警"],
            ["testcase-review:异常场景占比 ≥30%", "_ABNORMAL_RATIO_MIN = 0.30"],
            ["testcase-review:测试点覆盖率 100%", "_check_traceability 双向核对"],
            ["test-design:追溯矩阵必须存在", "_extract_trace_matrix 检测"],
        ],
        col_widths=[7.0, 9.5])

    add_para(doc,
        "Skills 是「软约束」(让模型知道怎么写),lint 是「硬约束」(让模型不可能写出违规)。"
        "两道防线互补,对抗模型幻觉。")

    add_h2(doc, "6.5 技能的版本管理")
    add_para(doc,
        "FilesystemBackend(root_dir=PROJECT_ROOT / 'skills', virtual_mode=True) 让技能定义"
        "和代码一起走版本控制。新增技能只需建目录 + 写 SKILL.md,重启 langgraph dev 即被自动发现。")

    doc.add_page_break()

    # ===== 七、MCP 集成 =====
    add_h1(doc, "七、MCP 集成")

    add_h2(doc, "7.1 接入了哪些 MCP 服务器")
    add_table(doc,
        ["服务", "类型", "提供的能力"],
        [
            ["Tavily MCP", "远程 SaaS(API key 拼到 URL 查询参数)", "tavily_search、tavily_extract"],
            ["AntV mcp-server-chart", "本地 Docker(端口 1122)", "generate_bar_chart、generate_line_chart、generate_pie_chart 等 25+ 种图表"],
        ],
        col_widths=[5.0, 5.5, 6.0])

    add_h2(doc, "7.2 三个工程取舍")
    add_h3(doc, "取舍 1:模块导入期同步拉取 + asyncio.run 桥接")
    add_code_block(doc,
        "with ThreadPoolExecutor(max_workers=1) as pool:\n"
        "    tools = pool.submit(asyncio.run, _fetch()).result()")
    add_para(doc,
        "langgraph.json 里 agent.py 是模块级导入,而 MCPAdapter.list_tools() 是 async 方法。"
        "直接 await 会与外层事件循环冲突甚至死锁。所以单线程池里跑 asyncio.run(...),"
        "把整个异步链路封进独立线程。")

    add_h3(doc, "取舍 2:失败降级,绝不阻断 agent 启动")
    add_code_block(doc,
        "except Exception as e:\n"
        "    print(f\"[WARN] 无法连接 MCP 服务 {server_name} ({url}): {e}\")\n"
        "    return []")
    add_para(doc, "MCP 服务器宕机时不应让整个 agent 启不起来。降级空列表后,主 agent 仍可跑。")

    add_h3(doc, "取舍 3:SDK 主力 + MCP 补充")
    add_para(doc, "internet_search(SDK 同步)与 tavily_search(MCP 异步)分工:")
    add_bullet(doc, "internet_search:日常联网检索主力,稳定、快速")
    add_bullet(doc, "tavily_search / tavily_extract:需要更新的结果或抓取指定 URL 正文时使用")

    add_h2(doc, "7.3 MCP 在主 agent 上的挂载")
    add_para(doc, "只在主 agent 挂载,子代理不持有 MCP 工具:")
    add_code_block(doc,
        "tools=[\n"
        "    internet_search,\n"
        "    request_approval,\n"
        "    request_scope_selection,\n"
        "    *mcp_tools,  # Tavily MCP + AntV chart MCP\n"
        "]")
    add_para(doc, "刻意设计:子代理不需要联网(MCP 检索到的背景知识由主 agent 写进委派 description 或落盘文件)。")

    add_h2(doc, "7.4 与 deepagents 的接入点")
    add_quote(doc,
        "必须把真正的工具对象列表传给 create_deep_agent;若传入 async 函数,"
        "deepagents 会把函数本身注册成工具,模型调用时只能拿到「工具定义文本」。")
    add_para(doc,
        "MCPAdapter 返回的是 async 函数列表,而 deepagents 注册要求 sync 工具对象。"
        "这里把 async 函数先 list_tools() 拉回来,让 framework 自己处理 sync/async 包装即可。")

    doc.add_page_break()

    # ===== 八、Lint 工具详解 =====
    add_h1(doc, "八、Lint 工具的具体规则")

    add_h2(doc, "8.1 检查分级")
    add_para(doc, "tools/lint_tool.py 的三大级别:")
    add_table(doc,
        ["级别", "含义", "影响"],
        [
            ["阻断", "不修复不能进入下一阶段(如整模块零用例、结构破坏)", "整体 FAIL"],
            ["严重", "必须打回修订(模糊预期、P0 占比超 30%、异常占比不足)", "整体 FAIL"],
            ["一般", "建议优化(孤儿用例、轻微占比)", "判定通过,仅报告"],
        ],
        col_widths=[2.5, 9.0, 5.0])

    add_para(doc, "verdict = 'PASS' if blocking == 0 else 'FAIL',阻断/严重任一出现即整体 FAIL。")

    add_h2(doc, "8.2 八道检查详解")
    add_table(doc,
        ["#", "函数", "检查维度", "命中级别"],
        [
            ["1", "_check_structure",
             "列数=6;编号格式 TC-<模块>-<三位序号>;编号唯一;每模块连续无跳号;优先级 ∈ {P0,P1,P2,P3}",
             "阻断/严重"],
            ["2", "_check_expected",
             "预期结果禁词(「正常」「无误」等 7 个);禁「正确」(但放行「不正确」「正确率」);整条预期 ≤10 字且以「成功/失败/通过」结尾视为过短",
             "严重"],
            ["3", "_check_data",
             "测试数据禁抽象:「一个合法手机号」「错误密码」等",
             "严重"],
            ["4", "_check_duplicates",
             "用例名重复;步骤+预期完全相同",
             "严重"],
            ["5", "_check_stats",
             "异常场景占比 ≥30%;P0 占比 ≤30%;smoke 模式总量 ≤30 条",
             "严重/一般"],
            ["6", "_check_traceability",
             "追溯矩阵存在性;空覆盖行区分「声明裁剪(—/不覆盖)」与「漏覆盖」;模块零用例时允许整模块声明裁剪;测试点→矩阵→用例的双向一致;引用不存在 / 孤儿用例",
             "阻断/严重/一般"],
            ["7", "_extract_trace_matrix",
             "追溯矩阵解析:表头第一列必为「测试点编号」,含「覆盖用例编号」列;TC 引用正则",
             "工具方法"],
            ["8", "_extract_case_table",
             "唯一表头识别(防止把附录的判定表/状态图当成用例表)",
             "阻断(找不到表头时)"],
        ],
        col_widths=[0.8, 4.0, 8.0, 3.5])

    add_h2(doc, "8.3 lint 与手工评审的分工边界")
    add_quote(doc,
        "评审子代理负责语义判断(覆盖合理性、技术选型、业务正确性),"
        "本工具负责不需要智能就能判定的硬规则。")
    add_table(doc,
        ["维度", "lint 负责", "reviewer 负责"],
        [
            ["结构 / 编号", "✓", ""],
            ["禁词 / 抽象数据", "✓", ""],
            ["占比 / 统计", "✓", ""],
            ["追溯矩阵存在性", "✓", ""],
            ["步骤-预期是否对应", "", "✓"],
            ["用例名是否达意", "", "✓"],
            ["技术选型是否合理", "", "✓"],
            ["业务正确性", "", "✓"],
        ],
        col_widths=[6.0, 4.5, 4.5])

    add_para(doc,
        "两者串联:designer 产出 → 主 agent 调 lint → FAIL 则报告原文委派 designer 修订 →"
        "修订后 PASS → 委派 reviewer → reviewer 通过 → 主 agent 再调一次 lint 确认未回归 →"
        "request_approval 终稿闸门。")

    add_h2(doc, "8.4 设计细节亮点")
    add_table(doc,
        ["细节", "说明"],
        [
            ["行首行尾 | 可选", "模型时常省略首尾竖线;strip(\"|\").split(\"|\") 直接消化"],
            ["附录表格不误判", "按列名精确匹配 EXPECTED_HEADERS,遇到非表格行即停"],
            ["「声明裁剪」区分「漏覆盖」", "矩阵行有「—/不覆盖」字样视为合法;这是普通 lint 看不到的设计点"],
            ["整模块裁剪合法", "若某模块的所有 TP 都声明不覆盖,整模块零用例不阻断"],
            ["双向核对", "测试点不在矩阵 / 用例不在表 → 防止「矩阵与用例两张皮」"],
            ["35 异常关键词枚举", "「错误/失败/异常/越权/并发…」,让异常占比指标可机器统计"],
            ["_CONCRETE_AFTER_RE 看后续 12 字符", "「错误密码 Wrong99」中虽然「错误密码」抽象但后续有具体值,放行"],
        ],
        col_widths=[5.5, 11.0])

    doc.add_page_break()

    # ===== 九、优化方向 =====
    add_h1(doc, "九、优化方向(按收益/成本划分)")

    add_h2(doc, "9.1 P0:稳定性 / 一致性")
    add_h3(doc, "① Lint 的问题定位可以更精细")
    add_para(doc,
        "当前 _Issue.render 只显示「哪条用例」,不显示在哪张表的哪一段。"
        "当一张表 100+ 条时,designer 修订成本高(要全文搜)。"
        "优化:在 issue 里加 line_no(行号),模型 read_file 时直接定位。")

    add_h3(doc, "② chat 模型切换未做配置校验")
    add_para(doc,
        "agent.py:42-61 只检查 LLM_PROVIDER 是否为 \"qwen\",其他全部走 deepseek。"
        "若用户不小心写成 LLM_PROVIDER=QWEN(大写)会静默失败,跑到 deepseek 上。"
        "优化:显式列出合法取值,否则启动时报错。")

    add_h3(doc, "③ 粗扫的「广度优先不展开」边界容易模糊")
    add_para(doc,
        "ROUGH_SCANNER_PROMPT 禁止展开 F-points/TP,但实际跑下来模型常常「忍不住就展开了」。"
        "优化:在 _ID_RE 之外加 lint 工具校验 /analysis/<doc>-rough-scan.md 的章节结构"
        "(只允许一级 H2 标题 + 一句话,不允许出现 TP- 编号)。")

    add_h2(doc, "9.2 P1:可观测性 / 可调试性")
    add_h3(doc, "④ 断点恢复可做 checkpointing 摘要")
    add_para(doc,
        "FileUploadMiddleware 注入的阶段判断只是「产物文件是否存在」。"
        "若用户在中途被驳回/中断,上下文状态不可见。"
        "优化:在 state 增加 current_stage 字段,主 agent 顶多 _doc_stages_block 一眼看到。")

    add_h3(doc, "⑤ denoise 审计 md 内容过于啰嗦")
    add_para(doc,
        "render_audit_md 把每条删除都列出来,50+ 条删除的文档会很冗长。"
        "优化:超过 N 条时折叠详情,只列「按规则统计 + 前 5 条样本 + 完整 JSON 路径」。")

    add_h3(doc, "⑥ MCP 加载失败时无重试")
    add_para(doc,
        "_load_mcp_tools 一次失败就降级,没有重试。若 MCP 容器启得比 agent 慢,启动时序不稳。"
        "优化:加一次指数退避重试。")

    add_h3(doc, "⑦ Skills 库目前是只读,没有版本切换/AB 实验")
    add_para(doc,
        "改一个 SKILL.md 直接影响产线。优化:支持技能版本 skills/<name>@v1/SKILL.md,"
        "主 agent 通过 .env 选择挂载哪个版本。")

    add_h3(doc, "⑧ 子代理提示词是字符串常量")
    add_para(doc,
        "agent.py:82-115 直接 import 四个 prompt,没法在不重启的情况下热调。"
        "优化:从 SKILL.md 同源加载,避免「提示词里改了技能没说/反之」的脱节。")

    add_h3(doc, "⑨ lint 规则硬编码,难扩展")
    add_para(doc,
        "_VAGUE_BANNED / _ABNORMAL_KEYWORDS 都是字面量列表。"
        "优化:抽到 lint_rules.yaml,不同行业可定制(如医疗行业有自己的模糊词清单)。")

    add_h2(doc, "9.3 P2:工程化 / DX")
    add_h3(doc, "⑩ 前端定制接口缺鉴权")
    add_para(doc,
        "server_ext.py:107 直接 get_client(url=LANGGRAPH_API_URL),没有任何 token。"
        "适合本地开发,但部署到公网时是裸奔。"
        "优化:加 Bearer Token 校验 + CORS 白名单。")

    add_h3(doc, "⑪ prompts.py 嵌入业务规则与 .env 读取")
    add_para(doc,
        "_scope_raw = os.getenv(\"TESTCASE_SCOPE\", \"standard\") 在 import 期读取。"
        "改 .env 必须重启 agent,且没法动态切换。"
        "优化:把 scope 也作为 state 注入主 agent 的 system prompt。")

    add_h3(doc, "⑫ run_watchdog.py 缺少与 LangGraph dev 的协同")
    add_para(doc,
        "watchdog 只是「agent 不在了就重启」,但 LangGraph dev 自己崩溃 watchdog 不知道。"
        "优化:watchdog 同时探活 /api/download 这种轻接口,更稳定。")

    add_h2(doc, "9.4 P3:值得记录但优先级低")
    add_h3(doc, "⑬ denoise 的「宁留勿删」可暴露调节入口")
    add_para(doc,
        "当 aborted=True 时只是保留原文告诉用户,没有给出「放宽阈值重试」的操作。"
        "优化:让 DoclingParseMiddleware 在 aborted 时注入 SystemMessage 时附上"
        "「如需重试可调高 DOCLING_DENOISE_MAX_DELETE_RATIO」,引导自愈。")

    add_h3(doc, "⑭ Excel 样式固定,不支持用户定制品牌色")
    add_para(doc,
        "_PRIORITY_FILL 是写死的(橙红/黄/绿/灰)。"
        "优化:把色板抽到 .env,团队可以匹配企业品牌色。")

    add_h3(doc, "⑮ 追溯矩阵只有 Markdown 一种格式")
    add_para(doc,
        "用户若想导出「覆盖率雷达图」或「覆盖率仪表盘」无法。"
        "优化:把矩阵当成结构化数据(/analysis/<doc>-trace-matrix.json),供上层图表工具消费。")

    doc.add_page_break()

    # ===== 附:通用工程模式总结 =====
    add_h1(doc, "附:项目体现的通用工程模式")

    add_para(doc, "这个项目让我看到的几条可复用的工程模式:")
    add_table(doc,
        ["模式", "在本项目的体现"],
        [
            ["软约束 + 硬约束双层防线", "SKILL.md 红线 + tools/lint_tool.py 八道检查"],
            ["副作用集中到主 agent", "子代理 tools=[],导出 / 落盘 / 批准全部归主 agent"],
            ["渐进式加载提示词", "Skills 规范,启动只注入 name/description"],
            ["阶段 = 文件存在性", "FileUploadMiddleware 通过产物文件判断阶段"],
            ["人工闸门 = interrupt_on 拦截", "request_approval / request_scope_selection"],
            ["跨阶段产物共享", "子代理之间通过虚拟文件系统传稿,避免 token 膨胀"],
            ["失败降级", "MCP 连接失败 / Tavily key 缺失都不阻断启动"],
            ["宁留勿删", "denoise 30% 安全刹车 + 整行匹配 + 审计可回溯"],
        ],
        col_widths=[6.0, 10.5])

    add_para(doc,
        "这种「流水线即系统」的设计哲学让整个 agent 的复杂度可控、可调试、可演进"
        "——每个子代理、每个工具、每条 lint 规则都有清晰的角色定位,"
        "没有一个「啥都干一点」的多功能模块。")

    # 保存
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_PATH)
    print(f"[OK] Word 文档已生成:{OUTPUT_PATH}")


if __name__ == "__main__":
    build()
