Release: v4.27.2-Async-Stable
🚀 版本概述
AstrBot v4.27.2-Async-Stable 是基于 v4.27.2-Async 的稳定修复版。在保留大多数性能优化的同时，回退了 aiocqhttp 适配器相关文件到官方版本，解决了因适配器优化导致的消息处理异常（终端无日志、无法接收/发送消息）问题。

⚠️ 本分支仍为非官方版本，与官方后续更新不兼容。
官方原版：AstrBotDevs/AstrBot

📌 与 v4.27.2-Async 的区别
模块	v4.27.2-Async	v4.27.2-Async-Stable
aiocqhttp 适配器	优化版（引入消息问题）	✅ 回退至官方 v4.27.2 版本
其他核心优化（数据库、I/O、知识库等）	✅ 已优化	✅ 保持不变
🧩 包含的优化（与 v4.27.2-Async 相同）
数据库模块：新增复合索引、get_session_conversations 重写为两步查询、减少无谓删除操作。

文件 I/O：SSL 上下文复用、64KB 缓冲写入、进度回调降频。

知识库管理：懒加载、并发检索、结果缓存（LRU 128）。

LLM 元数据：内存+本地文件缓存、请求超时、降级警告。

备份导出：分页查询、异步化 I/O、线程池遍历。

消息组件：统一文件源解析、URI 解码缓存。

消息链工具：生成器优化、提前返回。

详细改动请查看 Release Notes of v4.27.2-Async。

🛠️ 修复的问题
✅ 修复 aiocqhttp 适配器在优化后无法正常接收/发送消息的问题（终端无日志）。

✅ 回退后消息收发功能恢复稳定，日志正常输出。

📦 安装方法
方式一：下载 ZIP 包（推荐）
点击本页面下方的 "Source code (zip)" 下载。

解压后进入目录，执行：

bash
uv tool install . --python 3.12
astrbot init   # 首次运行初始化
astrbot run
方式二：Git 克隆
bash
git clone -b v4.27.2-Async-Stable https://github.com/ARCH0712/AstrBot-Async.git
cd AstrBot-Async
uv tool install . --python 3.12
astrbot init
astrbot run
⚠️ 注意事项
本版本不与官方后续版本兼容，请勿对官方仓库执行 git pull。

如果之前使用 v4.27.2-Async 并遇到了消息问题，强烈建议升级到此版本。

所有 data/ 目录下的数据（对话、配置、知识库）无需迁移，直接替换源码文件即可。

👤 维护者
Arch712（@ARCH0712）

QQ：1658796850

B站：UID3493123588098281

Email：LuoTianyi0712-@outlook.com

Enjoy a stable and faster AstrBot! 🎉

Release: v4.27.2-Async-Stable
🚀 Overview
AstrBot v4.27.2-Async-Stable is a stable hotfix release based on v4.27.2-Async. It reverts the aiocqhttp adapter back to the official version while keeping all other performance optimizations, fixing the message handling issues (no terminal logs, message receiving/sending failures) caused by the optimized adapter.

⚠️ This is still a non-official fork and is not compatible with future official updates.
Official version: AstrBotDevs/AstrBot

📌 Differences from v4.27.2-Async
Module	v4.27.2-Async	v4.27.2-Async-Stable
aiocqhttp adapter	Optimized (caused message issues)	✅ Reverted to official v4.27.2
Other core optimizations (DB, I/O, KB, etc.)	✅ Optimized	✅ Kept unchanged
🧩 Included Optimizations (same as v4.27.2-Async)
Database: Added composite indexes, rewrote get_session_conversations, reduced redundant deletions.

File I/O: SSL context reuse, 64KB buffered writes, progress callback frequency reduction.

Knowledge Base: Lazy loading, concurrent retrieval, LRU result caching.

LLM Metadata: In-memory + local file cache, 10s timeout, degraded logging.

Backup Export: Paginated queries, async I/O, thread-pool traversal.

Message Components: Unified file source parsing, URI decoding cache.

Message Chain Tools: Generator expressions, early returns.

For full details, see Release Notes of v4.27.2-Async.

🛠️ Fixed Issues
✅ Fixed aiocqhttp adapter message handling failures (no terminal output) caused by the optimization.

✅ After revert, message sending/receiving works stably, and logs are printed normally.

📦 Installation
Option 1: Download ZIP (Recommended)
Click "Source code (zip)" below.

Extract and navigate to the folder, then run:

bash
uv tool install . --python 3.12
astrbot init   # first time only
astrbot run
Option 2: Git Clone
bash
git clone -b v4.27.2-Async-Stable https://github.com/ARCH0712/AstrBot-Async.git
cd AstrBot-Async
uv tool install . --python 3.12
astrbot init
astrbot run
⚠️ Important Notes
This version is not compatible with future official updates. Do not run git pull on the official repository.

If you were using v4.27.2-Async and encountered message issues, we strongly recommend upgrading to this version.

All data in the data/ directory (conversations, config, knowledge base) does not need migration; just replace the source files.

👤 Maintainer
Arch712（@ARCH0712）

QQ：1658796850

Bilibili：UID3493123588098281

Email：LuoTianyi0712-@outlook.com

Enjoy a stable and faster AstrBot! 🎉

