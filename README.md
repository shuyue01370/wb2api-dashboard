# WorkBuddy2API Dashboard

**WorkBuddy2API 网关的本地可视化控制面板** —— 一个窗口管好账号池、自动切换、运维操作、接口测试与客户端接入。

> 独立项目：**不修改上游仓库的任何文件**，与上游没有隶属关系。
> 运行时依赖上游 [Sliverkiss/workbuddy2api](https://github.com/Sliverkiss/workbuddy2api)（MIT 许可）。

**特点**

- **零依赖后端** —— `server.py` 只用 Python 标准库，不需要 `pip install` 任何东西
- **单文件前端** —— `index.html` 内联 CSS/JS，无 CDN、无构建步骤、可离线
- **两种形态** —— 浏览器模式（`启动面板.bat`）或**单文件 exe**（一个窗口、无命令行，可独立分发）
- **双模式运行** —— 原生（直接跑 Windows 网关 exe）/ Docker（兼容保留），自动判定
- **关窗即全停** —— Windows Job Object 绑定进程树，父进程死亡内核回收全部子进程
- **反馈明确** —— 每个动作都有常驻状态条与实时输出，不靠一闪而过的 toast
- **账号好管** —— 从本机客户端登录态抓取入池；账号池单行「移除」（先备份再删、自动重载网关）；
  不想要的本机账号可「拉黑」，等它在客户端**重新登录**后自动恢复显示

## 直接下载（已打包好的单文件 exe）

不想自己编译的话，直接取打包好的版本 —— 内嵌 Python 运行时与全部网关二进制，**目标机器无需安装 Python**：

**下载地址**

```
https://github.com/shuyue01370/wb2api-dashboard/releases/latest/download/WorkBuddy2API.exe
```

> **维护者**：本仓库地址已配置为 `shuyue01370/wb2api-dashboard`。若日后改了仓库名，
> 重跑一次替换即可，三种入口任选：
> `python tools/set_repo.py 用户名/仓库名`（最省事，任意终端）、
> `tools\set-repo.cmd 用户名/仓库名`（Windows 命令行）、
> `bash tools/set-repo.sh 用户名/仓库名`（Git Bash、macOS、Linux）。

双击就一个窗口，没有命令行窗口、不用外部浏览器。要求与说明：

- **唯一系统要求**：WebView2 运行时（Win10 1803+ 与 Win11 系统自带；老系统装一次
  Microsoft Edge WebView2 Runtime）
- **内嵌内容**：Python 运行时、pywebview、网关 `wb2api.exe`、`login/credit/signin_bin`、
  任务执行器 `tasks_all.exe`、面板页面
- **数据根自动定位**：优先 exe 同目录；首次运行自动生成 `config.json` / `auths/` / `data/`
- **面板自身状态**也存在 exe 同目录：`panel-state.json`（本机账号拉黑名单）、
  `removed-auths/`（被「移除」账号的备份），换台机器只搬 exe 即可
- **首次打开账号池是空的**：用「添加账号」弹窗的三种方式添加
  （OAuth 授权 / 粘贴 Token / 从本机客户端导入），成功后会**自动重载网关**
- 详见下面「[分发给他人](#分发给他人便携使用)」一节

> 想自己编译，或者要改代码，继续往下看 —— 自行构建需要先编出 `bin/` 下的网关二进制。


## 目录结构

```
wb2api-dashboard/
├── app.py                  单文件应用入口（pywebview + 系统 WebView2）
├── build_exe.py            打包脚本：生成 dist/WorkBuddy2API.exe
├── 启动面板.bat            浏览器模式入口（开发用）
├── launcher.py             一键启动器：网关 + 面板 + 应用窗口，退出全停
├── server.py               面板后端（零依赖，仅标准库；native / docker 双模式）
├── index.html              可视化面板（单文件，内联 CSS/JS）
├── tasks_all.py            成长计划任务聚合执行器（批量接取 / 领奖）
├── config.example.json     网关配置模板（config-native.json 属本机文件，不进仓库）
├── 使用教程.md             客户端接入教程（Claude Code / Codex / Cline / Continue / Aider / Cursor …）
├── tools/build-bins.sh     从上游源码编译出 bin/ 下的 4 个网关二进制
├── bin/                    ← 自行构建产出，本仓库不提供
├── logs/                   网关日志（面板「实时日志」跟随的就是它）
└── tests/                  前端自检脚本 + 进程检查工具
```

## 前置条件（自行构建时需要）

| 需要 | 说明 |
|---|---|
| 上游网关 | 克隆 [Sliverkiss/workbuddy2api](https://github.com/Sliverkiss/workbuddy2api) 并编译出二进制（见下节） |
| Python 3.10+ | 运行面板本体（无第三方依赖；开发与测试环境为 3.13） |
| WebView2 运行时 | **仅单文件 exe 需要**；Win10 1803+ 与 Win11 系统自带 |
| Docker | 仅编译上游二进制时用，可选 |

> **建议与上游仓库并列放置**：例如 `tools/wb2api-dashboard/` 与 `tools/workbuddy2api/`。
> 面板默认按「同级 `../workbuddy2api`」定位数据根，找不到再退 `~/workbuddy2api`；
> 也可用环境变量 `WB2API_DIR` 显式指定。

## 构建 bin/（首次必做）

本仓库**不包含**编译好的二进制，也**不包含**上游源码，需要自己编一次。先把两个仓库放到同一个父目录：

```bash
git clone https://github.com/shuyue01370/wb2api-dashboard              # 本项目
git clone https://github.com/Sliverkiss/workbuddy2api    # 上游网关
bash tools/build-bins.sh                                 # 省略参数时自动找同级 workbuddy2api
```

产出 `bin/wb2api.exe`、`signin_bin.exe`、`login.exe`、`credit.exe`，并生成 `bin/config.json`
（与上游仓库共用同一份账号池与状态）。只需 Docker，本机不必装 Go；上游更新后重跑同一条命令即可。

## 快速开始

### 方式一：单文件 exe

```bash
python -m venv .venv
# Windows 用 .venv/Scripts/...，macOS/Linux 用 .venv/bin/...
.venv/Scripts/pip install pywebview pyinstaller
.venv/Scripts/python build_exe.py          # 产物：dist/WorkBuddy2API.exe
```

双击 **`dist/WorkBuddy2API.exe`**，只有一个窗口，没有命令行、不用外部浏览器：

1. 拉起网关 `bin/wb2api.exe`（127.0.0.1:7863，无窗口子进程）
2. 面板服务在应用进程内启动（127.0.0.1:7864）
3. 窗口用系统 **WebView2 内核**渲染面板页
4. **关闭窗口 → 网关、面板、WebView2 全部停止**，不留残留进程

> 配置与日志写在 **exe 同目录**（`config-native.json` / `.runtime.json` / `logs/`），
> exe 可随意移动，数据跟着它走。

### 方式二：启动面板.bat（浏览器模式，开发用）

双击 **`启动面板.bat`**，会做四件事：

1. 启动网关 `bin/wb2api.exe`（127.0.0.1:7863，等价于容器里的 wb2api）
2. 启动面板 `server.py`（127.0.0.1:7864）
3. 弹出一个 **Chrome 应用窗口**（无地址栏无标签页，观感即桌面应用）显示管理页面
4. **关闭应用窗口 / 关闭控制台 / Ctrl+C 任一操作 → 全部停止**
   （Windows Job Object 把进程树绑在一起，父进程死亡内核即终止全部子进程，
   含任务管理器强杀这种极端情况）

改 `server.py` / `index.html` 后**刷新即生效**（读的是实时文件），不必重新打包 —— 开发用这个。

> 两种方式读写同一份 `auths/` 与 `data/state.json`，可随时切换，但**不要同时跑**（都占 7863/7864）。

## 分发给他人（便携使用）

`dist/WorkBuddy2API.exe` 是**自包含**的，复制给别人就能直接跑：

- **内嵌**：Python 运行时、pywebview(WebView2)、网关 `wb2api.exe`、`login/credit/signin_bin`、
  任务执行器 `tasks_all.exe`（**目标机器无需安装 Python**）、面板页面
- **数据根自动定位**：优先 exe 同目录；首次运行自动生成 `config.json` / `auths/` / `data/`
- **面板自身状态**也存在 exe 同目录：`panel-state.json`（本机账号拉黑名单）、
  `removed-auths/`（被「移除」账号的备份），换台机器只搬 exe 即可
- **首次打开账号池是空的**：用「添加账号」弹窗的三种方式添加
  （OAuth 授权 / 粘贴 Token / 从本机客户端导入），成功后会**自动重载网关**
- **唯一的系统要求**：WebView2 运行时
- 可选环境变量：`WB2API_DIR`（数据根）、`WB2API_PANEL_PORT`（面板端口，默认 7864）

> 分发前确认 `auths/` 里没有你自己的账号凭据 —— 那是你的资产，不该跟着 exe 走。

## Docker 模式（兼容保留）

面板检测不到 `bin/wb2api.exe` 且未设 `WB2API_NATIVE=1` 时回到 docker 模式
（`docker compose up -d` 起容器后双击 bat，动作改走容器命令）。

---

## 它解决的问题

原来的页面是纯静态的，只能手动把 `/status` 的 JSON 粘进去渲染——因为网关**没有实现 CORS**
（实测 `OPTIONS /status` 返回 405，且响应里没有 `Access-Control-Allow-Origin`），
`file://` 页面直接 `fetch` 会被浏览器预检拦掉。

这个面板用一个本地服务做了**同源代理**：页面只请求 `127.0.0.1:7864/api/*`，
由服务端带上 `Authorization: Bearer <api_key>` 转发到 `7863`。所以点按钮就等于执行
`curl.exe -s http://localhost:7863/status -H "Authorization: Bearer <key>"`，不需要再粘贴。

## 页面里有什么

顶栏在任何页签都可见：探活灯、Docker / 容器状态、可用账号数、自动刷新（3/5/10/30s）、明暗主题、手动刷新。

页面按模块分成 **6 个页签**（切换状态记忆在 localStorage，刷新后仍停在原页签）：

| 页签 | 内容 |
|---|---|
| **概览** | KPI 九宫格（账号总数 / 可服务 / 冷却 / 熔断 / 禁用 / 在途 / 积分合计 / 存储模式 / 粘性会话）、定时排程（签到 / 猫猫旅行 / 活跃上报 / Token 保活，带下次触发倒计时）、配置全览 |
| **账号池** | 账号表格（状态徽标、积分、在途、成功率条、可见权重、连续失败、冷却剩余、解冻时间、最近成功/错误；可排序/过滤）＋ 每行「移除」（文件先备份到 `removed-auths/`，随后自动重载网关）＋ 选号逻辑模拟（三因子权重 Top5 短名单 + 模拟抽签）；下方「本机 WorkBuddy 客户端」区块：读客户端明文登录态，一键抓取入池 / 更新凭据 / 拉黑（拉黑后不再显示，客户端重新登录即自动恢复，也可在「已拉黑」名单里手动解除） |
| **自动切换** | 四级降级链路（直选 → 模型级豁免 → 全冷却兜底 → 503）、真实 `fallback_earliest_expiry` 事件、切换与恢复规则、事件时间线、实时日志跟随 |
| **运维操作** | 一键操作（签到 / 积分任务 / 积分日报 / 登录 / 重载）+ 作业控制台 + 账号文件 / 持久化状态 / 容器信息 |
| **接口测试** | 4 个接口的 curl 示例，**每个都带「测试此接口」按钮**——点一下真实请求 7863，直接看 HTTP 状态码 / 耗时 / 响应体；下方是模型列表（取自 `/v1/models` 实时结果） |
| **接入教程** | 把本网关接到 Claude Code / Codex / Cline·Roo / Continue / Aider / Cursor / Cherry Studio / SDK 的完整配置，每段可一键复制；地址、key、模型名全部按当前实际值动态生成 |

页签上带角标：**账号池**显示账号数，**自动切换**显示异常账号数（黄色，冷却 / 熔断 / 禁用 / 在途满），**运维操作**显示运行中的作业数。

### 按钮点了必须有反馈、执行中不可重复点

点任意动作按钮后立刻有三件事发生：

1. **按钮自己**转圈 + 文字变「执行中…」并 `disabled`。忙碌态记在 `S.pending`，
   并且 `renderMeta()` 末尾会重新套用 —— 否则一次自动刷新就把忙碌态冲掉了。
2. **面板顶部出现常驻状态条**（`#actStatus`）：执行中显示蓝色转圈 + 「已运行 N 秒」；
   结束变绿色 `✓ 完成` 或红色 `✗ 失败` + 退出码。**失败提示会一直留着**，
   不只靠 2.6 秒就消失的 toast。
3. **作业输出区滚动进视野**。

> ⚠️ 关键修复：`#jobArea` 原先在「一键操作」面板**最底部**，而动作卡片有一长列，
> 于是输出落在视野之外 —— 用户点完按钮看不到任何变化，反馈等于零。
> 现在 `#actStatus` → `#jobArea` → `#acts` 的顺序有专门的回归断言
> （`html.indexOf('id="jobArea"') < html.indexOf('id="acts"')`），
> 谁再把输出区挪下去测试会直接失败。

## 接口测试页签是怎么测的

浏览器不能直连 `7863`（网关无 CORS，会被预检拦掉），所以「测试此接口」走后端的
`GET /api/probe?ep=<healthz|status|models|chat>`：由服务端带 `Authorization` 真实请求网关，
再把**原始 HTTP 状态码、耗时、响应体**原样回给页面。这是前端自己 fetch 做不到的——前端拿不到真实状态码。

- `healthz` 的 `503` 会被单独标注为「进程正常但当前没有可服务账号」，不当作失败
- `chat` 会真实消耗一次对话额度，模型可在页面上选（默认取实时列表里的 `deepseek-v4.1-flash`）
- 探针**只做只读 GET**；`chat` 是唯一会写上游的，且只用固定的 32 token 上限

## 一键导入 cc-switch（官方深链协议）

「接入教程」页签顶部两个按钮，把本网关作为供应商导入 cc-switch，不用手填 base_url / key / 模型。

**走 cc-switch 官方的 `ccswitch://` 深链协议，不碰它的 SQLite 库。**
前提：系统里已注册 `ccswitch://` 协议（安装 cc-switch 时会自动注册，指向 cc-switch.exe），点链接才会唤起它的导入确认框。

生成的两条链接（实测可解析）：

```
ccswitch://v1/import?resource=provider&app=claude&name=WorkBuddy2API%20本地网关
  &endpoint=http://127.0.0.1:7863/v1&apiKey=test_key&model=deepseek-v4.1-flash
  &apiFormat=openai_chat&haikuModel=glm-5.3-flash&sonnetModel=...&opusModel=...

ccswitch://v1/import?resource=provider&app=codex&name=WorkBuddy2API%20本地网关
  &endpoint=http://127.0.0.1:7863/v1&apiKey=test_key&model=deepseek-v4.1-flash&apiFormat=openai_chat
```

点击 → 系统唤起 cc-switch → 弹**导入确认框** → 确认即写入。模型名会跟着 `/v1/models` 的实时结果走。

### 一个意外收获：Claude Code 不再需要 claude-code-router

cc-switch 自带本地代理（默认 `127.0.0.1:15721`），供应商的「API 格式」可选：

| API 格式 | 代理行为 |
|---|---|
| Anthropic Messages | 透传，不转换 |
| **OpenAI Chat Completions** | Anthropic → OpenAI Chat，响应反向转换 |
| OpenAI Responses API | Anthropic → Responses，响应反向转换 |

所以 **Claude Code → cc-switch 代理(15721) → 本网关(7863, OpenAI 格式)** 是通的，中间不需要 CCR。
深链已把 `endpoint` 指向本网关并带上 `apiFormat=openai_chat`。

### 导入后还需两步（cc-switch 内的开关，深链带不过去）

1. Claude Code：供应商「高级选项 → API 格式」确认是 **OpenAI Chat Completions**
2. Claude Code：「设置 → 路由」打开本地路由总开关（默认监听 `127.0.0.1:15721`），并打开 `Claude` 接管

> Codex 侧若报 404 / 协议错误：Codex 默认发 Responses 协议，而本网关只有 Chat Completions，
> 需 cc-switch v3.16.0+ 用本地路由做协议改写。

### 关于 1M 上下文（WorkBuddy 里的 300k / 1M 开关）

WorkBuddy 客户端里的 300k / 1M 选择是**它自己的 UI 设置，不在 API 请求链路上**——
网关只透传请求体，不改任何上下文字段（`internal/upstream/payload.go` 的改写清单里没有 context 类字段）。

走代理时，窗口由 **Claude Code 侧**决定，机制是模型 ID 的 **`[1m]` 后缀**（官方文档 Model configuration）：

- `ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4.1-flash[1m]` → 该别名启用 1M 窗口
- 官方明确：**指向网关时 Claude Code 无法探测 1M 支持，必须用后缀声明**
- **Claude Code 会在发给 provider 前把后缀剥掉**，所以网关/上游拿到的仍是干净模型名
- 实测印证：把带后缀的名字直接发给网关，上游报 `400 code=11102 model service info not found`
  —— 说明后缀确实不该到上游，也给了你一个排错特征

深链已自动给 `sonnetModel` / `opusModel` 加上 `[1m]`；`haikuModel`（后台任务）不需要。
若你在 cc-switch 里手动填，照上面的格式加后缀即可。相关开关：`CLAUDE_CODE_DISABLE_1M_CONTEXT=1`
可显式关掉 1M；`CLAUDE_CODE_AUTO_COMPACT_WINDOW` 可调自动压缩阈值。

**未验证项**：上游 API 路径是否默认按 300k 截断（而非按目录的 1M 受理），代码里看不出来，
需要发一条 >300k token 的真实请求才能确证——成本较高，未做。若你发现长对话被提前压缩，
告诉我，我再设计验证。

### 明确的不确定性

- `apiFormat` **不在**官方深链文档的参数表里，是本实现附加的。cc-switch 的解析器把参数收进
  HashMap，多出的键通常被忽略 —— 所以它不会导致导入失败，**但也不保证生效**。
  因此上面保留了「手动确认一次」这一步，没有假设它一定成功。
- 导入确认框需要你在 cc-switch 里点「确认」才能落库，这一步无法自动完成。

## 接入客户端（教程模块）

页签栏最后一项 **「接入教程」** 按客户端分块列出完整配置，每段可一键复制；完整版见同目录 [`使用教程.md`](使用教程.md)。三条最容易踩的：

1. **Claude Code 不能直连** —— 网关只有 OpenAI 格式的 `/v1/chat/completions`，**没有 Anthropic 的 `/v1/messages`**，
   所以必须用 `claude-code-router`(CCR) 做格式翻译。Codex / Cline / Continue / Aider / Cursor 支持 OpenAI 格式，可直连。
2. **base URL 有三种写法**：OpenAI 兼容客户端填 `http://127.0.0.1:7863/v1`；CCR 的 `api_base_url` 要写全
   `.../v1/chat/completions`；Claude Code 的 `ANTHROPIC_BASE_URL` 填的是 **CCR 的地址**（`http://127.0.0.1:3456`），不是网关地址。
3. **用 agent 型 CLI 时把 `prompt.mode` 改成 `passthrough`** —— 默认 `custom` 会删掉客户端全部 `system`/`developer`
   消息，只留网关自己的一条通用提示词（`internal/prompt/prompt.go:68-87`），Claude Code / Codex 的 agent 行为会因此退化。
   指纹误报由 `features.sanitize_blacklist_fingerprints` 兜底（`sanitize.go:15-21` 的特征表本就是为这两个 CLI 准备的）。

## 账号「自动切换」是怎么体现的

这是这套东西的核心，做了三层：

1. **降级链路图**——四级节点按当前数据实时高亮，告诉你此刻请求会走哪条路。
2. **Top5 短名单 + 权重**——按 Go 端 `weightOf` 公式
   `w = 1 + 积分/最大积分×10 + min(闲置小时×0.5, 5) + 成功率×3` 排序。
   ⚠️ `/status` **没有透出 `lastUsed`**，所以页面算的是「可见权重」，不含闲置补偿项，实际排序会有偏差——页面上写明了这一点。
3. **真实事件**——网关在无可用账号时会打 `pool: fallback_earliest_expiry uid=… kind=…`，
   面板跟随容器日志抓这类行并单列成「兜底切换」事件；同时前端对比相邻两次 `/status`，
   把 `健康→冷却/熔断/禁用` 的跃迁也记成事件。两路合起来就是一条看得见的切换时间线。

## 一键操作背后的命令

面板只触发**既有的官方脚本/二进制**，不接受任意命令（白名单）：

| 按钮 | 实际执行 |
|---|---|
| 一键签到 | `docker exec workbuddy2api /app/signin_bin /app/auths` |
| 积分日报 | `docker exec -w /app workbuddy2api /app/credit -pretty` |
| **成长计划任务** | `docker run --rm --user root -v "<repo>/scripts:/app/scripts:ro" -v "<本目录>/tasks_all.py:/app/tasks_all.py:ro" -v "<repo>/auths:/root/workbuddy2api/auths" --entrypoint python3 workbuddy2api-wb2api:latest /app/tasks_all.py ALL --only accept\|claim [--yes]` |
| 获取登录链接 | `docker exec workbuddy2api /app/login url` |
| 完成登录 | `docker exec workbuddy2api /app/login poll` |
| 重载账号 | `docker restart workbuddy2api` |

两个必须注意的点：

- **`docker exec` 没有 `-T`**（`-T` 是 `docker compose exec` 的参数），加会报 `unknown shorthand flag`。
- 任务脚本**没打进镜像**，且 `task_common.py` 里 `AUTHS` 写死为 `/root/workbuddy2api/auths`。
  所以用 `docker run --user root` 并把 `auths` 挂到那个路径上——容器默认用户是 `app`(10001)，
  **不加 `--user root` 读不到 `/root`**（实测 `PermissionError`）。这样 `ALL` 模式也能正常工作。

积分任务默认 **试运行（dry-run）**，只查询进度不写入；要真跑得勾选「真实执行」，会二次确认。

## 成长计划任务：一键接取 + 一键领奖

「运维操作」页签里两个按钮，对应两段**都不产生任何上游上报**的操作：

| 按钮 | 实际执行 | 作用 |
|---|---|---|
| **一键接取所有任务** | `tasks_all.py ALL --only accept --yes` | 把 `not_accepted` 批量改为 accepted |
| **一键领取所有积分奖励** | `tasks_all.py ALL --only claim --yes` | 对 `completed` 任务逐个尝试领奖 |

**推荐流程**：点「一键接取所有任务」→ 到 WorkBuddy 客户端逐个完成任务 → 回来点「一键领取所有积分奖励」。

`tasks_all.py` 放本目录，**不进 workbuddy2api 仓库**。任务清单用 `list_tasks` **动态拉取**
（单账号实测 18 个任务），平台新增任务自动适应，不用逐个数。
默认点击即执行，勾选「仅预览」则只打印计划不写库。

### 为什么不做「脚本自动完成任务」（三次实测得出的结论）

1. **仓库 4 个 `task_*.py` 全都不领奖。** `task_common.claim_reward()` 定义了但全仓零调用，
   唯一引用是 `task_model_chat.py:87` 的一句 print。→ 只跑现有脚本，进度满了也拿不到分。
2. **列表的 `completed` ≠ 可领取。** 实测对列表显示 `completed 1/1` 的 `skill_1` 调 claim，
   返回 **`400 task not completed`**。列表进度是**乐观显示**，真实门禁在服务端行为事件上、比列表严。
   → 所以「伪造事件凑进度」这条路不可靠：**进度条会满，但领不到奖**。
3. **18 个任务里只有 3 个判据走事件上报**（`chat_5` / `Model_chat_GLM5.2` / `first_buddy`），
   其余是行为驱动。`chat_request_send` 事件里虽然有 `expertId` / `skillId` / `knowledgeId` /
   `recommendId` 字段（见 `internal/upstream/report.go`），但**仓库里没有任何这些 ID 的来源**；
   猜错会被上游 **200 静默丢弃**，代价是白增上报次数，与网关自身注释写明的
   「每号每天 1 次上报」风控口径冲突。

**自己到客户端做一遍是真实行为**，触发真实事件，门禁才会真正满足——比伪造上报可靠得多。
所以面板只保留上面两个按钮；补跑入口已从面板撤下（脚本里仍保留 `--only run` 供显式调用）。

### 两段的风险（都实测过）

- **`accept`**：只调 `/v2/activity/growth/tasks/accept` 这一个端点，**不碰 `/v2/report`**，
  不产生任何遥测；任务无有效期（`valid_start` / `valid_end` 全为 `null`）。
  实测：`HTTP=200 code=0 msg=OK`。
- **`claim`**：幂等，重复点无副作用；未真正达标的返回 400 并被跳过，**不当错误**。
  实测：`skill_1 → HTTP=400 task not completed`，脚本按正常结果处理。

### 接取后需要你自己去完成的任务

脚本会在每次运行时把这份清单打印出来（不静默跳过），方便你照着做：

| 类别 | 任务 |
|---|---|
| 需要装技能 / 用专家 / 建画布 | `skill_1`、`expert_5`、`Expert_lighthouse`、`Expert_Philanthropy`、`Expert_team_use_3`、`Library_read`、`playbook_prompt`、`create_canvas`、`template_5` |
| 纯桌面端 UI 行为 | `Hp_Appearance`、`Buddy_App`、`Buddy_App_QQ`、`automation_1` |
| 有反例 / 零收益 | `RichMeow_Chat`（实测进度不动）、`black_cat`（0 分 0 能量） |

## 自检脚本

```powershell
# 静态：内联 JS 语法、DOM id 引用完整性、状态判定/权重/格式化的单元断言
node tests\test-frontend-static.js

# 运行时：用真实数据在 DOM 桩里跑完整页脚本，核对各区域渲染产物 + 抓未捕获异常
# （需要面板服务已在 7864 运行）
node tests\test-frontend-runtime.js
```

## 已知边界

- 网关**没有任何写接口**，所以「管理」只能到「看 + 触发既有脚本」：
  禁用账号的恢复只能重新登录，冷却只能等自动到期或签到解冻。
- `signin_bin` 的输出里偶见 `�`——那是它自己 `short()` 按**字节**截断 UTF-8 造成的，不是本面板的问题。
- 「活跃上报」和「猫猫旅行」没有提供 CLI 入口，只能等 scheduler 定时执行，面板里只展示排程。

---

## 遇到问题先看日志

程序会在 **exe 同目录**的 `logs/` 下写两份日志，反馈问题时把这两份一起发出来基本就能定位：

- `logs/app.log` —— 面板/应用自身的启动过程、环境信息（系统版本、路径、WebView2 版本）与异常堆栈
- `logs/gateway.log` —— 网关进程的输出（账号加载、监听地址、上游报错）

| 现象 | 多半是 | 怎么办 |
|---|---|---|
| 双击后窗口没出来、网关也没起来 | 缺 WebView2 运行时 | 看弹窗提示装一次；此时程序会自动改用浏览器打开面板，服务不受影响 |
| 面板显示网关未运行 | 7863 端口被占用 | 两个实例（含 Docker 容器）不能同时跑，先关掉一个；细节见 `logs/gateway.log` |
| 账号状态异常 / 请求拿不到回复 | 账号过期或上游报错 | 「账号池」页签看 token 到期时间，必要时重新授权 |


## 发布新版本（维护者）

单文件 exe 是构建产物、体积也大，所以**不进仓库**，改用 GitHub Release 附件分发。

### 1. 先打包

```bash
taskkill /F /IM WorkBuddy2API.exe      # 关掉正在运行的 exe，否则文件被占用、打包会失败
python build_exe.py                     # 产出 dist/WorkBuddy2API.exe
```

前提：`bin/` 已构建好（见上一节），且已装 `pywebview` + `pyinstaller`。

### 2. 上传：网页操作（零依赖，推荐）

1. 打开 `https://github.com/你的用户名/你的仓库/releases/new`
2. **Choose a tag** 里填一个新 tag（如 `v1.0.0`），点 **Create new tag**
3. **Release title** 填 `v1.0.0`
4. 把 `dist` 目录下的 `WorkBuddy2API.exe` **拖进**页面底部附件区（Attach binaries）
5. 点 **Publish release**

### 3. 上传：命令行（需要一个访问令牌）

```bash
export GITHUB_TOKEN=你的令牌          # 经典令牌给 repo 权限；细粒度令牌给 Contents: Read and write
REPO=你的用户名/你的仓库

# 先建 release，从返回 JSON 里记下 "id"
curl -s -X POST -H "Authorization: Bearer $GITHUB_TOKEN" \
     -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/$REPO/releases \
     -d '{"tag_name":"v1.0.0","name":"v1.0.0","body":"首个版本"}'

# 再上传附件：host 是 uploads.github.com，且 name= 必须写成 WorkBuddy2API.exe
curl -X POST -H "Authorization: Bearer $GITHUB_TOKEN" \
     -H "Content-Type: application/octet-stream" \
     --data-binary @dist/WorkBuddy2API.exe \
     "https://uploads.github.com/repos/$REPO/releases/<上一步的 id>/assets?name=WorkBuddy2API.exe"
```

已经装了 `gh` 的话，一条命令即可：

```bash
gh release create v1.0.0 dist/WorkBuddy2API.exe --title "v1.0.0" --notes "首个版本"
```

### 4. 两个硬约束

- **附件文件名必须保持 `WorkBuddy2API.exe`**：README 顶部的下载链接是
  `releases/latest/download/WorkBuddy2API.exe`，名字一改就是 404。
- `latest` 只认**最新一个正式 Release**：别把新版本勾成 `draft` 或 `pre-release`，否则链接仍指向旧版本。

`dist/tasks_all.exe`（8.7 MB）是任务执行器的独立版本，需要的话可以一并作为附件上传。

## 许可证

本项目以 **MIT 许可证**发布，见 [LICENSE](LICENSE)。

运行时依赖的上游网关 [Sliverkiss/workbuddy2api](https://github.com/Sliverkiss/workbuddy2api)
同为 MIT 许可（Copyright (c) 2026 Sliverkiss）。本仓库不含其源码与二进制；
若你自行编译并分发这些二进制，请一并保留上游的许可证与版权声明。
详见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

## 免责声明

本项目只是一个**本地管理界面**，不提供、不代理、不存储任何模型服务，也不修改上游网关的行为。
使用时请遵守上游项目条款与你所用服务的相关规定；账号凭据由你自己保管，泄露风险自负。
