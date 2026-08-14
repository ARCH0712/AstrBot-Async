import time
import uuid

import numpy as np

from astrbot import logger
from astrbot.core.exceptions import KnowledgeBaseUploadError
from astrbot.core.provider.provider import EmbeddingProvider, RerankProvider

from ..base import BaseVecDB, Result
from .document_storage import DocumentStorage
from .embedding_storage import EmbeddingStorage


class FaissVecDB(BaseVecDB):
    """A class to represent a vector database."""

    def __init__(
        self,
        doc_store_path: str,
        index_store_path: str,
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider | None = None,
    ) -> None:
        self.doc_store_path = doc_store_path
        self.index_store_path = index_store_path
        self.embedding_provider = embedding_provider
        self.document_storage = DocumentStorage(doc_store_path)
        self.embedding_storage = EmbeddingStorage(
            embedding_provider.get_dim(),
            index_store_path,
        )
        self.embedding_provider = embedding_provider
        self.rerank_provider = rerank_provider

    async def initialize(self) -> None:
        await self.document_storage.initialize()

    async def insert(
        self,
        content: str,
        metadata: dict | None = None,
        id: str | None = None,
    ) -> int:
        """插入一条文本和其对应向量，自动生成 ID 并保持一致性。"""
        metadata = metadata or {}
        str_id = id or str(uuid.uuid4())

        vector = await self.embedding_provider.get_embedding(content)
        vector = np.array(vector, dtype=np.float32)

        int_id = await self.document_storage.insert_document(str_id, content, metadata)
        await self.embedding_storage.insert(vector, int_id)
        return int_id

    async def insert_batch(
        self,
        contents: list[str],
        metadatas: list[dict] | None = None,
        ids: list[str] | None = None,
        batch_size: int = 32,
        tasks_limit: int = 3,
        max_retries: int = 3,
        progress_callback=None,
        embedding_contents: list[str] | None = None,
    ) -> list[int]:
        """批量插入文本和其对应向量，自动生成 ID 并保持一致性。"""
        metadatas = metadatas or [{} for _ in contents]
        ids = ids or [str(uuid.uuid4()) for _ in contents]
        if embedding_contents is None:
            embedding_contents = contents

        content_count = len(contents)
        if not content_count:
            logger.debug("No contents provided for batch insert; skipping.")
            return []

        # 验证长度一致性（保持不变）
        if len(metadatas) != content_count:
            raise KnowledgeBaseUploadError(
                stage="storage",
                user_message=(
                    f"存储失败：文本分块数量与元数据数量不一致（期望 {content_count}，"
                    f"实际 {len(metadatas)}）。"
                ),
                details={"expected": content_count, "actual": len(metadatas)},
            )
        if len(ids) != content_count:
            raise KnowledgeBaseUploadError(
                stage="storage",
                user_message=(
                    f"存储失败：文本分块数量与文档 ID 数量不一致（期望 {content_count}，"
                    f"实际 {len(ids)}）。"
                ),
                details={"expected": content_count, "actual": len(ids)},
            )
        if len(embedding_contents) != content_count:
            raise KnowledgeBaseUploadError(
                stage="storage",
                user_message=(
                    f"存储失败：文本分块数量与向量化文本数量不一致（期望 {content_count}，"
                    f"实际 {len(embedding_contents)}）。"
                ),
                details={"expected": content_count, "actual": len(embedding_contents)},
            )

        # 生成向量
        start = time.time()
        logger.debug(f"Generating embeddings for {content_count} contents...")
        vectors = await self.embedding_provider.get_embeddings_batch(
            embedding_contents,
            batch_size=batch_size,
            tasks_limit=tasks_limit,
            max_retries=max_retries,
            progress_callback=progress_callback,
        )
        logger.debug(
            f"Generated embeddings in {time.time() - start:.2f}s for {content_count} items."
        )

        if len(vectors) != content_count:
            raise KnowledgeBaseUploadError(
                stage="embedding",
                user_message=(
                    f"向量化失败：嵌入模型返回的向量数量与文本分块数量不一致"
                    f"（期望 {content_count}，实际 {len(vectors)}）。"
                ),
                details={"expected": content_count, "actual": len(vectors)},
            )

        # ========== 优化点1：使用 np.asarray 并尽早验证维度 ==========
        try:
            vectors_array = np.asarray(vectors, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise KnowledgeBaseUploadError(
                stage="embedding",
                user_message="向量化失败：返回的向量格式不正确，无法转换为浮点矩阵。",
                details={"vector_count": len(vectors)},
            ) from exc

        if vectors_array.ndim != 2:
            raise KnowledgeBaseUploadError(
                stage="embedding",
                user_message="向量化失败：无法构造成二维向量矩阵。",
                details={"ndim": int(vectors_array.ndim)},
            )

        expected_dim = self.embedding_storage.dimension
        if vectors_array.shape[1] != expected_dim:
            raise KnowledgeBaseUploadError(
                stage="embedding",
                user_message=(
                    f"向量化失败：返回向量维度与索引维度不一致"
                    f"（期望 {expected_dim}，实际 {vectors_array.shape[1]}）。"
                ),
                details={"expected": expected_dim, "actual": int(vectors_array.shape[1])},
            )

        # ========== 优化点2：批量插入文档和向量，减少中间对象 ==========
        # 先写文档存储（可能失败），再写向量存储，若向量写入失败则回滚
        int_ids = await self.document_storage.insert_documents_batch(
            ids, contents, metadatas
        )
        if len(int_ids) != content_count:
            # 理论上不会发生，但保留检查
            raise KnowledgeBaseUploadError(
                stage="storage",
                user_message="内部 ID 数量与文本数量不一致。",
                details={"expected": content_count, "actual": len(int_ids)},
            )

        try:
            await self.embedding_storage.insert_batch(vectors_array, int_ids)
        except Exception:
            await self._rollback_partial_insert(ids=ids, int_ids=int_ids)
            raise
        return int_ids

    async def retrieve(
        self,
        query: str,
        k: int = 5,
        fetch_k: int = 20,
        rerank: bool = False,
        metadata_filters: dict | None = None,
    ) -> list[Result]:
        """搜索最相似的文档。"""
        # ========== 优化点3：提前获取局部变量引用，减少属性查找 ==========
        emb_storage = self.embedding_storage
        doc_storage = self.document_storage
        rerank_provider = self.rerank_provider

        # 获取查询向量
        embedding = await self.embedding_provider.get_embedding(query)
        query_vec = np.asarray(embedding, dtype=np.float32)

        # 搜索向量索引
        search_k = fetch_k if metadata_filters else k
        scores, indices = await emb_storage.search(query_vec, k=search_k)

        if len(indices[0]) == 0 or indices[0][0] == -1:
            return []

        # ========== 优化点4：归一化分数 ==========
        # 原归一化：1.0 - (scores / 2.0)，将余弦距离从[0,2]映射到[1,0]（相似度越大越好）
        # 但注意：如果返回的是余弦距离，距离越小相似度越高，归一化后分数越高越好
        # 如果scores已经是余弦相似度（-1到1），则归一化应为 (scores + 1)/2
        # 此处保留原逻辑但使用向量化操作加快
        scores_norm = 1.0 - (scores / 2.0)  # 形状 (1, N)

        # 获取文档
        doc_ids = indices[0].tolist()
        fetched_docs = await doc_storage.get_documents(
            metadata_filters=metadata_filters or {},
            ids=doc_ids,
        )
        if not fetched_docs:
            return []

        # ========== 优化点5：使用字典映射 id->index，避免多次查找 ==========
        id_to_pos = {doc["id"]: idx for idx, doc in enumerate(fetched_docs)}
        results = []
        # 直接遍历索引和分数
        for idx_id, score in zip(indices[0], scores_norm[0]):
            pos = id_to_pos.get(idx_id)
            if pos is None:
                continue
            doc = fetched_docs[pos]
            results.append(Result(similarity=float(score), data=doc))

        # 取前 k 个
        top_k_results = results[:k]

        # 可选重排序
        if rerank and rerank_provider:
            documents = [doc.data["text"] for doc in top_k_results]
            if documents:
                reranked = await rerank_provider.rerank(query, documents)
                reranked.sort(key=lambda x: x.relevance_score, reverse=True)
                # 按重排序后的顺序重新排列
                top_k_results = [top_k_results[r.index] for r in reranked]

        return top_k_results

    async def _rollback_partial_insert(
        self,
        ids: list[str],
        int_ids: list[int],
    ) -> None:
        """回滚部分插入（异常时尽力清理）。"""
        if int_ids:
            try:
                await self.embedding_storage.delete(int_ids)
            except Exception as exc:
                logger.warning(f"回滚 FAISS 向量失败: {exc}")

        for doc_id in ids:
            try:
                await self.document_storage.delete_document_by_doc_id(doc_id)
            except Exception as exc:
                logger.warning(f"回滚文档存储条目失败 {doc_id}: {exc}")

    async def delete(self, doc_id: str) -> None:
        """删除一条文档块（chunk）"""
        result = await self.document_storage.get_document_by_doc_id(doc_id)
        if not result:
            return
        int_id = result["id"]
        await self.document_storage.delete_document_by_doc_id(doc_id)
        await self.embedding_storage.delete([int_id])

    async def close(self) -> None:
        await self.document_storage.close()

    async def count_documents(self, metadata_filter: dict | None = None) -> int:
        return await self.document_storage.count_documents(
            metadata_filters=metadata_filter or {}
        )

    async def delete_documents(self, metadata_filters: dict) -> None:
        docs = await self.document_storage.get_documents(
            metadata_filters=metadata_filters,
            offset=None,
            limit=None,
        )
        doc_ids = [doc["id"] for doc in docs]
        if doc_ids:
            await self.embedding_storage.delete(doc_ids)
            await self.document_storage.delete_documents(
                metadata_filters=metadata_filters
            )
