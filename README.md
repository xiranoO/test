# Issue Lens：GitHub Issue 管理 Agent

一个用于作品集演示的、可审计的 GitHub Issue 分析工作流。它既能读取本地模拟数据，也能以严格只读方式分析真实公开 GitHub 仓库，完成：

- Issue 类型、优先级和模块分类
- 疑似重复 Issue 检测
- 相关代码文件定位，并返回匹配行
- 复现步骤、待确认信息和修复方案生成
- 回复草稿生成
- 人工批准后的模拟提交

## Web 审批工作台

安装依赖后启动本地网页：

```powershell
py -m pip install -r requirements.txt
py web_main.py
```

浏览器访问 `http://127.0.0.1:8000`。网页支持真实 GitHub 只读分析、评论证据查看、回复草稿编辑、二次确认和模拟提交。还可以载入仓库中的 Open Issues，选择最多 10 条顺序分析，并从队列打开已有分析记录。审批状态与审计事件保存在本机 `.data/reviews.sqlite3`，该文件已被 Git 忽略。

批量队列只批量执行分析，不提供批量发布。每条草稿必须单独打开并完成人工确认。

不填写仓库并输入 Issue `101` 可以运行离线演示。默认配置不会向 GitHub 发布评论或修改标签。

### 可选：启用受控的真实评论发布

只应对你有权操作的测试仓库启用。创建一个仅授权目标仓库、拥有 `Issues: Read and write` 权限的 fine-grained token，然后在启动 Web 服务的同一终端设置：

```powershell
$env:ENABLE_GITHUB_WRITE = "true"
$env:GITHUB_WRITE_TOKEN = "你的独立写入 Token"
py web_main.py
```

读取仍使用 `GITHUB_TOKEN`，写入单独使用 `GITHUB_WRITE_TOKEN`。真实发布必须依次完成草稿审核、校验码确认，并准确输入 `PUBLISH owner/repo#编号`。提交模式始终默认选中“模拟提交”。当前写入客户端只能创建一条 Issue 评论，不能关闭 Issue、修改标签或删除内容。

## 命令行运行

Windows 下可运行：

```powershell
py main.py
```

查看完整结构化结果：

```powershell
py main.py --issue 101 --json
```

明确批准并执行模拟提交：

```powershell
py main.py --issue 101 --approve
```

不传 `--approve` 时，工作流只会生成草稿并停在 `waiting_for_approval`，不会提交任何内容。

## 真实 GitHub 只读模式

传入公开仓库和 Issue 编号：

```powershell
py main.py --repo owner/repo --issue 123
```

程序只会执行以下只读操作：

- 通过 GitHub REST API 读取目标 Issue、评论和最近的历史 Issue
- 排除 Issues 接口中混入的 Pull Request
- 将公开仓库浅克隆到 `.cache/github/owner/repo`
- 在本地快照上执行代码检索

代码检索会扫描常见源文件以及 `pyproject.toml`、requirements、锁文件和构建配置；依赖类 Issue 会优先排序依赖清单，并降低项目名、`version` 等普通词的权重。

公开数据不要求登录，但 GitHub 对匿名请求有较低的频率限制。可选择仅在当前终端设置 Token：

```powershell
$env:GITHUB_TOKEN = "你的只读 Token"
py main.py --repo owner/repo --issue 123 --history-limit 100
```

真实模式禁止 `--approve`，不会评论、修改标签或产生其他 GitHub 写操作。Token 不会写入项目文件，也不会传给 `git clone`；当前版本因此只支持克隆公开仓库。

评论会携带作者和 `author_association` 进入分析上下文。只有 `OWNER`、`MEMBER` 或 `COLLABORATOR` 的明确评论才可作为维护策略、修复状态或版本承诺的依据；未读取 Issue timeline 时，Agent 仍不会根据 `closed` 状态猜测关闭原因。

## DeepSeek 智能分析

设置 DeepSeek API Key 后，`auto` 模式会保留本地候选召回，再让模型基于 Issue、重复候选和代码片段生成结构化分析：

```powershell
$env:LLM_PROVIDER = "deepseek"
$env:DEEPSEEK_API_KEY = "你的 Key"
$env:LLM_MODEL = "deepseek-v4-flash"
py main.py --repo owner/repo --issue 123
```

也可以明确选择分析器：

```powershell
py main.py --issue 101 --provider heuristic
py main.py --issue 101 --provider deepseek --json
```

模型请求失败、返回空内容或 JSON 校验失败时，工作流自动回退到规则分析，并在 `provider_warning` 中说明原因。Key 只从环境变量读取，不会写入输出、缓存或日志。

## 测试

```powershell
py -m unittest discover -s tests -v
```

离线质量评测：

```powershell
py evaluate.py --min-score 0.80
```

评测集位于 `data/eval/cases.json`，会检查分类、组件、重复 Issue 和相关文件召回。GitHub Actions 会在每次 push 和 pull request 时运行测试、质量门槛和语法编译检查。

## Docker 本地运行

复制 `.env.example` 为 `.env`，填写需要的读取或模型配置，然后运行：

```powershell
docker compose up --build
```

访问 `http://127.0.0.1:8000`。Compose 只映射本机回环地址，且真实 GitHub 写入默认关闭。除非另行增加身份认证和 HTTPS，否则不要把启用写入的 Web 服务暴露到公网。

停止容器：

```powershell
docker compose down
```

## 架构与安全边界

完整的数据流、信任边界和失败处理见 [`docs/architecture.md`](docs/architecture.md)。核心设计是读取与写入客户端分离、模型输出强校验、独立写入 Token，以及不可跳过的人工确认。

## 当前边界

- 当前只支持公开仓库的 Git 快照；私有仓库克隆尚未接入。
- 批量分析由浏览器顺序执行，并非后台任务队列。
- Web 工作台没有用户登录，定位为本机工具。
- GitHub 写入仅支持创建 Issue 评论，不修改标签和 Issue 状态。
- 离线评测集规模较小，需要持续加入真实失败案例。
