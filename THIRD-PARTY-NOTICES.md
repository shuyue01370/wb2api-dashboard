# 第三方组件与出处说明

本项目是 **WorkBuddy2API 网关的一个独立可视化控制面板**，与上游项目没有隶属关系，
也不修改上游仓库的任何文件。

## 1. workbuddy2api（运行时依赖，必需）

- 上游仓库：https://github.com/Sliverkiss/workbuddy2api
- 作用：真正的网关本体。本面板只是它的管理界面，**所有对话请求、账号池、签到等逻辑都由它提供**。
- 许可证：MIT License，Copyright (c) 2026 Sliverkiss
- 与本项目的关系：
  - 本仓库**不包含**上游的任何源码副本，也**不包含**编译好的二进制。
  - `bin/` 下的 4 个可执行文件需由使用者**自行从上游源码编译**（见 `tools/build-bins.sh`），
    或直接使用上游仓库自己的 Docker 部署方式。
  - 若你自行编译并分发这些二进制，请一并保留上游的 MIT 许可证与版权声明。
- 接口事实（本面板依赖的部分）：上游只暴露 OpenAI 兼容协议 4 条路由
  （`POST /v1/chat/completions`、`GET /v1/models`、`GET /status`、`GET /healthz`），
  没有 Anthropic 原生路由，也没有任何管理类写入接口 —— 所以本面板通过子进程调用
  上游自带的 `login` / `signin_bin` / `credit` 工具与直接读写 `auths/` 目录来完成管理动作。

## 2. 打包相关（仅当你自行构建单文件 exe 时需要）

| 组件 | 许可证 | 用途 |
|---|---|---|
| Python | PSF License | 运行 `server.py` / `launcher.py` / `app.py` |
| pywebview | BSD-3-Clause | 用系统 WebView2 内核渲染原生窗口 |
| pythonnet / clr_loader | MIT | pywebview 的 Windows 后端 |
| PyInstaller | GPL-2.0-or-later **with Bootloader exception** | 打包成单文件 exe（该例外允许分发打包产物） |

以上均不随本仓库分发，由使用者按需自行安装。

## 3. 本项目的代码

除上述依赖外，本仓库全部代码（`server.py`、`index.html`、`launcher.py`、`app.py`、
`build_exe.py`、`tasks_all.py`、`tests/`）均为本项目原创，以 MIT 许可证发布，见 `LICENSE`。

## 4. 关于客户端接入的第三方工具

`使用教程.md` 与 README 中提到的 Claude Code、Codex CLI、Cline、Continue、Aider、Cursor、
cc-switch 等，均为各自权利人的产品，本项目仅说明配置方式，不包含其任何代码。
