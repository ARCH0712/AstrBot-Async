![AstrBot-Logo-Simplified](https://github.com/user-attachments/assets/36fb04e4-cc75-4454-bd8b-049d11aa86f9)

<div align="center">

# ⚡ AstrBot v4.27.2-Async

**极致异步性能优化版 · 非官方分支**

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
<img src="https://img.shields.io/badge/版本-4.27.2--Async-76bad9?style=flat-square&label=Release">
<img src="https://img.shields.io/badge/Python-3.12+-blue.svg" alt="python">
<img src="https://img.shields.io/badge/状态-稳定-brightgreen?style=flat-square">
<img src="https://img.shields.io/github/license/ARCH0712/AstrBot-Async?color=76bad9&style=flat-square">
</div>

<br>

<a href="https://github.com/ARCH0712/AstrBot-Async/releases">📦 下载 Releases</a> ｜
<a href="https://github.com/ARCH0712/AstrBot-Async/issues">📝 反馈 Issues</a> ｜
<a href="https://github.com/AstrBotDevs/AstrBot">📖 官方文档</a>

</div>

---

## 📌 这个仓库是什么？

本仓库是 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的**非官方性能优化分支**，专注于**极致异步性能调优**。

相比官方版本，优化后：
- ✅ **CPU 占用更低**
- ✅ **内存占用更少**
- ✅ **消息响应更快**
- ✅ **启动速度更快**

你可以在保留 AstrBot 全部功能的前提下，获得更流畅的运行体验。

---

## 🚀 本分支优化亮点

| 模块 | 优化内容 |
|------|----------|
| **消息组件** | 统一文件源解析、URI 解码缓存、减少 `os.path.exists` 调用 |
| **数据库** | 新增复合索引、查询重写、减少无谓删除操作 |
| **文件 I/O** | SSL 上下文复用、64KB 缓冲写入、进度回调降频 |
| **知识库** | 懒加载、并发检索、检索结果缓存（LRU） |
| **协议适配** | 合并文本段、流式输出列表拼接、并发推送、TTL 缓存 |
| **备份导出** | 分页查询、异步化 I/O、线程池遍历 |
| **LLM 元数据** | 内存+本地文件缓存、10s 超时、降级警告 |

详细改动请查看 [Release Notes](https://github.com/ARCH0712/AstrBot-Async/releases)。

---

## 📦 快速安装

### 方式一：直接下载 Release ZIP（推荐）
1. 前往 [Releases](https://github.com/ARCH0712/AstrBot-Async/releases) 页面
2. 下载 `Source code (zip)` 并解压
3. 在解压目录执行：
```bash
uv tool install . --python 3.12
astrbot init   # 首次运行初始化
astrbot run
方式二：通过 Git 克隆
bash
git clone -b v4.27.2-Async https://github.com/ARCH0712/AstrBot-Async.git
cd AstrBot-Async
uv tool install . --python 3.12
astrbot init
astrbot run
🔄 更新与补救策略
你的操作	后果	补救方案
直接 git pull 官方仓库	所有优化被覆盖，性能回退	用本分支代码重新覆盖 astrbot/core/ 目录
覆盖升级到官方新 Release	优化丢失，新索引不会被创建	保留旧版 data/，手动合入优化
想合并官方新功能到优化版	可能产生代码冲突	使用 git diff > patch，新版本上 git apply
建议：将此分支视为独立项目长期维护。

👤 作者与联系方式
维护者：Arch712（@ARCH0712）

💬 QQ：1658796850

📺 Bilibili：UID3493123588098281

📧 Email：LuoTianyi0712-@outlook.com

🏠 GitHub：ARCH0712

🙏 致谢
AstrBotDevs 提供的卓越开源项目

所有 AstrBot 官方 Contributor 和社区开发者

使用本分支的每一位用户 ❤️

📄 版权声明
本仓库所有代码基于 AstrBotDevs/AstrBot 修改，遵循 MIT 协议。

官方版权归 AstrBotDevs 所有

优化分支版权归 Arch712 所有

MIT License

Copyright (c) 2021 Lxns-Network（AstrBot 官方）
Copyright (c) 2026 Arch712（AstrBot-Async 优化分支）

<div align="center">
高性能，永不止步。

<img src="https://files.astrbot.app/watashiwa-koseino-desukara.gif" width="100"/></div> ```