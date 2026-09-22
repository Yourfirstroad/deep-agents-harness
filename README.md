# 测试用例生成 Agent(deep-agents-harness)

基于 deepagents + LangGraph 的测试用例生成智能体:上传需求文档(PDF/Word/图片等),
自动解析为 Markdown,生成测试用例并交付 Markdown / Excel / XMind 三种产物。

## 启动

```bash
pip install -r requirements.txt
langgraph dev   # http://127.0.0.1:2024,Assistant ID = testcase
```

依赖本地 docling-serve(文档解析,默认 http://localhost:5001),配置见 `.env`。

## 前端对接 API(前后端分离)

前端只需 2 个自定义接口(由 `server_ext.py` 提供,与 LangGraph API 同端口):

### 上传文件:`POST /api/upload`

multipart 表单,字段:

| 字段 | 说明 |
|---|---|
| `file` | 文件二进制(pdf/docx/pptx/xlsx/html/png/jpg…,以及 md/txt 纯文本) |
| `thread_id` | LangGraph 会话线程 ID(先通过 LangGraph SDK 创建线程) |

行为:文件落盘到服务器 `uploads/` 目录,并以 FileData 注入该线程的对话状态
(文本走 utf-8,二进制统一 base64);Agent 下次响应时自动完成解析与登记。

```bash
curl -F "file=@需求文档.pdf" -F "thread_id=<线程ID>" http://127.0.0.1:2024/api/upload
# => {"path": "/uploads/需求文档.pdf", "name": "需求文档.pdf", "size": 123456}
```

```js
const form = new FormData();
form.append("file", fileInput.files[0]);
form.append("thread_id", threadId);
const res = await fetch("http://127.0.0.1:2024/api/upload", { method: "POST", body: form });
const { path } = await res.json();   // 之后照常发送消息即可,Agent 会自动解析该文档
```

### 下载产物:`GET /api/download?file=<文件名>`

从服务器 `outputs/` 目录下载交付物(Agent 回复末尾会给出这些链接):

```bash
curl -O "http://127.0.0.1:2024/api/download?file=需求文档-testcases.xlsx"
```

产物命名约定(`<doc>` 为文档名去扩展名):
`<doc>-testcases.md` / `<doc>-testcases.xlsx` / `<doc>-mindmap.xmind` / `<doc>-mindmap.html`

## 目录结构

| 目录 | 内容 |
|---|---|
| `uploads/` | 用户上传的原始文件(服务器落盘留档) |
| `outputs/` | 生成的交付物(Excel / XMind / Markdown / HTML) |
| `middleware/` | `DoclingParseMiddleware`(自动解析)、`FileUploadMiddleware`(登记+流程注入) |
| `tools/` | `docling_tool`(解析)、`excel_tool`、`xmind_tool` |
| `skills/` | 专业技能库(Agent Skills 规范,见下节) |

## 技能库(skills/)

按 [deepagents skills](https://docs.langchain.com/oss/python/deepagents/skills) 渐进式加载:
启动时只注入各技能的 name/description,模型在对应阶段 `read_file` 加载完整方法。

| 技能 | 用途 | 触发阶段 |
|---|---|---|
| `requirement-analysis` | 需求分析、测试点提取、需求疑问识别,产出 `/analysis/<doc>-test-points.md` | 步骤 1 |
| `test-design` | 用例设计技术选择矩阵(等价类/边界值/判定表/状态迁移/场景法/正交/错误推测) | 步骤 2 |
| `testcase-review` | 覆盖度核对、质量自检、优先级评审,产出 `/review/<doc>-review.md` | 步骤 3 |

后端为 `CompositeBackend`:`/skills/` 路由到磁盘 `skills/` 目录(FilesystemBackend),
其余路径(/uploads、/outputs)仍走 StateBackend,与前端文件注入机制兼容。

新增技能:在 `skills/<skill-name>/` 下写 `SKILL.md`(YAML frontmatter 需含
`name`、`description`,且 `name` 与目录名一致),支持 `references/` 子目录放
详细资料;重启 `langgraph dev` 后自动被发现。
