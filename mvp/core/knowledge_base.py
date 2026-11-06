# -*- coding: utf-8 -*-
"""知识库模块（Knowledge Base）
MVP 以结构化 JSON 作为单一口径出口，支持：
  - 别名/关键词匹配（match）
  - 按券检索（get / list）
  - 版本与时效信息（version / meta）
  - 管理端写入（upsert）与发版（publish，写快照）
设计为可平滑升级为向量检索（RAG）的接口形态。
"""
import json
import os
import copy
import datetime


class KnowledgeBase:
    def __init__(self, path: str):
        self.path = path
        self.load()

    # ---------- 读取 ----------
    def load(self):
        with open(self.path, encoding="utf-8") as f:
            self.data = json.load(f)
        self.meta = self.data.get("meta", {})
        self.coupons = self.data.get("coupons", [])
        self._build_index()

    def _build_index(self):
        """建立 别名/关键词 -> coupon_id 的索引。"""
        self._index = {}
        for c in self.coupons:
            self._index[c["id"]] = c["id"]
            for a in c.get("alias", []):
                self._index[a] = c["id"]

    def match(self, text: str):
        """在文本中匹配券（最长别名优先）。"""
        best = None
        for alias, key in self._index.items():
            if alias and alias in text:
                if best is None or len(alias) > len(best[0]):
                    best = (alias, key)
        return best[1] if best else None

    def get(self, key: str):
        for c in self.coupons:
            if c["id"] == key:
                return c
        return None

    def list(self):
        return copy.deepcopy(self.coupons)

    def version(self) -> str:
        return self.meta.get("version", "unknown")

    def meta_full(self) -> dict:
        return copy.deepcopy(self.meta)

    # ---------- 管理端写入 ----------
    def upsert(self, coupon: dict) -> dict:
        """新增或更新一条券规则（不自动发版，需 publish 生效版本号）。"""
        cid = coupon.get("id")
        if not cid:
            raise ValueError("coupon 缺少 id")
        found = False
        for i, c in enumerate(self.coupons):
            if c["id"] == cid:
                self.coupons[i] = coupon
                found = True
                break
        if not found:
            self.coupons.append(coupon)
        self._build_index()
        self._persist()
        return coupon

    def publish_version(self, new_version: str, owner: str, note: str = "") -> dict:
        """发版：更新 meta.version / updated_at / owner，并持久化。"""
        self.meta["version"] = new_version
        self.meta["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d")
        self.meta["owner"] = owner
        if note:
            self.meta["note"] = note
        # 同步把版本号写到每条券，保证答案可追溯
        for c in self.coupons:
            c["version"] = new_version
        self._persist()
        return self.meta

    def snapshot(self) -> str:
        """导出当前知识库完整 JSON 字符串（用于版本快照）。"""
        return json.dumps(self.data, ensure_ascii=False, indent=2)

    def _persist(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
