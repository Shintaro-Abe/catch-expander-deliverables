# PoC品質 - このコードは概念実証用です。本番環境での利用前に十分なテストと改修を行ってください。
"""
メモリ管理（Memory Manager）モジュール

AIエージェントの短期記憶・長期記憶・手続き記憶を管理します。

【初学者向け補足】
- 短期メモリ（ワーキングメモリ）≒ 作業台（今やっている仕事の書類）
- 長期メモリ（セマンティック記憶）≒ ファイリングキャビネット（過去の記録）
- 手続き記憶              ≒ 業務マニュアル（やり方の手順書）

メモリ管理は「書込 → 管理 → 読出」の3段階ループとして設計されています。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# データ構造
# --------------------------------------------------------------------------- #

@dataclass
class Message:
    """
    会話の1メッセージを表すデータクラス。

    Attributes:
        role      : "user" または "assistant"（Converse API 互換）
        content   : メッセージ本文
        timestamp : Unix タイムスタンプ
        token_est : トークン数の概算（1文字≒1.5トークンの簡易計算）
    """
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    token_est: int = field(init=False)

    def __post_init__(self) -> None:
        # 簡易トークン推定：日本語は1文字≒2トークン、英語は1単語≒1.3トークン
        self.token_est = int(len(self.content) * 1.5)

    def to_api_format(self) -> Dict[str, Any]:
        """Bedrock Converse API のメッセージ形式に変換する。"""
        return {"role": self.role, "content": [{"text": self.content}]}


@dataclass
class MemoryEntry:
    """
    長期記憶の1エントリ。

    Attributes:
        key       : エントリの一意識別子
        content   : 記憶の内容（テキスト）
        timestamp : 最終更新時刻
        version   : 楽観的ロック用バージョン番号
        tags      : 検索・フィルタリング用タグ
    """
    key: str
    content: str
    timestamp: float = field(default_factory=time.time)
    version: int = 1
    tags: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# ワーキングメモリ（短期記憶 / コンテキストウィンドウ管理）
# --------------------------------------------------------------------------- #

class WorkingMemory:
    """
    コンテキストウィンドウ内のメッセージ履歴を管理するクラス。

    コンテキストウィンドウは有限なため、古いメッセージの圧縮・削除が必要です。
    本実装では「ウィンドウサイズ超過時に古いメッセージをサマリーに置換」する
    スライディングウィンドウ方式を採用しています。

    落とし穴:
    - 要約ドリフト: 繰り返し圧縮すると意味が変質するため、
      原文も別途 EpisodicMemory に保存することを推奨します。
    """

    def __init__(self, max_tokens: int = 4096, max_messages: int = 20) -> None:
        """
        Args:
            max_tokens   : コンテキストに保持できる最大トークン数
            max_messages : 圧縮前の最大メッセージ件数
        """
        self.max_tokens = max_tokens
        self.max_messages = max_messages
        self._messages: List[Message] = []
        self._system_prompt: str = ""
        self._summary: str = ""  # 圧縮済み過去会話のサマリー

    # ---------------------------------------------------------------- #
    # 書き込み
    # ---------------------------------------------------------------- #

    def set_system_prompt(self, prompt: str) -> None:
        """システムプロンプトを設定する（エージェントの基本指示）。"""
        self._system_prompt = prompt

    def add(self, role: str, content: str) -> None:
        """
        メッセージを追加する。追加後にコンテキストウィンドウのチェックを行い、
        必要に応じて古いメッセージを圧縮する。
        """
        msg = Message(role=role, content=content)
        self._messages.append(msg)
        logger.debug("メッセージ追加: role=%s tokens≈%d", role, msg.token_est)
        self._trim_if_needed()

    # ---------------------------------------------------------------- #
    # 読み出し
    # ---------------------------------------------------------------- #

    def get_messages(self) -> List[Dict[str, Any]]:
        """
        Bedrock Converse API へ渡す messages リストを返す。
        サマリーが存在する場合は先頭に挿入する。
        """
        result = []
        if self._summary:
            result.append({
                "role": "user",
                "content": [{"text": f"[過去の会話サマリー]\n{self._summary}"}],
            })
            result.append({
                "role": "assistant",
                "content": [{"text": "過去の会話内容を把握しました。"}],
            })
        result.extend(msg.to_api_format() for msg in self._messages)
        return result

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def total_tokens(self) -> int:
        """現在の推定合計トークン数。"""
        return sum(m.token_est for m in self._messages)

    # ---------------------------------------------------------------- #
    # 圧縮（プライベート）
    # ---------------------------------------------------------------- #

    def _trim_if_needed(self) -> None:
        """
        メッセージ数またはトークン数が上限を超えた場合に古いメッセージを圧縮する。

        本 PoC では単純に先頭の 1/3 を削除してサマリーに置き換えます。
        本番実装では LLM に要約を依頼することを推奨します。
        """
        over_count = len(self._messages) > self.max_messages
        over_tokens = self.total_tokens > self.max_tokens

        if not (over_count or over_tokens):
            return

        trim_count = max(len(self._messages) // 3, 1)
        old_messages = self._messages[:trim_count]
        self._messages = self._messages[trim_count:]

        # 簡易サマリー生成（本番では LLM に依頼すること）
        old_text = " / ".join(f"[{m.role}] {m.content[:50]}..." for m in old_messages)
        if self._summary:
            self._summary += f"\n{old_text}"
        else:
            self._summary = old_text

        logger.info(
            "コンテキスト圧縮: %d件を削除しサマリーに統合 (残り %d件)",
            trim_count,
            len(self._messages),
        )

    def clear(self) -> None:
        """会話履歴をすべてクリアする。"""
        self._messages.clear()
        self._summary = ""


# --------------------------------------------------------------------------- #
# エピソード記憶（過去の会話の生データ保存）
# --------------------------------------------------------------------------- #

class EpisodicMemory:
    """
    「何をしたか」という具体的な経験の履歴を保存するクラス。

    要約ドリフト対策として、WorkingMemory から削除されたメッセージの
    原文をこちらに保持します。RAG（検索拡張生成）と組み合わせることで
    過去の関連会話をコンテキストに再注入できます。

    本 PoC ではインメモリ実装。
    本番では PostgreSQL / Redis / DynamoDB 等の永続ストアを使用してください。
    """

    def __init__(self, max_episodes: int = 1000) -> None:
        self.max_episodes = max_episodes
        self._store: List[Tuple[float, str, str]] = []  # (timestamp, role, content)

    def save(self, role: str, content: str) -> None:
        """エピソードを保存する。容量超過時は最古エントリを削除する。"""
        if len(self._store) >= self.max_episodes:
            self._store.pop(0)
        self._store.append((time.time(), role, content))

    def search(self, keyword: str, top_k: int = 3) -> List[str]:
        """
        キーワードで過去エピソードを検索する（簡易全文検索）。
        本番ではベクトル類似検索（Bedrock Knowledge Base 等）を推奨。
        """
        keyword_lower = keyword.lower()
        matches = [
            f"[{role}] {content}"
            for _, role, content in self._store
            if keyword_lower in content.lower()
        ]
        return matches[-top_k:]  # 直近 top_k 件を返す

    @property
    def count(self) -> int:
        return len(self._store)


# --------------------------------------------------------------------------- #
# セマンティック記憶（長期記憶 / キーバリューストア）
# --------------------------------------------------------------------------- #

class SemanticMemory:
    """
    抽象化された知識・確立された事実を保存する長期記憶クラス。

    AgeMem の概念に基づき、記憶操作（格納・取得・更新・削除）を
    明示的な操作として公開しています。

    本 PoC ではインメモリ辞書実装。
    本番では DynamoDB / PostgreSQL / Redis 等を使用してください。
    """

    def __init__(self) -> None:
        self._store: Dict[str, MemoryEntry] = {}

    # ---------------------------------------------------------------- #
    # CRUD 操作
    # ---------------------------------------------------------------- #

    def put(self, key: str, content: str, tags: Optional[List[str]] = None) -> MemoryEntry:
        """
        エントリを格納または更新する。
        既存キーが存在する場合はバージョンをインクリメントする（楽観的ロック）。
        """
        existing = self._store.get(key)
        version = (existing.version + 1) if existing else 1
        entry = MemoryEntry(key=key, content=content, tags=tags or [], version=version)
        self._store[key] = entry
        logger.debug("長期記憶 put: key=%s version=%d", key, version)
        return entry

    def get(self, key: str) -> Optional[MemoryEntry]:
        """キーでエントリを取得する。存在しない場合は None を返す。"""
        return self._store.get(key)

    def delete(self, key: str) -> bool:
        """エントリを削除する。削除できた場合 True を返す。"""
        if key in self._store:
            del self._store[key]
            logger.debug("長期記憶 delete: key=%s", key)
            return True
        return False

    def search_by_tags(self, tags: List[str]) -> List[MemoryEntry]:
        """タグに一致するエントリを検索する。"""
        return [e for e in self._store.values() if set(tags) & set(e.tags)]

    def search_by_content(self, keyword: str) -> List[MemoryEntry]:
        """
        コンテンツにキーワードが含まれるエントリを検索する（簡易全文検索）。
        本番ではベクトル検索を推奨。
        """
        kw = keyword.lower()
        return [e for e in self._store.values() if kw in e.content.lower()]

    @property
    def all_entries(self) -> List[MemoryEntry]:
        return list(self._store.values())


# --------------------------------------------------------------------------- #
# MemoryManager：全メモリ層を統合する ファサード
# --------------------------------------------------------------------------- #

class MemoryManager:
    """
    ワーキングメモリ・エピソード記憶・セマンティック記憶を統合管理するファサード。

    エージェントのハーネスはこのクラスを通じてメモリ操作を行います。
    各メモリ層の詳細は呼び出し側が意識する必要はありません。
    """

    def __init__(
        self,
        max_context_tokens: int = 4096,
        max_context_messages: int = 20,
        max_episodes: int = 1000,
    ) -> None:
        self.working = WorkingMemory(
            max_tokens=max_context_tokens,
            max_messages=max_context_messages,
        )
        self.episodic = EpisodicMemory(max_episodes=max_episodes)
        self.semantic = SemanticMemory()

    # ---------------------------------------------------------------- #
    # 会話フロー操作（エージェントループから呼ばれる主要 API）
    # ---------------------------------------------------------------- #

    def add_user_message(self, content: str) -> None:
        """ユーザーメッセージを追加し、エピソード記憶にも保存する。"""
        self.working.add("user", content)
        self.episodic.save("user", content)

    def add_assistant_message(self, content: str) -> None:
        """アシスタント（エージェント）のメッセージを追加し、エピソード記憶にも保存する。"""
        self.working.add("assistant", content)
        self.episodic.save("assistant", content)

    def get_context_messages(self) -> List[Dict[str, Any]]:
        """現在のコンテキストメッセージを Converse API 形式で返す。"""
        return self.working.get_messages()

    # ---------------------------------------------------------------- #
    # 長期記憶操作
    # ---------------------------------------------------------------- #

    def remember(self, key: str, content: str, tags: Optional[List[str]] = None) -> None:
        """重要な情報を長期記憶に格納する。"""
        self.semantic.put(key, content, tags)

    def recall(self, key: str) -> Optional[str]:
        """キーで長期記憶を取得する。存在しない場合 None を返す。"""
        entry = self.semantic.get(key)
        return entry.content if entry else None

    def recall_by_keyword(self, keyword: str) -> List[str]:
        """
        キーワードに関連する記憶をすべての層から検索する（RAG的アプローチ）。
        エピソード記憶と長期記憶の両方を検索し、関連情報を返す。
        """
        results: List[str] = []
        # エピソード記憶を検索
        results.extend(self.episodic.search(keyword))
        # 長期記憶を検索
        for entry in self.semantic.search_by_content(keyword):
            results.append(f"[長期記憶:{entry.key}] {entry.content}")
        return results

    # ---------------------------------------------------------------- #
    # セッション管理
    # ---------------------------------------------------------------- #

    def clear_context(self) -> None:
        """コンテキストウィンドウをクリアする（新規セッション開始時に使用）。"""
        self.working.clear()

    def dump_state(self) -> Dict[str, Any]:
        """
        現在のメモリ状態をシリアライズ可能な辞書形式で返す。
        セッションの永続化・デバッグに使用する。
        """
        return {
            "working_memory": {
                "message_count": len(self.working._messages),
                "total_tokens_est": self.working.total_tokens,
                "has_summary": bool(self.working._summary),
            },
            "episodic_memory": {"episode_count": self.episodic.count},
            "semantic_memory": {
                "entry_count": len(self.semantic.all_entries),
                "keys": [e.key for e in self.semantic.all_entries],
            },
        }

    def content_hash(self) -> str:
        """
        現在のコンテキストメッセージの SHA256 ハッシュを返す。
        ループ検出（同一状態の繰り返し検知）に使用する。
        """
        state = json.dumps(self.get_context_messages(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(state.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# 動作確認エントリポイント
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("=== メモリ管理動作確認 ===\n")

    manager = MemoryManager(max_context_tokens=200, max_context_messages=5)
    manager.working.set_system_prompt("あなたは有能なアシスタントエージェントです。")

    # 1. 会話履歴の追加とコンテキスト管理
    print("[1] 会話履歴の追加:")
    exchanges = [
        ("AWSとは何ですか？", "AWS（Amazon Web Services）はAmazonのクラウドサービスです。"),
        ("Lambdaとは？", "AWS Lambdaはサーバーレスコンピューティングサービスです。"),
        ("料金体系は？", "Lambdaは実行時間とリクエスト数で課金されます。無料枠もあります。"),
    ]
    for user_msg, assistant_msg in exchanges:
        manager.add_user_message(user_msg)
        manager.add_assistant_message(assistant_msg)
        print(f"  追加: [{user_msg[:20]}...] -> [{assistant_msg[:30]}...]")

    # 2. 現在の状態確認
    print("\n[2] メモリ状態:")
    state = manager.dump_state()
    import json as _json
    print(_json.dumps(state, ensure_ascii=False, indent=2))

    # 3. 長期記憶への格納と検索
    print("\n[3] 長期記憶:")
    manager.remember("user_preference", "ユーザーはAWSのサーバーレス技術に興味がある", tags=["preference", "aws"])
    manager.remember("session_goal", "Lambdaの使い方を学習する", tags=["goal"])
    print("  格納済みエントリ:", [e.key for e in manager.semantic.all_entries])
    print("  'aws' タグ検索:", [e.content for e in manager.semantic.search_by_tags(["aws"])])

    # 4. キーワード横断検索（RAG 的アプローチ）
    print("\n[4] 'Lambda' に関する記憶の横断検索:")
    recalled = manager.recall_by_keyword("Lambda")
    for r in recalled:
        print(f"  - {r[:60]}...")

    # 5. コンテキストハッシュ（ループ検出用）
    print("\n[5] コンテキストハッシュ:", manager.content_hash()[:16], "...")
