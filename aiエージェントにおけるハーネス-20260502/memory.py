# PoC品質 - 本番利用前に永続化バックエンド（DynamoDB・pgvector等）を接続すること
"""
メモリ管理モジュール

3層メモリ構造:
  ┌─────────────────────────────────────────────────┐
  │ Layer 1: セッション内短期メモリ（In-context）     │ ← コンテキストウィンドウ内
  │ Layer 2: ワーキングメモリ（作業記憶）             │ ← セッション中永続
  │ Layer 3: 長期メモリ（Long-term）                  │ ← セッション間永続（外部DB）
  └─────────────────────────────────────────────────┘

参考: AWS AgentCore のコンテキストトークン 89〜95% 削減アプローチを参考に、
      LLM が「何を・いつ」保存するかを管理する設計パターン。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

@dataclass
class MemoryEntry:
    """メモリの1エントリ"""
    id: str                            # 一意なID（コンテンツのハッシュ）
    content: str                       # 記憶内容
    memory_type: str                   # "episodic"（出来事）/ "semantic"（知識）/ "procedural"（手順）
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    metadata: dict = field(default_factory=dict)
    embedding: Optional[list[float]] = None  # ベクター検索用（本番で設定）


def _make_id(content: str) -> str:
    """コンテンツのSHA-256ハッシュからIDを生成する"""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# ストレージバックエンド（差し替え可能な設計）
# ---------------------------------------------------------------------------

class MemoryBackend:
    """
    メモリストレージの基底クラス。
    本番実装では DynamoDB・Redis・PostgreSQL 等に差し替える。
    このスケルトンはインメモリ実装（プロセス再起動で消える）。
    """

    def __init__(self) -> None:
        self._store: dict[str, MemoryEntry] = {}

    def save(self, entry: MemoryEntry) -> None:
        self._store[entry.id] = entry

    def get(self, memory_id: str) -> Optional[MemoryEntry]:
        entry = self._store.get(memory_id)
        if entry:
            entry.last_accessed = time.time()
            entry.access_count += 1
        return entry

    def search_by_keyword(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        """
        キーワードベースの単純検索（スケルトン）。
        本番ではベクター類似度検索（cosine similarity）に置換すること。
        """
        query_lower = query.lower()
        matches = [
            e for e in self._store.values()
            if query_lower in e.content.lower()
        ]
        # アクセス頻度が高いものを優先（LRU的ランキング）
        matches.sort(key=lambda e: e.access_count, reverse=True)
        return matches[:top_k]

    def delete(self, memory_id: str) -> bool:
        return bool(self._store.pop(memory_id, None))

    def count(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# メモリマネージャー本体
# ---------------------------------------------------------------------------

class MemoryManager:
    """
    3層メモリ管理マネージャー。

    AgentHarness から以下の用途で使用される:
      1. retrieve_relevant() : 新しいユーザープロンプトに関連する過去のコンテキストを取得
      2. store()             : タスク完了後にQ&Aペアや重要な知識を保存
      3. update()            : 既存のメモリを更新（重複統合）
      4. forget()            : 不要・古いメモリを削除

    メモリ操作の判断（何を・いつ保存するか）は AgentHarness が制御するため、
    このクラスはCRUD操作と検索のみを担う。
    """

    def __init__(self, backend: Optional[MemoryBackend] = None) -> None:
        self._backend = backend or MemoryBackend()

    # ------------------------------------------------------------------
    # 基本 CRUD
    # ------------------------------------------------------------------

    def store(
        self,
        user_query: str,
        agent_response: str,
        memory_type: str = "episodic",
        metadata: Optional[dict] = None,
    ) -> str:
        """
        ユーザークエリとエージェント応答のペアをエピソード記憶として保存する。

        Parameters
        ----------
        user_query : str
            ユーザーの質問・指示
        agent_response : str
            エージェントの応答（重要な情報・決定事項）
        memory_type : str
            "episodic"（出来事）/ "semantic"（知識）/ "procedural"（手順）
        metadata : dict, optional
            タグ・セッションID 等の追加情報

        Returns
        -------
        str
            保存されたメモリのID
        """
        content = f"Q: {user_query}\nA: {agent_response}"
        memory_id = _make_id(content)

        # 重複チェック: 同一コンテンツは上書きせずアクセスカウントを更新
        existing = self._backend.get(memory_id)
        if existing:
            return memory_id

        entry = MemoryEntry(
            id=memory_id,
            content=content,
            memory_type=memory_type,
            metadata=metadata or {},
        )
        self._backend.save(entry)
        return memory_id

    def store_knowledge(
        self,
        knowledge: str,
        tags: Optional[list[str]] = None,
    ) -> str:
        """
        手順・ルール・ドメイン知識を意味記憶（semantic memory）として保存する。
        ラチェットパターン: エージェントの失敗→学習→保存のサイクルで活用。
        """
        memory_id = _make_id(knowledge)
        entry = MemoryEntry(
            id=memory_id,
            content=knowledge,
            memory_type="semantic",
            metadata={"tags": tags or []},
        )
        self._backend.save(entry)
        return memory_id

    def retrieve_relevant(self, query: str, top_k: int = 5) -> list[str]:
        """
        クエリに関連するメモリを検索し、コンテンツ文字列のリストで返す。

        本番実装ではベクター埋め込みによる類似度検索を使用することで
        より高精度な関連記憶の取得が可能。

        Parameters
        ----------
        query : str
            検索クエリ（ユーザーの新しい質問・タスク）
        top_k : int
            返す結果の最大件数

        Returns
        -------
        list[str]
            関連するメモリのコンテンツリスト
        """
        results = self._backend.search_by_keyword(query, top_k=top_k)
        return [r.content for r in results]

    def update(self, memory_id: str, new_content: str) -> bool:
        """
        既存のメモリを更新する。
        LLMによる重複統合（ADD/UPDATE/NO-OP判定）パターンで使用する。
        """
        existing = self._backend.get(memory_id)
        if not existing:
            return False
        existing.content = new_content
        existing.last_accessed = time.time()
        self._backend.save(existing)
        return True

    def forget(self, memory_id: str) -> bool:
        """指定したメモリを削除する（忘れる）"""
        return self._backend.delete(memory_id)

    # ------------------------------------------------------------------
    # コンテキスト注入
    # ------------------------------------------------------------------

    def build_context_block(self, query: str, top_k: int = 5) -> str:
        """
        クエリに関連するメモリをシステムプロンプトに注入できる
        フォーマット済みテキストブロックとして返す。

        使用例:
            system_prompt = base_prompt + memory.build_context_block(user_query)
        """
        memories = self.retrieve_relevant(query, top_k=top_k)
        if not memories:
            return ""

        lines = ["## 関連する過去のコンテキスト（自動取得）"]
        for i, mem in enumerate(memories, 1):
            # 長いメモリは先頭200文字に切り詰めてトークンを節約
            snippet = mem[:200] + "..." if len(mem) > 200 else mem
            lines.append(f"{i}. {snippet}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 統計情報
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """メモリストアの統計情報を返す"""
        return {
            "total_entries": self._backend.count(),
            "backend_type": type(self._backend).__name__,
        }

    def export_snapshot(self) -> list[dict]:
        """
        現在のメモリ内容をシリアライズ可能な形式でエクスポートする。
        デバッグや外部バックアップ用途。
        """
        all_entries = self._backend.search_by_keyword("", top_k=10000)
        return [
            {
                "id": e.id,
                "content": e.content,
                "type": e.memory_type,
                "created_at": e.created_at,
                "access_count": e.access_count,
                "metadata": e.metadata,
            }
            for e in all_entries
        ]


# ---------------------------------------------------------------------------
# 使用例
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    memory = MemoryManager()

    # エピソード記憶の保存（Q&Aペア）
    id1 = memory.store(
        user_query="Pythonでファイルを読み込む方法は？",
        agent_response="open() 関数と with 文を使います: `with open('file.txt') as f: data = f.read()`",
    )

    # 意味記憶（ルール・知識）の保存
    id2 = memory.store_knowledge(
        "このプロジェクトではPEP8コーディング規約を遵守すること。行の最大長は100文字。",
        tags=["coding_standard", "python"],
    )

    # 関連メモリの検索
    results = memory.retrieve_relevant("Pythonのコーディング規約")
    print("=== 検索結果 ===")
    for r in results:
        print(f"  - {r[:80]}...")

    # コンテキストブロックの生成（システムプロンプトへの注入用）
    ctx = memory.build_context_block("Pythonでファイル操作")
    print(f"\n=== コンテキストブロック ===\n{ctx}")

    # 統計情報
    print(f"\n=== 統計 ===\n{json.dumps(memory.stats(), ensure_ascii=False, indent=2)}")
