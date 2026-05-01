# PoC品質 - 本番利用前に認証・エラーハンドリング・テストを追加してください
"""
推論コスト最適化モジュール
==========================
LLMOpsにおけるトークン課金（推論コスト）を削減するための戦略を実装します。

【LLMのコスト構造（MLOpsとの違い）】
従来のMLOps: 訓練コストが主体（一度払えば使い続けられる）
LLMOps:      推論コスト（トークン課金）が主体 → リクエストのたびに課金

【コスト削減戦略の組み合わせ効果（業界データより）】
  セマンティックキャッシュ: 最大69%削減
  プロバイダーキャッシュ:   最大90%削減（Anthropic Claude の場合）
  モデルルーティング:       最大87%削減
  バッチ処理:               30〜50%削減
  複合適用:                 60〜80%削減
"""

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


# =========================================================
# セマンティックキャッシュ
# =========================================================

@dataclass
class CacheEntry:
    """キャッシュの1エントリ"""
    query: str          # 元のクエリ文字列
    response: str       # LLMからの回答
    embedding: list[float]  # クエリの埋め込みベクトル
    token_count: int    # 消費したトークン数
    created_at: float = field(default_factory=time.time)
    hit_count: int = 0  # 何回キャッシュヒットしたか


class SemanticCache:
    """
    セマンティックキャッシュ（意味的に類似したクエリの回答を再利用）。

    【通常のキャッシュとの違い】
    通常のキャッシュ: 完全一致のみ（"今日の天気" と "今日の天気は?" は別クエリ扱い）
    セマンティックキャッシュ: 意味が近ければヒット（両者を同一として扱える）

    【性能指標（GPTCacheの実績）】
      キャッシュヒット率: 62〜69%（カテゴリによる）
      最適なコサイン類似度閾値: 0.8
      APIコール削減率: 最大68.8%

    【本番環境でのバックエンド選択】
      小規模: FAISS（インメモリ）
      中規模: Redis + ベクトル検索
      大規模: Milvus / Amazon OpenSearch / pgvector（PostgreSQL）
    """

    def __init__(self, similarity_threshold: float = 0.8, max_entries: int = 1000):
        """
        Args:
            similarity_threshold: ヒットと見なすコサイン類似度の閾値（推奨: 0.8）
            max_entries: キャッシュの最大エントリ数（LRUで古いものを削除）
        """
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self._entries: list[CacheEntry] = []
        self._stats = {"hits": 0, "misses": 0, "tokens_saved": 0}

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """コサイン類似度を計算する（値: -1〜1、1が完全一致）"""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x ** 2 for x in a))
        norm_b = math.sqrt(sum(x ** 2 for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def lookup(self, query_embedding: list[float]) -> Optional[CacheEntry]:
        """
        クエリの埋め込みに最も類似したキャッシュエントリを検索する。

        Returns:
            類似度が閾値以上のエントリ（見つからなければ None）
        """
        best_entry = None
        best_similarity = 0.0

        for entry in self._entries:
            sim = self._cosine_similarity(query_embedding, entry.embedding)
            if sim > best_similarity:
                best_similarity = sim
                best_entry = entry

        if best_similarity >= self.similarity_threshold and best_entry is not None:
            best_entry.hit_count += 1
            self._stats["hits"] += 1
            self._stats["tokens_saved"] += best_entry.token_count
            return best_entry

        self._stats["misses"] += 1
        return None

    def store(self, query: str, embedding: list[float], response: str, token_count: int) -> None:
        """新しいエントリをキャッシュに保存する（最大件数を超えたら古いものを削除）。"""
        if len(self._entries) >= self.max_entries:
            # LRU: 最もヒット数が少なく最も古いエントリを削除
            self._entries.sort(key=lambda e: (e.hit_count, e.created_at))
            self._entries.pop(0)

        self._entries.append(CacheEntry(
            query=query,
            response=response,
            embedding=embedding,
            token_count=token_count,
        ))

    @property
    def hit_rate(self) -> float:
        """キャッシュヒット率（0〜1）"""
        total = self._stats["hits"] + self._stats["misses"]
        return self._stats["hits"] / total if total > 0 else 0.0

    def get_stats(self) -> dict:
        total = self._stats["hits"] + self._stats["misses"]
        return {
            "total_requests": total,
            "cache_hits": self._stats["hits"],
            "cache_misses": self._stats["misses"],
            "hit_rate": round(self.hit_rate, 4),
            "tokens_saved": self._stats["tokens_saved"],
            "cached_entries": len(self._entries),
        }


# =========================================================
# プロバイダーキャッシュ（Anthropic Claude プロンプトキャッシュ）
# =========================================================

class AnthropicPromptCacheBuilder:
    """
    Anthropic Claude のプロンプトキャッシュ設定を構築するヘルパークラス。

    【プロンプトキャッシュとは】
    同じシステムプロンプトやコンテキストを繰り返し使う場合、
    Anthropic のサーバー側でキャッシュして2回目以降のコストを90%削減する機能。

    【コスト比較（Claude Sonnet の場合）】
    通常:          入力 $3.00/Mトークン
    キャッシュ書込: $3.75/Mトークン（+25%：初回書き込みコスト）
    キャッシュ読出: $0.30/Mトークン（-90%：2回目以降）
    ブレークイーブン: 1.4回のキャッシュヒットでコスト回収

    【適用条件】
    - 最小1,024トークン以上のブロックにのみ適用可能
    - 繰り返し使われる長いシステムプロンプト・ドキュメントに最も効果的
    """

    CACHE_CONTROL = {"type": "ephemeral"}  # Anthropic のキャッシュマーカー
    MIN_CACHE_TOKENS = 1024  # キャッシュ適用の最小トークン数

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt

    def build_messages(self, user_query: str, context_documents: list[str]) -> dict:
        """
        キャッシュ設定付きのメッセージリストを構築する。

        cache_control マーカーをつけた部分がキャッシュされます。
        ユーザークエリはリクエストごとに変わるため、キャッシュしません。

        Returns:
            Anthropic API に渡すメッセージ辞書（boto3 の bedrock-runtime 形式）
        """
        # システムプロンプト（キャッシュ対象: 変更頻度が低く長い）
        system_content = [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": self.CACHE_CONTROL,  # ← キャッシュマーカー
            }
        ]

        # コンテキストドキュメント（RAG で取得した参照文書もキャッシュ対象）
        user_content = []
        for i, doc in enumerate(context_documents):
            user_content.append({
                "type": "text",
                "text": f"参照文書 {i + 1}:\n{doc}",
                "cache_control": self.CACHE_CONTROL,  # ← キャッシュマーカー
            })

        # ユーザーの質問（キャッシュしない: リクエストごとに異なる）
        user_content.append({
            "type": "text",
            "text": f"\n質問: {user_query}",
        })

        return {
            "system": system_content,
            "messages": [{"role": "user", "content": user_content}],
        }

    def estimate_savings(self, monthly_requests: int, avg_system_tokens: int, cache_hit_rate: float) -> dict:
        """
        月間コスト削減額を試算する。

        Args:
            monthly_requests: 月間リクエスト数
            avg_system_tokens: システムプロンプトの平均トークン数
            cache_hit_rate: キャッシュヒット率（0〜1）

        Returns:
            コスト比較の試算結果（USD）
        """
        price_per_m_normal = 3.00   # 通常の入力トークン単価 ($/Mトークン)
        price_per_m_write = 3.75    # キャッシュ書き込み単価
        price_per_m_read = 0.30     # キャッシュ読み出し単価

        total_tokens_m = (monthly_requests * avg_system_tokens) / 1_000_000

        # キャッシュなしのコスト
        cost_without_cache = total_tokens_m * price_per_m_normal

        # キャッシュありのコスト
        write_requests = monthly_requests * (1 - cache_hit_rate)
        read_requests = monthly_requests * cache_hit_rate
        cost_with_cache = (
            (write_requests * avg_system_tokens / 1_000_000) * price_per_m_write
            + (read_requests * avg_system_tokens / 1_000_000) * price_per_m_read
        )

        savings = cost_without_cache - cost_with_cache

        return {
            "monthly_requests": monthly_requests,
            "avg_system_tokens": avg_system_tokens,
            "cache_hit_rate": cache_hit_rate,
            "cost_without_cache_usd": round(cost_without_cache, 2),
            "cost_with_cache_usd": round(cost_with_cache, 2),
            "monthly_savings_usd": round(savings, 2),
            "savings_percentage": round(savings / cost_without_cache * 100, 1) if cost_without_cache > 0 else 0,
        }


# =========================================================
# モデルルーティング（カスケード戦略）
# =========================================================

@dataclass
class ModelConfig:
    """LLMモデルの設定"""
    model_id: str              # モデルの識別子
    input_cost_per_1k: float   # 入力トークン1000件あたりのコスト (USD)
    output_cost_per_1k: float  # 出力トークン1000件あたりのコスト (USD)
    capability_level: int      # 能力レベル（1=基本, 2=中級, 3=高度）
    max_tokens: int = 4096     # 最大出力トークン数


class ModelRouter:
    """
    クエリの複雑度に応じて最適なモデルを選択するルーター。

    【カスケード戦略の背景】
    エンタープライズLLMリクエストの分析:
      50〜70%: 最安モデルで処理可能（単純な質問・要約など）
       5〜15%: 最高性能モデルが必要（複雑な推論・多段階タスク）
    90%を安価なモデルにルーティングすることで約87%のコスト削減が可能。

    【Amazon Bedrock での実装】
    本番環境では amazon.bedrock.InvokeModel の model_id を動的に切り替えます。
    """

    def __init__(self, models: list[ModelConfig]):
        """
        Args:
            models: 利用可能なモデルのリスト（能力レベル昇順に並べる）
        """
        self.models = sorted(models, key=lambda m: m.capability_level)
        self._routing_log: list[dict] = []

    def route(self, query: str, complexity_score: float) -> ModelConfig:
        """
        クエリの複雑度スコアに基づいてモデルを選択する。

        Args:
            query: ユーザーのクエリ
            complexity_score: 複雑度スコア（0〜1: 0=単純, 1=複雑）

        Returns:
            選択されたモデルの設定

        【複雑度スコアの計算方法】
        本番環境では以下を組み合わせて計算:
          - クエリの長さ（長いほど複雑）
          - 特定キーワードの有無（「分析」「比較」「理由を説明」など）
          - タスクタイプ（分類: 低 / 要約: 中 / 複雑な推論: 高）
          - 必要な出力長の推定
        """
        selected = self.models[0]  # デフォルトは最安モデル

        for model in self.models:
            required_capability = complexity_score * len(self.models)
            if model.capability_level >= required_capability:
                selected = model
                break
        else:
            selected = self.models[-1]  # 最高性能モデルにフォールバック

        self._routing_log.append({
            "query_length": len(query),
            "complexity_score": complexity_score,
            "selected_model": selected.model_id,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return selected

    def estimate_cost(self, model: ModelConfig, input_tokens: int, output_tokens: int) -> float:
        """モデル・トークン数からコストを計算する (USD)"""
        input_cost = (input_tokens / 1000) * model.input_cost_per_1k
        output_cost = (output_tokens / 1000) * model.output_cost_per_1k
        return input_cost + output_cost

    def get_routing_summary(self) -> dict:
        """ルーティングの統計サマリーを返す。"""
        if not self._routing_log:
            return {}

        model_counts: dict[str, int] = {}
        for log in self._routing_log:
            model_id = log["selected_model"]
            model_counts[model_id] = model_counts.get(model_id, 0) + 1

        total = len(self._routing_log)
        return {
            "total_requests": total,
            "model_distribution": {
                model_id: {
                    "count": count,
                    "percentage": round(count / total * 100, 1),
                }
                for model_id, count in model_counts.items()
            },
        }


# =========================================================
# トークンバジェット管理
# =========================================================

class TokenBudgetManager:
    """
    リクエストごとのトークン上限を管理するクラス。

    【なぜトークンバジェットが必要か】
    LLMは入力プロンプトが長くなるほど出力も長くなる傾向があります。
    バジェットを設定しないと、1リクエストで予想外に多くのトークンを消費します。

    【Early Stopping の効果】
    max_tokens を適切に設定することで出力トークンを20〜40%削減できます。
    """

    def __init__(
        self,
        daily_budget_usd: float,
        default_max_output_tokens: int = 1024,
    ):
        self.daily_budget_usd = daily_budget_usd
        self.default_max_output_tokens = default_max_output_tokens
        self._daily_spend: float = 0.0
        self._request_count: int = 0

    def check_budget(self, estimated_cost: float) -> bool:
        """予算内かどうかをチェックする。"""
        return (self._daily_spend + estimated_cost) <= self.daily_budget_usd

    def record_spend(self, actual_cost: float) -> None:
        """実際の支出を記録する。"""
        self._daily_spend += actual_cost
        self._request_count += 1

    def get_adaptive_max_tokens(self, remaining_budget_ratio: float) -> int:
        """
        残り予算の割合に応じて max_tokens を動的に調整する。

        予算が逼迫してきたら出力トークン数を絞ってコストを抑えます。
        """
        if remaining_budget_ratio > 0.5:
            return self.default_max_output_tokens
        elif remaining_budget_ratio > 0.2:
            return int(self.default_max_output_tokens * 0.7)
        else:
            return int(self.default_max_output_tokens * 0.3)

    @property
    def remaining_budget(self) -> float:
        return max(0.0, self.daily_budget_usd - self._daily_spend)

    @property
    def remaining_budget_ratio(self) -> float:
        return self.remaining_budget / self.daily_budget_usd if self.daily_budget_usd > 0 else 0.0

    def get_status(self) -> dict:
        return {
            "daily_budget_usd": self.daily_budget_usd,
            "spent_usd": round(self._daily_spend, 4),
            "remaining_usd": round(self.remaining_budget, 4),
            "remaining_ratio": round(self.remaining_budget_ratio, 4),
            "request_count": self._request_count,
            "adaptive_max_tokens": self.get_adaptive_max_tokens(self.remaining_budget_ratio),
        }


# =========================================================
# 統合コスト最適化パイプライン
# =========================================================

class LLMCostOptimizer:
    """
    セマンティックキャッシュ・モデルルーティング・バジェット管理を
    統合したコスト最適化パイプライン。

    【処理フロー（マルチティアキャッシング）】
    リクエスト
      ↓ Layer 1: セマンティックキャッシュ検索
      → キャッシュヒット → 即座に返却（API呼び出しなし: 100%節約）
      ↓ キャッシュミス
      ↓ Layer 2: モデルルーティング（複雑度に応じて安価なモデルへ）
      ↓ Layer 3: プロバイダーキャッシュ（Anthropic cache_control）
      → LLM API 呼び出し（KVキャッシュで50〜90%節約）
      ↓ 結果をセマンティックキャッシュに保存
    """

    def __init__(
        self,
        semantic_cache: SemanticCache,
        model_router: ModelRouter,
        budget_manager: TokenBudgetManager,
    ):
        self.cache = semantic_cache
        self.router = model_router
        self.budget = budget_manager

    def process(
        self,
        query: str,
        query_embedding: list[float],
        complexity_score: float,
        estimated_input_tokens: int = 500,
    ) -> dict:
        """
        クエリを受け取り、コスト最適化された処理結果を返す。

        Args:
            query: ユーザーのクエリ
            query_embedding: クエリの埋め込みベクトル（キャッシュ検索用）
            complexity_score: クエリの複雑度（0〜1）
            estimated_input_tokens: 入力トークン数の推定値
        """
        # Step 1: セマンティックキャッシュ検索
        cached = self.cache.lookup(query_embedding)
        if cached:
            return {
                "source": "semantic_cache",
                "response": cached.response,
                "tokens_used": 0,
                "cost_usd": 0.0,
                "model_used": "cache",
                "cache_similarity_hit": True,
            }

        # Step 2: モデルルーティング
        selected_model = self.router.route(query, complexity_score)

        # Step 3: バジェット確認
        estimated_output_tokens = self.budget.get_adaptive_max_tokens(
            self.budget.remaining_budget_ratio
        )
        estimated_cost = self.router.estimate_cost(
            selected_model, estimated_input_tokens, estimated_output_tokens
        )

        if not self.budget.check_budget(estimated_cost):
            return {
                "source": "budget_exceeded",
                "response": "本日のAPIバジェット上限に達しました。明日またお試しください。",
                "tokens_used": 0,
                "cost_usd": 0.0,
                "model_used": None,
            }

        # Step 4: LLM API 呼び出し（PoC用モック）
        response, actual_tokens = self._mock_llm_call(query, selected_model, estimated_output_tokens)
        actual_cost = self.router.estimate_cost(selected_model, estimated_input_tokens, actual_tokens)

        # Step 5: 支出記録とキャッシュ保存
        self.budget.record_spend(actual_cost)
        self.cache.store(query, query_embedding, response, actual_tokens)

        return {
            "source": "llm_api",
            "response": response,
            "tokens_used": actual_tokens,
            "cost_usd": round(actual_cost, 6),
            "model_used": selected_model.model_id,
            "cache_similarity_hit": False,
        }

    @staticmethod
    def _mock_llm_call(query: str, model: ModelConfig, max_tokens: int) -> tuple[str, int]:
        """PoC用モックLLM呼び出し。本番では boto3 で Bedrock API を呼び出します。"""
        import random
        tokens = random.randint(50, min(max_tokens, 500))
        return f"[{model.model_id}による回答] {query[:30]}...", tokens


# =========================================================
# デモ実行
# =========================================================

def demo():
    import random

    print("=" * 60)
    print("推論コスト最適化 デモ")
    print("=" * 60)

    # モデル設定（Amazon Bedrock の価格を参考にした架空の値）
    models = [
        ModelConfig("bedrock/haiku",  input_cost_per_1k=0.00025, output_cost_per_1k=0.00125, capability_level=1),
        ModelConfig("bedrock/sonnet", input_cost_per_1k=0.003,   output_cost_per_1k=0.015,   capability_level=2),
        ModelConfig("bedrock/opus",   input_cost_per_1k=0.015,   output_cost_per_1k=0.075,   capability_level=3),
    ]

    optimizer = LLMCostOptimizer(
        semantic_cache=SemanticCache(similarity_threshold=0.85),
        model_router=ModelRouter(models),
        budget_manager=TokenBudgetManager(daily_budget_usd=10.0),
    )

    queries = [
        ("今日の天気は？", 0.1),
        ("今日の天気教えて", 0.1),   # 上と類似 → キャッシュヒット期待
        ("量子コンピュータの原理を詳しく説明してください", 0.9),
        ("Pythonでソートするには？", 0.2),
        ("このコードのバグを分析し、修正案を3つ提示してください", 0.8),
    ]

    print("\n[リクエスト処理]")
    total_cost = 0.0
    for query, complexity in queries:
        # PoC用ダミー埋め込み（本番では text-embedding モデルを使用）
        emb = [random.gauss(complexity, 0.1) for _ in range(16)]
        result = optimizer.process(query, emb, complexity)
        total_cost += result["cost_usd"]
        print(
            f"  クエリ: {query[:30]:<32} | "
            f"ソース: {result['source']:<18} | "
            f"モデル: {str(result['model_used']):<20} | "
            f"コスト: ${result['cost_usd']:.6f}"
        )

    print(f"\n[キャッシュ統計]")
    cache_stats = optimizer.cache.get_stats()
    print(json.dumps(cache_stats, indent=2))

    print(f"\n[モデルルーティング統計]")
    routing_summary = optimizer.router.get_routing_summary()
    print(json.dumps(routing_summary, indent=2))

    print(f"\n[バジェット状況]")
    budget_status = optimizer.budget.get_status()
    print(json.dumps(budget_status, indent=2))

    print(f"\n[プロンプトキャッシュ コスト試算]")
    cache_builder = AnthropicPromptCacheBuilder("システムプロンプト（長文）")
    savings = cache_builder.estimate_savings(
        monthly_requests=100_000,
        avg_system_tokens=2000,
        cache_hit_rate=0.80,
    )
    print(json.dumps(savings, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    demo()
