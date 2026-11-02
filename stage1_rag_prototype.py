#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
拾光连锁智能体系统 · 阶段 1 裸问答原型（无 Web）
====================================================

角色：资深 Python 后端架构师给出的【最小可运行原型】，承接《技术架构选型 V0.1》。
目标：在不引入 FastAPI / 数据库的前提下，跑通一条完整的 RAG 链路：

    读 Markdown 报销制度  →  500 字切片  →  向量化存入 FAISS
    →  命令行输入问题  →  BM25 + 向量 混合检索 Top3
    →  拼装提示词  →  调用（可配置供应商的）LLM 生成回答

用法示例
--------
    # 1) 用内置 mock 供应商（无需 API Key）快速验证整条链路
    python3.11 stage1_rag_prototype.py --markdown 报销制度.md --provider mock

    # 2) 接真实大模型（OpenAI 兼容端点，如 DeepSeek / 智谱 / 通义 / 本地 Ollama）
    python3.11 stage1_rag_prototype.py \
        --markdown 报销制度.md --provider openai-compatible \
        --base-url https://api.deepseek.com/v1 \
        --api-key sk-xxxx --model deepseek-chat

    # 重新构建索引（制度文档更新后）
    python3.11 stage1_rag_prototype.py --markdown 报销制度.md --rebuild

核心复用约定
------------
    代码里凡标注「【核心复用】」的函数 / 类，都是后续 Web 阶段会原样搬进
    技术架构选型里目录结构的位置：
        split_markdown_into_chunks   →  app/rag/ingest.py      （解析与分块）
        EmbeddingAdapter             →  app/rag/store.py       （可替换嵌入）
        FAISSKnowledgeStore          →  app/rag/store.py       （向量库读写）
        BM25Retriever                →  app/rag/retrieve.py    （关键词检索）
        hybrid_retrieve              →  app/rag/retrieve.py    （混合检索+重排）
        call_llm                     →  app/llm/gateway.py     （LLM 网关雏形）
        build_prompt                 →  app/agents/orchestrator.py（提示词编排）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import faiss
from rank_bm25 import BM25Okapi
import jieba
import requests


# =====================================================================
# 1. 配置（阶段 1 用 argparse + 默认值，Web 阶段替换为 pydantic-settings）
# =====================================================================
@dataclass
class Settings:
    """全局配置。Web 阶段会替换为 core/config.py 的 pydantic-settings。"""
    markdown_path: str = "报销制度.md"   # 本地报销制度文档
    chunk_size: int = 500                 # 切片长度（按「字/字符」计，用户明确要求 500）
    chunk_overlap: int = 50               # 滑动窗口重叠字数，避免切断语义
    embed_mode: str = "local"             # 嵌入后端：local（离线哈希）/ api（OpenAI 兼容 embeddings）
    embed_dim: int = 512                  # 向量维度（local 用 512；api 模式按模型返回维度）
    provider: str = "mock"                # LLM 供应商：mock（规则占位）/ openai-compatible
    base_url: str = ""                    # OpenAI 兼容端点，如 https://api.deepseek.com/v1
    api_key: str = ""                     # 供应商 API Key（Web 阶段走环境变量/Secret，不入库）
    model: str = "deepseek-chat"          # 模型名
    top_k: int = 3                        # 混合检索返回条数（用户要求 Top3）
    cache_dir: str = ".stage1_cache"      # FAISS 索引与元数据的持久化目录
    rebuild: bool = False                 # 是否强制重建索引


# =====================================================================
# 2. 文本切片  【核心复用】→ app/rag/ingest.py
# =====================================================================
def split_markdown_into_chunks(
    text: str, chunk_size: int = 500, overlap: int = 50
) -> List[Dict[str, str]]:
    """
    把 Markdown 文本切成若干「块」，每块约 chunk_size 字。

    设计要点（承接架构文档「按语义分块、保留章节上下文」）：
      1. 扫描行，遇到 #/##/### 标题则更新「当前章节名」，作为每块的前缀元数据；
      2. 按字符累积，超过 chunk_size 且遇到合适边界（句末/换行）时切一刀；
      3. 切分处保留 overlap 个字到下一块开头，缓解语义割裂；
      4. 返回结构含 {section, content, text}，text = section 前缀 + content，供检索用。

    这是后续 Web 阶段 ingest.py 的雏形：真实系统会复用同样的「章节前缀 + 滑动窗口」逻辑，
    只是把「字」换成「token」、把重叠策略参数化。
    """
    def first_heading(text: str) -> Optional[str]:
        """在一段文本里找「章节归属」标题。

        优先取 H2~H6 作为章节（H1 是文档主标题，不当作章节）；
        仅当块内没有 H2+ 时才回退到 H1。这样滑窗切片跨章节时，
        块归属到『块内首个真实章节』而非文档主标题，便于按章节/版本审计溯源。
        """
        for line in text.splitlines():
            m = re.match(r"^#{2,6}\s*(.+)$", line.strip())
            if m:
                return m.group(1).strip()
        for line in text.splitlines():
            m = re.match(r"^#{1,6}\s*(.+)$", line.strip())
            if m:
                return m.group(1).strip()
        return None

    lines = text.splitlines()
    chunks: List[Dict[str, str]] = []
    current_section = "总则"          # 未遇到标题前的默认章节
    buffer = ""                       # 当前累积的未切片文本

    # 把当前累积文本按 chunk_size 切分；每切出一块，buffer 保留尾部 overlap 字
    # 作为下一块开头（滑动窗口，避免切断语义）。这是后续 Web 阶段 ingest.py 的雏形。
    for line in lines:
        # 识别标题行，更新章节名（取第一个 # 后的文字）；标题行本身也并入 buffer
        m = re.match(r"^#{1,6}\s*(.+)$", line.strip())
        if m:
            current_section = m.group(1).strip()
        buffer += line + "\n"

        # 到达切分阈值就循环切，直到剩余不足 chunk_size
        while len(buffer) >= chunk_size:
            piece = buffer[:chunk_size]
            sec = first_heading(piece) or current_section
            chunks.append({
                "section": sec,
                # text 字段 = 章节前缀 + 正文，检索与向量化都用它
                "content": piece.strip(),
                "text": f"【{sec}】\n{piece.strip()}",
            })
            # 保留末尾 overlap 字进入下一块，复用上下文（重叠不为负）
            buffer = buffer[chunk_size - overlap:] if overlap < chunk_size else ""

    # 收尾：把不足 chunk_size 的剩余文本也归档为最后一块
    if buffer.strip():
        piece = buffer.strip()
        sec = first_heading(piece) or current_section
        chunks.append({
            "section": sec,
            "content": piece,
            "text": f"【{sec}】\n{piece}",
        })
    return chunks


# =====================================================================
# 3. 嵌入适配器  【核心复用】→ app/rag/store.py
# =====================================================================
class EmbeddingAdapter:
    """
    可替换的嵌入（Embedding）后端。架构文档要求「Embedding 可替换」。

    两种模式：
      - local：离线、零下载。用 jieba 中文分词 + 哈希词袋（hashing trick）生成
              固定维度稠密向量并 L2 归一化。语义性较弱，但配合 BM25 混合检索
              足够跑通原型；明确标注为「阶段 1 占位」，Web 阶段替换为 bge / 多语言
              模型等真实 embedding 服务（接口不变）。
      - api  ：调用 OpenAI 兼容的 /embeddings 端点（与 LLM 供应商同源，可配置），
              返回真实语义向量。沙箱无 Key 时默认走 local。
    """

    def __init__(self, mode: str = "local", dim: int = 512,
                 base_url: str = "", api_key: str = "", model: str = "text-embedding-3-small"):
        self.mode = mode
        self.dim = dim
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        # 预热 jieba 分词词典（首次分词较慢，预热避免首块卡顿）
        if mode == "local":
            jieba.lcut("预热分词")

    def _tokenize(self, text: str) -> List[str]:
        """中文分词：jieba 按词切，比按字切的 BM25/哈希质量更好。"""
        return [w for w in jieba.lcut(text) if w.strip()]

    def _local_embed(self, text: str) -> np.ndarray:
        """local 模式：哈希词袋向量 + L2 归一化。"""
        vec = np.zeros(self.dim, dtype=np.float32)
        for w in self._tokenize(text):
            # 用稳定哈希把词映射到固定维度的一维，避免随机初始化
            idx = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16) % self.dim
            vec[idx] += 1.0
        # L2 归一化后，FAISS 内积（IndexFlatIP）等价余弦相似度
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)

    def _api_embed(self, text: str) -> np.ndarray:
        """api 模式：调用 OpenAI 兼容 /embeddings。后续接真实 embedding 服务时复用此函数。"""
        resp = requests.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": text},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()["data"][0]["embedding"]
        self.dim = len(data)  # 同步真实维度，供 FAISS 建索引
        arr = np.array(data, dtype=np.float32)
        norm = np.linalg.norm(arr)
        return (arr / norm).astype(np.float32) if norm > 0 else arr

    def embed(self, text: str) -> np.ndarray:
        return self._api_embed(text) if self.mode == "api" else self._local_embed(text)

    def embed_batch(self, texts: List[str]) -> np.ndarray:
        """批量嵌入（原型数据量小，直接循环；Web 阶段改为并发/批请求）。"""
        return np.stack([self.embed(t) for t in texts]).astype(np.float32)


# =====================================================================
# 4. FAISS 知识库存储（构建 + 持久化 + 检索）
# =====================================================================
class FAISSKnowledgeStore:
    """
    把切片后的文本向量化并存入 FAISS，支持持久化到磁盘。

    用户要求「向量化存入 FAISS」，因此这里：
      - 构建时调用 EmbeddingAdapter 生成向量，建 IndexFlatIP（内积=余弦）索引；
      - 把索引（index.faiss）与元数据（chunks.pkl）落盘到缓存目录；
      - 下次启动若缓存存在且非 --rebuild，则直接加载，避免重复嵌入。
    Web 阶段会替换为 pgvector 的 store.py，但「build / persist / load / search」接口一致。
    """

    def __init__(self, settings: Settings, adapter: EmbeddingAdapter):
        self.settings = settings
        self.adapter = adapter
        os.makedirs(settings.cache_dir, exist_ok=True)
        self.index_path = os.path.join(settings.cache_dir, "index.faiss")
        self.meta_path = os.path.join(settings.cache_dir, "chunks.pkl")
        self.index: Optional[faiss.Index] = None
        self.chunks: List[Dict[str, str]] = []

    def build(self, chunks: List[Dict[str, str]]) -> None:
        """对全量切片做嵌入并建索引（阶段 1 为内存构建，Web 阶段可改流式/批处理）。"""
        self.chunks = chunks
        vectors = self.adapter.embed_batch([c["text"] for c in chunks])
        dim = vectors.shape[1]
        self.index = faiss.IndexFlatIP(dim)        # 内积索引（向量已归一化 → 等价余弦）
        self.index.add(vectors)
        self._persist()
        print(f"[FAISS] 已构建索引：{len(chunks)} 块，维度 {dim}")

    def _persist(self) -> None:
        """持久化索引与元数据。"""
        faiss.write_index(self.index, self.index_path)
        with open(self.meta_path, "wb") as f:
            pickle.dump(self.chunks, f)

    def load(self) -> bool:
        """从磁盘加载已有索引；成功返回 True。"""
        if not (os.path.exists(self.index_path) and os.path.exists(self.meta_path)):
            return False
        self.index = faiss.read_index(self.index_path)
        with open(self.meta_path, "rb") as f:
            self.chunks = pickle.load(f)
        print(f"[FAISS] 已加载缓存索引：{len(self.chunks)} 块")
        return True

    def search(self, query_vec: np.ndarray, top_k: int) -> Tuple[np.ndarray, np.ndarray]:
        """向量检索：返回 (相似度分数, 块索引)。"""
        scores, indices = self.index.search(query_vec.reshape(1, -1).astype(np.float32), top_k)
        return scores[0], indices[0]


# =====================================================================
# 5. BM25 关键词检索  【核心复用】→ app/rag/retrieve.py
# =====================================================================
class BM25Retriever:
    """
    基于 rank_bm25 的关键词检索，弥补纯向量检索在「发票、税号、额度」等
    精确术语上的召回短板（架构文档明确要 BM25 + 向量混合）。

    Web 阶段直接复用该类，仅把语料来源从内存列表换成向量库/ES 的文档字段。
    """

    def __init__(self, chunks: List[Dict[str, str]]):
        # 对每个块做中文分词，构造 BM25 语料
        self.chunks = chunks
        self.corpus = [jieba.lcut(c["text"]) for c in chunks]
        self.bm25 = BM25Okapi(self.corpus)

    def retrieve(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        """返回 [(块索引, 分数), ...] 按分数降序。"""
        tokens = jieba.lcut(query)
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(i, s) for i, s in ranked[:top_k] if s > 0]


# =====================================================================
# 6. 混合检索（向量 + BM25，RRF 融合）  【核心复用】→ app/rag/retrieve.py
# =====================================================================
def hybrid_retrieve(
    query: str,
    store: FAISSKnowledgeStore,
    bm25: BM25Retriever,
    adapter: EmbeddingAdapter,
    top_k: int = 3,
    candidate_k: int = 8,
    rrf_k: int = 60,
) -> List[Dict[str, str]]:
    """
    把向量检索与 BM25 检索的结果用 RRF（倒数排名融合）合并，取 Top-K。

    RRF 公式：score = Σ 1/(rrf_k + rank)，对两种召回的排名求和后再排序。
    好处是不依赖各自分数的量纲（向量相似度 vs BM25 分数不可比），鲁棒性好。
    这是 Web 阶段 retrieve.py 的雏形；真实系统会在融合后接 cross-encoder 重排。
    """
    q_vec = adapter.embed(query)

    # 向量侧：取候选数倍的近邻，记录排名
    v_scores, v_idx = store.search(q_vec, candidate_k)
    vector_rank: Dict[int, float] = {}
    for rank, idx in enumerate(v_idx):
        if idx != -1:
            vector_rank[idx] = 1.0 / (rrf_k + rank)

    # BM25 侧：取候选数倍，记录排名
    bm25_res = bm25.retrieve(query, candidate_k)
    bm25_rank: Dict[int, float] = {}
    for rank, (idx, _s) in enumerate(bm25_res):
        bm25_rank[idx] = 1.0 / (rrf_k + rank)

    # 融合：所有出现过的块索引，累加两侧 RRF 分数
    fused: Dict[int, float] = {}
    for idx, sc in list(vector_rank.items()) + list(bm25_rank.items()):
        fused[idx] = fused.get(idx, 0.0) + sc

    # 按融合分降序，取 Top-K
    top_indices = sorted(fused.keys(), key=lambda i: fused[i], reverse=True)[:top_k]
    return [store.chunks[i] for i in top_indices]


# =====================================================================
# 7. LLM 调用（可配置供应商）  【核心复用】→ app/llm/gateway.py
# =====================================================================
def call_llm(
    messages: List[Dict[str, str]],
    provider: str = "mock",
    base_url: str = "",
    api_key: str = "",
    model: str = "deepseek-chat",
) -> str:
    """
    LLM 调用封装成独立函数，供应商可配置（架构文档「LLM 网关」雏形）。

      - provider="mock"             ：离线规则占位，无需网络/Key，便于本地验证整条链路。
                                      真实回答由下方 openai-compatible 分支产生。
      - provider="openai-compatible"：向任意 OpenAI 兼容端点（DeepSeek/智谱/通义/Ollama）
                                      发 /chat/completions，业务侧不感知具体厂商。

    Web 阶段此函数升级为 LLMProvider 协议 + 降级链（主→备→本地小模型），接口签名保持一致。
    """
    if provider == "mock":
        # 占位实现（不调用真实大模型）：从提示词中抽取检索到的制度片段，
        # 做「抽取式要点摘要」，让无 Key 时也能直观看到 RAG 检索价值。
        # 真实语义生成由下方 openai-compatible 分支完成。
        user_msg = messages[-1]["content"]
        snippets = re.findall(
            r"\[(\d+)\] 来源章节：(.+?)\n(.*?)(?=\n\[\d+\]|\Z)", user_msg, re.S
        )
        out = ["（mock 模式占位：以下为检索到的制度要点摘要；接入真实 LLM 后将生成自然语言回答）"]
        for _num, sec, body in snippets:
            head = re.sub(r"\s+", " ", body.strip())[:120]
            out.append(f"[{_num}] 《{sec.strip()}》：{head}…")
        return "\n".join(out)

    # —— 真实供应商：OpenAI 兼容端点 ——
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": messages, "temperature": 0.2},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# =====================================================================
# 8. 提示词拼装  【核心复用】→ app/agents/orchestrator.py
# =====================================================================
def build_prompt(question: str, contexts: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    把检索到的片段拼进提示词。承接架构文档「每次建议保留资料版本与事实引用」。

    返回 OpenAI 兼容的 messages 列表（system + user），Web 阶段直接复用此结构。
    片段以「[1] 来源章节：xxx」编号，强制模型引用，便于后续审计与责任追溯。
    """
    system = (
        "你是「拾光连锁」报销制度问答助手。只能依据下面提供的制度片段回答，"
        "不得编造。回答时以「[编号]」标注所引用的制度片段，并说明对应章节。"
        "若片段不足以回答问题，明确告知用户「制度未覆盖该情形」，不要猜测。"
    )
    ctx_text = "\n\n".join(
        f"[{i+1}] 来源章节：{c['section']}\n{c['content']}"
        for i, c in enumerate(contexts)
    )
    user = f"问题：{question}\n\n可参考的制度片段如下：\n{ctx_text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# =====================================================================
# 9. 主流程：读文档 → 切片 → 建/载 FAISS → 命令行问答循环
# =====================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="拾光连锁 · 阶段1 裸问答 RAG 原型")
    parser.add_argument("--markdown", default="报销制度.md", help="本地报销制度 Markdown 路径")
    parser.add_argument("--embed", default="local", choices=["local", "api"], help="嵌入后端")
    parser.add_argument("--provider", default="mock", choices=["mock", "openai-compatible"], help="LLM 供应商")
    parser.add_argument("--base-url", default="", help="OpenAI 兼容端点（api 嵌入 / LLM 共用）")
    parser.add_argument("--api-key", default="", help="供应商 API Key")
    parser.add_argument("--model", default="deepseek-chat", help="LLM 模型名")
    parser.add_argument("--top-k", type=int, default=3, help="混合检索返回条数")
    parser.add_argument("--rebuild", action="store_true", help="强制重建 FAISS 索引")
    args = parser.parse_args()

    settings = Settings(
        markdown_path=args.markdown, embed_mode=args.embed,
        base_url=args.base_url, api_key=args.api_key, model=args.model,
        provider=args.provider, top_k=args.top_k, rebuild=args.rebuild,
    )

    # —— 步骤 1：读取本地报销制度 Markdown ——
    if not os.path.exists(settings.markdown_path):
        print(f"[错误] 找不到文档：{settings.markdown_path}")
        sys.exit(1)
    with open(settings.markdown_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
    print(f"[读取] {settings.markdown_path}（{len(raw_text)} 字）")

    # —— 步骤 2：切片（核心复用模块）——
    chunks = split_markdown_into_chunks(raw_text, settings.chunk_size, settings.chunk_overlap)
    print(f"[切片] 共 {len(chunks)} 块（每块约 {settings.chunk_size} 字，重叠 {settings.chunk_overlap}）")

    # —— 步骤 3：嵌入适配器 + FAISS 存储 ——
    adapter = EmbeddingAdapter(mode=settings.embed_mode, dim=settings.embed_dim,
                               base_url=settings.base_url, api_key=settings.api_key)
    store = FAISSKnowledgeStore(settings, adapter)

    # 缓存命中且非重建 → 直接加载；否则构建并「存入 FAISS」（用户要求）
    if settings.rebuild or not store.load():
        store.build(chunks)

    # BM25 检索器基于完整块列表构建（与 FAISS 共用同一批切片）
    bm25 = BM25Retriever(store.chunks)

    # —— 步骤 4：命令行问答循环 ——
    print("\n========== 报销制度问答（输入 quit 退出）==========")
    while True:
        try:
            question = input("\n你的问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if question.lower() in {"quit", "exit", "q"}:
            print("再见。")
            break
        if not question:
            continue

        # 混合检索 Top3
        contexts = hybrid_retrieve(question, store, bm25, adapter, top_k=settings.top_k)
        print(f"\n[检索到 {len(contexts)} 个片段]")
        for i, c in enumerate(contexts, 1):
            print(f"  [{i}] 【{c['section']}】 {c['content'][:60].replace(chr(10), ' ')}…")

        # 拼提示词 → 调 LLM
        messages = build_prompt(question, contexts)
        answer = call_llm(messages, provider=settings.provider,
                          base_url=settings.base_url, api_key=settings.api_key,
                          model=settings.model)
        print(f"\n[回答]\n{answer}")


if __name__ == "__main__":
    main()
