

```markdown
# ⚡ AstrBot v4.27.2-Async-Stable

**极致异步性能优化版 · 稳定修复版**

<br>

> [!CAUTION]
> **⚠️ 这不是官方版本！**
>
> 本仓库是 [AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot) 的**性能优化分支**。
> - **与官方后续版本不兼容**，执行 `git pull` 或直接覆盖升级将**丢失所有优化**。
> - 本分支适合**长期独立维护**。
> - 官方原版请移步：[AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot)

<br>

<div>
<img src="https://img.shields.io/badge/版本-4.27.2--Async--Stable-76bad9?style=flat-square&label=Release">
<img src="https://img.shields.io/badge/Python-3.12+-blue.svg" alt="python">
<img src="https://img.shields.io/badge/状态-稳定-brightgreen?style=flat-square">
</div>

---

## 📌 这个版本是什么？

**v4.27.2-Async-Stable** 是基于官方 v4.27.2 的性能优化分支，在保留全部核心优化的基础上，**修复了 v4.27.2-Async 版本中私聊回复速度异常缓慢的问题**。

### 核心变更

| 类型 | 文件数 | 说明 |
|------|--------|------|
| **核心优化（保留）** | 10 个 | 数据库、I/O、知识库、消息组件等深度优化 |
| **场景识别（新增）** | 3 个 | 群聊/私聊场景判断，为私聊轻量化奠定基础 |
| **私聊加速（新增）** | 已在上述 3 个文件中实现 | 根据场景自动调整 Agent 参数 |
| **适配器回退** | 2 个 | `aiocqhttp` 适配器回退官方版，保证消息收发稳定 |

---

## 🚀 包含的优化文件清单

以下 **10 个核心文件** 保留自 v4.27.2-Async 的全部优化：

| 文件路径 | 优化内容 |
|----------|----------|
| `astrbot/core/message/components.py` | 统一文件源解析、URI 解码缓存（LRU 128） |
| `astrbot/core/message/message_event_result.py` | 生成器替换列表推导、`squash_plain` 提前返回 |
| `astrbot/core/utils/io.py` | SSL 上下文复用、64KB 缓冲写入、进度回调降频 |
| `astrbot/core/utils/llm_metadata.py` | 内存+本地文件缓存（TTL 1h）、10s 超时、降级警告 |
| `astrbot/core/db/sqlite.py` | 新增 3 个复合索引、`get_session_conversations` 两步查询 |
| `astrbot/core/db/vec_db/faiss_impl/vec_db.py` | 向量归一化 NumPy 向量化、局部变量引用优化 |
| `astrbot/core/knowledge_base/kb_mgr.py` | 懒加载、并发检索（`asyncio.gather`）、LRU 结果缓存 |
| `astrbot/core/backup/exporter.py` | 分页导出（1000 条/页）、zip 写入 / `os.walk` 移至线程池 |
| `astrbot/core/platform/sources/webchat/webchat_adapter.py` | 并发推送（`asyncio.gather`）、消息历史 TTL 缓存 |
| `astrbot/__init__.py` | **（未修改，保留官方版本号）** |

---

## 🔧 新增：私聊场景识别与加速（3 个文件）

为修复私聊回复慢的问题，新增了场景判断机制，让 Agent 在私聊时自动采用轻量化配置：

| 文件路径 | 改动说明 |
|----------|----------|
| `astrbot/core/platform/astr_message_event.py` | 新增 `is_private_chat()` / `is_group_chat()` 方法，支持多平台 session_id 兜底判断 |
| `astrbot/core/platform/platform.py` | 新增 `is_group_session()` 静态方法，统一场景判断逻辑 |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | 支持 `is_private_chat` 参数，私聊时自动限制最大步数（3）、重试次数（2）、上下文轮数（10） |

---

## 🔄 回退到官方版本的文件（2 个）

为保证消息收发稳定性，以下文件回退至官方 v4.27.2 版本：

| 文件路径 | 回退原因 |
|----------|----------|
| `astrbot/core/platform/sources/aiocqhttp/aiocqhttp_message_event.py` | 适配器优化引入消息收发异常，回退后恢复稳定 |
| `astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py` | 同上 |

> 回退后，QQ 平台的私聊和群聊消息收发功能完全恢复正常，日志正常输出。

---

## 🐛 修复的问题

### 私聊回复速度异常缓慢
在 v4.27.2-Async 中，私聊场景下 Agent 使用了与群聊相同的完整配置（上下文窗口大、工具调用轮数多、重试次数多），导致响应时间显著延长。

**本版本修复方式：**
- 在 `AstrMessageEvent` 和 `Platform` 基类中增加场景判断方法
- Agent 执行器（`ToolLoopAgentRunner`）根据 `is_private_chat` 标志动态调整：
  - 工具调用最大轮数：私聊降至 3 轮
  - 重试次数：私聊降至 2 次
  - 上下文保留轮数：私聊限制为 10 轮
- 群聊配置保持不变，不受影响

**效果：私聊回复速度恢复至正常水平，群聊性能不受影响。**

---

## 📦 安装方法

### 方式一：下载 ZIP 包（推荐）
1. 前往 [Releases](https://github.com/ARCH0712/AstrBot-Async/releases) 页面
2. 下载 `Source code (zip)` 并解压
3. 在解压目录执行：
```bash
uv tool install . --python 3.12
astrbot init   # 首次运行初始化
astrbot run
```

### 方式二：Git 克隆
```bash
git clone -b v4.27.2-Async-Stable https://github.com/ARCH0712/AstrBot-Async.git
cd AstrBot-Async
uv tool install . --python 3.12
astrbot init
astrbot run
```

---

## ⚠️ 更新风险与补救策略

| 你的操作 | 后果 | 补救方案 |
|----------|------|----------|
| 直接 `git pull` 官方仓库 | 所有优化被覆盖，性能回退 | 用本分支代码重新覆盖 `astrbot/core/` 目录 |
| 覆盖升级到官方新 Release | 优化丢失，新索引不会被创建 | 保留旧版 `data/`，手动合入优化 |
| 想合并官方新功能到优化版 | 可能产生代码冲突 | 使用 `git diff > patch`，新版本上 `git apply` |

**建议**：将此分支视为独立项目长期维护。

---

## 👤 作者与联系方式

**维护者**：Arch712（@ARCH0712）

- 💬 QQ：1658796850
- 📺 B站：[UID3493123588098281](https://space.bilibili.com/3493123588098281)
- 📧 Email：LuoTianyi0712-@outlook.com
- 🏠 GitHub：[ARCH0712](https://github.com/ARCH0712)

---

## 📄 版权声明

本仓库所有代码基于 [AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot) 修改，遵循 **MIT 协议**。

- 官方版权归 AstrBotDevs 所有
- 优化分支版权归 Arch712 所有

---

<div align="center">

_高性能，永不止步。_

<img src="https://files.astrbot.app/watashiwa-koseino-desukara.gif" width="100"/>

</div>
```
