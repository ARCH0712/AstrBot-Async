import asyncio
import json
import time
from pathlib import Path
from typing import Literal, TypedDict

import aiohttp

from astrbot.core import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.utils.http_ssl import build_tls_connector


class LLMModalities(TypedDict):
    input: list[Literal["text", "image", "audio", "video"]]
    output: list[Literal["text", "image", "audio", "video"]]


class LLMLimit(TypedDict):
    context: int
    output: int


class LLMMetadata(TypedDict):
    id: str
    reasoning: bool
    tool_call: bool
    knowledge: str
    release_date: str
    modalities: LLMModalities
    open_weights: bool
    limit: LLMLimit


LLM_METADATAS: dict[str, LLMMetadata] = {}

# ==================== 优化新增：缓存控制 ====================
_CACHE_TTL = 3600  # 缓存有效期（秒），默认 1 小时
_last_fetch_time: float = 0
_update_lock = asyncio.Lock()
_CACHE_FILE = Path(get_astrbot_data_path()) / "llm_metadata_cache.json"


async def _load_from_local_cache() -> dict[str, LLMMetadata] | None:
    """从本地缓存文件加载元数据，若文件不存在或损坏则返回 None。"""
    try:
        if _CACHE_FILE.exists():
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 简单校验结构
                if isinstance(data, dict) and data:
                    return data
    except Exception as e:
        logger.warning(f"加载本地 LLM 元数据缓存失败: {e}")
    return None


async def _save_to_local_cache(data: dict[str, LLMMetadata]) -> None:
    """保存元数据到本地缓存文件。"""
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存 LLM 元数据缓存失败: {e}")


async def update_llm_metadata(force_refresh: bool = False) -> None:
    """
    更新 LLM 元数据。若缓存未过期则直接使用缓存，否则从远程获取。
    可传入 force_refresh=True 强制刷新。
    """
    global LLM_METADATAS, _last_fetch_time

    # 检查内存缓存是否有效
    if not force_refresh and _last_fetch_time > 0:
        elapsed = time.time() - _last_fetch_time
        if elapsed < _CACHE_TTL and LLM_METADATAS:
            logger.debug(f"使用内存缓存的 LLM 元数据 (剩余 {_CACHE_TTL - elapsed:.0f}s)")
            return

    # 尝试加载本地缓存（仅在内存缓存无效时）
    if not force_refresh:
        local_data = await _load_from_local_cache()
        if local_data:
            # 检查本地缓存时间（通过文件修改时间）
            try:
                mtime = _CACHE_FILE.stat().st_mtime
                if time.time() - mtime < _CACHE_TTL:
                    LLM_METADATAS.clear()
                    LLM_METADATAS.update(local_data)
                    _last_fetch_time = mtime
                    logger.info(f"从本地缓存加载了 {len(local_data)} 个 LLM 元数据")
                    return
            except Exception:
                pass

    # 需要远程获取（加锁防止并发）
    async with _update_lock:
        # 双重检查：可能其他协程已经更新
        if not force_refresh and _last_fetch_time > 0:
            elapsed = time.time() - _last_fetch_time
            if elapsed < _CACHE_TTL and LLM_METADATAS:
                return

        logger.info("正在从远程获取 LLM 元数据...")
        url = "https://models.dev/api.json"
        try:
            timeout = aiohttp.ClientTimeout(total=10)  # 10秒超时
            async with aiohttp.ClientSession(
                trust_env=True,
                connector=build_tls_connector(),
                timeout=timeout,
            ) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.warning(f"获取 LLM 元数据失败，HTTP {response.status}，使用本地缓存（如有）")
                        return
                    data = await response.json()
                    models = {}
                    for info in data.values():
                        for model in info.get("models", {}).values():
                            model_id = model.get("id")
                            if not model_id:
                                continue
                            # 一次提取，减少多次 get 调用
                            models[model_id] = LLMMetadata(
                                id=model_id,
                                reasoning=model.get("reasoning", False),
                                tool_call=model.get("tool_call", False),
                                knowledge=model.get("knowledge", "none"),
                                release_date=model.get("release_date", ""),
                                modalities=model.get(
                                    "modalities", {"input": [], "output": []}
                                ),
                                open_weights=model.get("open_weights", False),
                                limit=model.get("limit", {"context": 0, "output": 0}),
                            )
                    # 更新全局缓存
                    LLM_METADATAS.clear()
                    LLM_METADATAS.update(models)
                    _last_fetch_time = time.time()
                    logger.info(f"成功获取 {len(models)} 个 LLM 元数据")
                    # 异步保存本地缓存（避免阻塞）
                    asyncio.create_task(_save_to_local_cache(models))
        except asyncio.TimeoutError:
            logger.warning("获取 LLM 元数据超时，使用本地缓存（如有）")
        except aiohttp.ClientError as e:
            logger.warning(f"获取 LLM 元数据网络错误: {e}，使用本地缓存（如有）")
        except Exception as e:
            logger.warning(f"获取 LLM 元数据失败: {e}，使用本地缓存（如有）")
