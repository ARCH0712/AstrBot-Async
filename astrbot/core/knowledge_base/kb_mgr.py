import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from astrbot.core import logger
from astrbot.core.provider.manager import ProviderManager
from astrbot.core.utils.astrbot_path import get_astrbot_knowledge_base_path

from .chunking.recursive import RecursiveCharacterChunker
from .kb_db_sqlite import KBSQLiteDatabase
from .kb_helper import KBHelper
from .models import KBDocument, KnowledgeBase
from .retrieval.manager import RetrievalManager, RetrievalResult
from .retrieval.rank_fusion import RankFusion
from .retrieval.sparse_retriever import SparseRetriever

FILES_PATH = get_astrbot_knowledge_base_path()
DB_PATH = Path(FILES_PATH) / "kb.db"
CHUNKER = RecursiveCharacterChunker()


class KnowledgeBaseManager:
    kb_db: KBSQLiteDatabase
    retrieval_manager: RetrievalManager

    def __init__(
        self,
        provider_manager: ProviderManager,
    ) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.provider_manager = provider_manager
        self._session_deleted_callback_registered = False

        # ========== 优化点1：延迟加载，启动时不加载所有知识库 ==========
        self.kb_insts: dict[str, KBHelper] = {}
        self._loading_lock = asyncio.Lock()
        self._loaded = False  # 标记是否已加载过

        # ========== 优化点2：增加简单查询缓存（LRU） ==========
        self._retrieve_cache: Dict[str, List[RetrievalResult]] = {}
        self._cache_max_size = 128  # 最大缓存条目数

    async def initialize(self) -> None:
        """初始化知识库模块（仅初始化数据库和检索器，不加载知识库实例）"""
        try:
            await self._init_kb_database()
            # 初始化检索管理器（只依赖数据库，不依赖具体实例）
            sparse_retriever = SparseRetriever(self.kb_db)
            rank_fusion = RankFusion(self.kb_db)
            self.retrieval_manager = RetrievalManager(
                sparse_retriever=sparse_retriever,
                rank_fusion=rank_fusion,
                kb_db=self.kb_db,
            )
            # 不再立即加载所有知识库
            logger.info("KnowledgeBaseManager initialized (lazy loading enabled)")
        except ImportError as e:
            logger.error(f"知识库模块导入失败: {e}")
            logger.warning("请确保已安装所需依赖: pypdf, aiofiles, Pillow, rank-bm25")
        except Exception as e:
            logger.error(f"知识库模块初始化失败: {e}", exc_info=True)

    async def _init_kb_database(self) -> None:
        self.kb_db = KBSQLiteDatabase(DB_PATH.as_posix())
        await self.kb_db.initialize()
        await self.kb_db.migrate_to_v1()
        logger.info(f"KnowledgeBase database initialized: {DB_PATH}")

    # ========== 优化点3：懒加载知识库实例 ==========
    async def _ensure_loaded(self) -> None:
        """确保所有知识库实例已加载（仅首次调用时执行）"""
        if self._loaded:
            return
        async with self._loading_lock:
            if self._loaded:
                return
            kb_records = await self.kb_db.list_kbs()
            for record in kb_records:
                kb_helper = KBHelper(
                    kb_db=self.kb_db,
                    kb=record,
                    provider_manager=self.provider_manager,
                    kb_root_dir=FILES_PATH,
                    chunker=CHUNKER,
                )
                try:
                    await kb_helper.initialize()
                except Exception as e:
                    kb_helper.init_error = str(e)
                    logger.error(
                        f"知识库 {record.kb_name}({record.kb_id}) 初始化失败: {e}",
                        exc_info=True,
                    )
                self.kb_insts[record.kb_id] = kb_helper
            self._loaded = True
            logger.info(f"Loaded {len(self.kb_insts)} knowledge bases")

    async def create_kb(
        self,
        kb_name: str,
        description: str | None = None,
        emoji: str | None = None,
        embedding_provider_id: str | None = None,
        rerank_provider_id: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        top_k_dense: int | None = None,
        top_k_sparse: int | None = None,
        top_m_final: int | None = None,
    ) -> KBHelper:
        """创建新的知识库实例"""
        if embedding_provider_id is None:
            raise ValueError("创建知识库时必须提供embedding_provider_id")
        # 预先检查名称是否已存在
        existing = await self.kb_db.get_kb_by_name(kb_name)
        if existing:
            raise ValueError(f"知识库名称 '{kb_name}' 已存在")

        kb = KnowledgeBase(
            kb_name=kb_name,
            description=description,
            emoji=emoji or "📚",
            embedding_provider_id=embedding_provider_id,
            rerank_provider_id=rerank_provider_id,
            chunk_size=chunk_size if chunk_size is not None else 512,
            chunk_overlap=chunk_overlap if chunk_overlap is not None else 50,
            top_k_dense=top_k_dense if top_k_dense is not None else 50,
            top_k_sparse=top_k_sparse if top_k_sparse is not None else 50,
            top_m_final=top_m_final if top_m_final is not None else 5,
        )
        try:
            async with self.kb_db.get_db() as session:
                session.add(kb)
                await session.flush()

                kb_helper = KBHelper(
                    kb_db=self.kb_db,
                    kb=kb,
                    provider_manager=self.provider_manager,
                    kb_root_dir=FILES_PATH,
                    chunker=CHUNKER,
                )
                await kb_helper.initialize()
                await session.commit()
                self.kb_insts[kb.kb_id] = kb_helper
                self._loaded = True  # 至少有一个已加载
                return kb_helper
        except IntegrityError as e:
            logger.exception("创建知识库失败：唯一约束冲突")
            raise ValueError(f"知识库名称 '{kb_name}' 已存在") from e
        except Exception:
            logger.exception("创建知识库失败")
            raise

    async def get_kb(self, kb_id: str) -> KBHelper | None:
        """获取知识库实例（自动懒加载）"""
        await self._ensure_loaded()
        return self.kb_insts.get(kb_id)

    async def get_kb_by_name(self, kb_name: str) -> KBHelper | None:
        """通过名称获取知识库实例（自动懒加载）"""
        await self._ensure_loaded()
        for kb_helper in self.kb_insts.values():
            if kb_helper.kb.kb_name == kb_name:
                return kb_helper
        return None

    async def delete_kb(self, kb_id: str) -> bool:
        """删除知识库实例"""
        await self._ensure_loaded()
        kb_helper = self.kb_insts.get(kb_id)
        if not kb_helper:
            return False

        await kb_helper.delete_vec_db()
        async with self.kb_db.get_db() as session:
            await session.delete(kb_helper.kb)
            await session.commit()

        self.kb_insts.pop(kb_id, None)
        # 清除缓存中与该KB相关的条目
        self._retrieve_cache = {k: v for k, v in self._retrieve_cache.items() if not any(r.kb_id == kb_id for r in v)}
        return True

    async def list_kbs(self) -> list[KnowledgeBase]:
        """列出所有知识库实例（自动懒加载）"""
        await self._ensure_loaded()
        return [kb_helper.kb for kb_helper in self.kb_insts.values()]

    async def update_kb(
        self,
        kb_id: str,
        kb_name: str,
        description: str | None = None,
        emoji: str | None = None,
        embedding_provider_id: str | None = None,
        rerank_provider_id: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        top_k_dense: int | None = None,
        top_k_sparse: int | None = None,
        top_m_final: int | None = None,
    ) -> KBHelper | None:
        """更新知识库实例"""
        await self._ensure_loaded()
        kb_helper = await self.get_kb(kb_id)
        if not kb_helper:
            return None

        kb = kb_helper.kb
        previous_state = {
            "kb_name": kb.kb_name,
            "description": kb.description,
            "emoji": kb.emoji,
            "embedding_provider_id": kb.embedding_provider_id,
            "rerank_provider_id": kb.rerank_provider_id,
            "chunk_size": kb.chunk_size,
            "chunk_overlap": kb.chunk_overlap,
            "top_k_dense": kb.top_k_dense,
            "top_k_sparse": kb.top_k_sparse,
            "top_m_final": kb.top_m_final,
        }
        previous_init_error = kb_helper.init_error

        if kb_name is not None:
            kb.kb_name = kb_name
        if description is not None:
            kb.description = description
        if emoji is not None:
            kb.emoji = emoji
        if embedding_provider_id is not None:
            kb.embedding_provider_id = embedding_provider_id
        kb.rerank_provider_id = rerank_provider_id
        if chunk_size is not None:
            kb.chunk_size = chunk_size
        if chunk_overlap is not None:
            kb.chunk_overlap = chunk_overlap
        if top_k_dense is not None:
            kb.top_k_dense = top_k_dense
        if top_k_sparse is not None:
            kb.top_k_sparse = top_k_sparse
        if top_m_final is not None:
            kb.top_m_final = top_m_final

        new_helper = KBHelper(
            kb_db=self.kb_db,
            kb=kb,
            provider_manager=self.provider_manager,
            kb_root_dir=FILES_PATH,
            chunker=CHUNKER,
        )

        try:
            await new_helper.initialize()
        except Exception as e:
            # 回滚设置
            kb.kb_name = previous_state["kb_name"]
            kb.description = previous_state["description"]
            kb.emoji = previous_state["emoji"]
            kb.embedding_provider_id = previous_state["embedding_provider_id"]
            kb.rerank_provider_id = previous_state["rerank_provider_id"]
            kb.chunk_size = previous_state["chunk_size"]
            kb.chunk_overlap = previous_state["chunk_overlap"]
            kb.top_k_dense = previous_state["top_k_dense"]
            kb.top_k_sparse = previous_state["top_k_sparse"]
            kb.top_m_final = previous_state["top_m_final"]
            kb_helper.init_error = previous_init_error
            logger.error(
                f"知识库 {kb.kb_name}({kb.kb_id}) 重新初始化失败，继续使用旧实例: {e}",
                exc_info=True,
            )
            return kb_helper

        async with self.kb_db.get_db() as session:
            session.add(kb)
            await session.commit()
            await session.refresh(kb)

        old_helper = kb_helper
        self.kb_insts[kb_id] = new_helper
        await old_helper.terminate()
        new_helper.init_error = None
        # 清除缓存
        self._retrieve_cache = {k: v for k, v in self._retrieve_cache.items() if not any(r.kb_id == kb_id for r in v)}
        return new_helper

    # ========== 优化点4：检索时并发执行各KB的检索，并增加结果缓存 ==========
    @lru_cache(maxsize=128)
    def _get_cache_key(self, query: str, kb_names_tuple: tuple, top_k_fusion: int, top_m_final: int) -> str:
        """生成缓存键（使用lru_cache自动管理）"""
        return f"{query}|{kb_names_tuple}|{top_k_fusion}|{top_m_final}"

    async def retrieve(
        self,
        query: str,
        kb_names: list[str],
        top_k_fusion: int = 20,
        top_m_final: int = 5,
        use_cache: bool = True,
    ) -> dict | None:
        """从指定知识库中检索相关内容（支持缓存和并发）"""
        # 生成缓存键
        cache_key = self._get_cache_key(query, tuple(sorted(kb_names)), top_k_fusion, top_m_final)

        if use_cache and cache_key in self._retrieve_cache:
            logger.debug(f"Retrieve cache hit for query: {query[:30]}...")
            cached_results = self._retrieve_cache[cache_key]
            # 重建返回结构（需要重新格式化上下文）
            return self._build_response(cached_results)

        # 确保已加载
        await self._ensure_loaded()

        # 收集可用的KB
        kb_ids = []
        kb_id_helper_map = {}
        unavailable_kbs = []
        for kb_name in kb_names:
            kb_helper = await self.get_kb_by_name(kb_name)
            if kb_helper:
                if kb_helper.init_error:
                    unavailable_kbs.append((kb_name, kb_helper.init_error))
                    logger.warning(f"知识库 {kb_name} 不可用: {kb_helper.init_error}")
                    continue
                kb_ids.append(kb_helper.kb.kb_id)
                kb_id_helper_map[kb_helper.kb.kb_id] = kb_helper
            else:
                unavailable_kbs.append((kb_name, "未找到"))

        if not kb_ids and unavailable_kbs:
            errors = "; ".join(f"{n}: {e}" for n, e in unavailable_kbs)
            raise ValueError(f"所有请求的知识库均不可用: {errors}")

        if not kb_ids:
            return {}

        # ========== 优化点5：并发执行检索（每个KB独立检索） ==========
        tasks = []
        for kb_id in kb_ids:
            helper = kb_id_helper_map[kb_id]
            # 为每个KB单独检索，使用其自身的检索参数
            task = self.retrieval_manager.retrieve_for_kb(
                query=query,
                kb_id=kb_id,
                kb_helper=helper,
                top_k_fusion=top_k_fusion,
                top_m_final=top_m_final,
            )
            tasks.append(task)

        # 并发执行所有检索任务
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        # 合并结果
        all_results = []
        for res in results_list:
            if isinstance(res, Exception):
                logger.warning(f"检索过程中出现异常: {res}")
                continue
            if res:
                all_results.extend(res)

        # 排序和截断
        all_results.sort(key=lambda x: x.score, reverse=True)
        final_results = all_results[:top_m_final] if top_m_final else all_results

        # 缓存结果
        if use_cache:
            if len(self._retrieve_cache) >= self._cache_max_size:
                # 简单清除一半（LRU近似）
                keys = list(self._retrieve_cache.keys())
                for k in keys[:len(keys)//2]:
                    del self._retrieve_cache[k]
            self._retrieve_cache[cache_key] = final_results

        return self._build_response(final_results)

    def _build_response(self, results: List[RetrievalResult]) -> dict:
        """构建返回响应结构"""
        if not results:
            return None

        context_text = self._format_context(results)
        results_dict = [
            {
                "chunk_id": r.chunk_id,
                "doc_id": r.doc_id,
                "kb_id": r.kb_id,
                "kb_name": r.kb_name,
                "doc_name": r.doc_name,
                "chunk_index": r.metadata.get("chunk_index", 0),
                "content": r.content,
                "score": r.score,
                "char_count": r.metadata.get("char_count", 0),
            }
            for r in results
        ]
        return {
            "context_text": context_text,
            "results": results_dict,
        }

    def _format_context(self, results: list[RetrievalResult]) -> str:
        """格式化知识上下文（优化字符串拼接）"""
        # 预分配列表容量
        lines = ["以下是相关的知识库内容,请参考这些信息回答用户的问题:\n"]
        for i, result in enumerate(results, 1):
            lines.append(f"【知识 {i}】")
            lines.append(f"来源: {result.kb_name} / {result.doc_name}")
            lines.append(f"内容: {result.content}")
            lines.append(f"相关度: {result.score:.2f}")
            lines.append("")
        return "\n".join(lines)

    async def terminate(self) -> None:
        """终止所有知识库实例,关闭数据库连接"""
        # 使用并发关闭多个helper
        tasks = []
        for kb_id, kb_helper in self.kb_insts.items():
            tasks.append(kb_helper.terminate())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self.kb_insts.clear()
        self._retrieve_cache.clear()

        if hasattr(self, "kb_db") and self.kb_db:
            try:
                await self.kb_db.close()
            except Exception as e:
                logger.error(f"关闭知识库元数据数据库失败: {e}")

    async def upload_from_url(
        self,
        kb_id: str,
        url: str,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        batch_size: int = 32,
        tasks_limit: int = 3,
        max_retries: int = 3,
        progress_callback=None,
    ) -> KBDocument:
        """从 URL 上传文档到指定的知识库"""
        kb_helper = await self.get_kb(kb_id)
        if not kb_helper:
            raise ValueError(f"Knowledge base with id {kb_id} not found.")

        return await kb_helper.upload_from_url(
            url=url,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            batch_size=batch_size,
            tasks_limit=tasks_limit,
            max_retries=max_retries,
            progress_callback=progress_callback,
        )
