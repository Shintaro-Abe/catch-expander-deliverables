# PoC品質 - 本番利用前に認証・エラーハンドリング・テストを追加してください
"""
プロンプト管理モジュール
========================
LLMOpsにおけるプロンプト（LLMへの指示文）のバージョン管理・A/Bテスト・デプロイを担当します。

【なぜプロンプト管理が重要か】
従来のMLOpsではモデルのバイナリ（重みファイル）がメインの成果物でした。
LLMOpsでは「プロンプト」が主要な開発レバーとなります。
プロンプトの10%改善は、モデル自体の10%改善より本番性能に大きく影響することもあります。
"""

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# =========================================================
# データ定義
# =========================================================

class PromptEnvironment(str, Enum):
    """プロンプトのデプロイ環境"""
    DEVELOPMENT = "development"   # 開発中
    STAGING = "staging"           # テスト環境
    PRODUCTION = "production"     # 本番環境


@dataclass
class PromptVersion:
    """
    プロンプトの1バージョンを表すデータクラス。

    セマンティックバージョニング (X.Y.Z) を採用:
      Major (X): 構造的な大変更（互換性なし）
      Minor (Y): 新しい変数や機能の追加
      Patch (Z): 小さな修正・表現の調整
    """
    name: str                          # プロンプトの識別名
    version: str                       # バージョン番号 (例: "1.2.3")
    template: str                      # プロンプトテンプレート本文 ({{変数名}} 形式)
    variables: list[str]               # テンプレート内の変数リスト
    environment: PromptEnvironment     # デプロイ先環境
    description: str = ""             # このバージョンの変更内容の説明
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    is_active: bool = True             # このバージョンがアクティブかどうか

    @property
    def version_tuple(self) -> tuple[int, int, int]:
        """バージョン文字列をタプルに変換 (比較用)"""
        parts = self.version.split(".")
        return (int(parts[0]), int(parts[1]), int(parts[2]))

    @property
    def prompt_id(self) -> str:
        """プロンプトの一意ID (名前 + バージョン + 環境)"""
        return f"{self.name}:{self.version}:{self.environment.value}"

    def render(self, **kwargs) -> str:
        """
        テンプレートに変数を埋め込んでプロンプト文字列を生成する。

        Args:
            **kwargs: テンプレート内の {{変数名}} に対応するキーワード引数

        Returns:
            変数が埋め込まれたプロンプト文字列

        Raises:
            ValueError: 必要な変数が不足している場合
        """
        missing = [v for v in self.variables if v not in kwargs]
        if missing:
            raise ValueError(f"プロンプト変数が不足しています: {missing}")

        rendered = self.template
        for key, value in kwargs.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
        return rendered

    def compute_hash(self) -> str:
        """テンプレート内容のハッシュ値を返す (変更検出用)"""
        return hashlib.sha256(self.template.encode()).hexdigest()[:8]


# =========================================================
# プロンプトストア（インメモリ実装）
# =========================================================

class PromptStore:
    """
    プロンプトのバージョン履歴を管理するストア。

    本番環境では DynamoDB や PostgreSQL に置き換えることを推奨します。
    DynamoDB のテーブル設計例:
      PK: prompt_name  SK: version#environment  GSI: environment#is_active
    """

    def __init__(self):
        # {prompt_name: [PromptVersion, ...]} の形式で保存
        self._store: dict[str, list[PromptVersion]] = {}

    def save(self, prompt: PromptVersion) -> None:
        """プロンプトを保存する。同一バージョンの上書きは禁止。"""
        if prompt.name not in self._store:
            self._store[prompt.name] = []

        existing_ids = [p.prompt_id for p in self._store[prompt.name]]
        if prompt.prompt_id in existing_ids:
            raise ValueError(
                f"バージョン {prompt.version} はすでに存在します。"
                "変更は必ず新バージョンとして登録してください（不変性の原則）。"
            )

        self._store[prompt.name].append(prompt)
        print(f"[PromptStore] 保存完了: {prompt.prompt_id} (hash={prompt.compute_hash()})")

    def get_active(self, name: str, environment: PromptEnvironment) -> Optional[PromptVersion]:
        """指定環境でアクティブな最新バージョンを取得する。"""
        versions = self._store.get(name, [])
        active = [
            p for p in versions
            if p.environment == environment and p.is_active
        ]
        if not active:
            return None
        # バージョン番号が最も大きいものを返す
        return max(active, key=lambda p: p.version_tuple)

    def list_versions(self, name: str) -> list[PromptVersion]:
        """プロンプトの全バージョン履歴を取得する。"""
        return sorted(
            self._store.get(name, []),
            key=lambda p: p.version_tuple,
            reverse=True
        )

    def deactivate(self, name: str, version: str, environment: PromptEnvironment) -> None:
        """特定バージョンを無効化する（ロールバック用）。"""
        for p in self._store.get(name, []):
            if p.version == version and p.environment == environment:
                p.is_active = False
                print(f"[PromptStore] 無効化: {p.prompt_id}")
                return
        raise ValueError(f"バージョン {name}:{version}:{environment.value} が見つかりません。")


# =========================================================
# A/Bテスト（カナリアデプロイ）
# =========================================================

@dataclass
class ABTestConfig:
    """
    プロンプトA/Bテストの設定。

    カナリアデプロイ戦略:
      最初は新バリアント（B）に5〜10%のトラフィックをルーティングし、
      結果が安定したら段階的に比率を拡大します。
    """
    name: str                    # テスト名
    variant_a: PromptVersion     # 現行バージョン（コントロール群）
    variant_b: PromptVersion     # 新バージョン（テスト群）
    traffic_to_b: float = 0.1   # Bへのトラフィック比率（0.0〜1.0）
    metrics: dict = field(default_factory=dict)  # 収集したメトリクス


class ABTestRunner:
    """A/Bテストを実行し、メトリクスを収集するクラス。"""

    def __init__(self, config: ABTestConfig):
        self.config = config
        self._results: list[dict] = []

    def select_variant(self, user_id: str) -> tuple[PromptVersion, str]:
        """
        ユーザーIDに基づいてバリアントを選択する。

        ユーザーIDを使うことで、同じユーザーが常に同じバリアントを受け取るように
        します（一貫性の確保）。
        """
        # ユーザーIDのハッシュを使って一貫したルーティングを実現
        hash_val = int(hashlib.md5(user_id.encode()).hexdigest(), 16)
        ratio = (hash_val % 1000) / 1000.0

        if ratio < self.config.traffic_to_b:
            return self.config.variant_b, "B"
        return self.config.variant_a, "A"

    def record_result(
        self,
        user_id: str,
        variant_label: str,
        latency_ms: float,
        token_count: int,
        quality_score: float,
    ) -> None:
        """テスト結果を記録する。"""
        self._results.append({
            "user_id": user_id,
            "variant": variant_label,
            "latency_ms": latency_ms,
            "token_count": token_count,
            "quality_score": quality_score,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def summarize(self) -> dict:
        """
        A/Bテスト結果を集計して比較レポートを返す。

        【評価指標の分類】
        - Computational: レイテンシ、トークン消費量（客観的・自動計測）
        - Semantic:      品質スコア（LLM-as-Judge や人手評価）
        """
        summary = {}
        for label in ["A", "B"]:
            results = [r for r in self._results if r["variant"] == label]
            if not results:
                continue
            count = len(results)
            summary[label] = {
                "sample_count": count,
                "avg_latency_ms": sum(r["latency_ms"] for r in results) / count,
                "avg_token_count": sum(r["token_count"] for r in results) / count,
                "avg_quality_score": sum(r["quality_score"] for r in results) / count,
            }
        return summary


# =========================================================
# デモ実行
# =========================================================

def demo():
    print("=" * 60)
    print("プロンプト管理システム デモ")
    print("=" * 60)

    store = PromptStore()

    # バージョン1.0.0: 初期バージョン
    v1 = PromptVersion(
        name="customer_support",
        version="1.0.0",
        template=(
            "あなたは{{company_name}}のカスタマーサポート担当者です。\n"
            "以下のお問い合わせに日本語で丁寧に回答してください。\n\n"
            "お問い合わせ: {{user_query}}"
        ),
        variables=["company_name", "user_query"],
        environment=PromptEnvironment.PRODUCTION,
        description="初期リリース版",
    )
    store.save(v1)

    # バージョン1.1.0: 回答フォーマット追加（Minor変更）
    v2 = PromptVersion(
        name="customer_support",
        version="1.1.0",
        template=(
            "あなたは{{company_name}}のカスタマーサポート担当者です。\n"
            "以下のお問い合わせに日本語で丁寧に回答してください。\n\n"
            "お問い合わせ: {{user_query}}\n\n"
            "回答は以下の形式でお願いします:\n"
            "1. 問題の確認\n2. 解決策\n3. 次のステップ"
        ),
        variables=["company_name", "user_query"],
        environment=PromptEnvironment.PRODUCTION,
        description="回答フォーマットを構造化（品質向上のため）",
    )
    store.save(v2)

    # プロンプトレンダリングのデモ
    active = store.get_active("customer_support", PromptEnvironment.PRODUCTION)
    if active:
        rendered = active.render(
            company_name="株式会社サンプル",
            user_query="注文した商品がまだ届いていません。",
        )
        print(f"\n[アクティブバージョン] {active.version}")
        print(f"[レンダリング結果]\n{rendered}")

    # A/Bテストのデモ
    print("\n" + "-" * 40)
    print("A/Bテスト デモ")
    print("-" * 40)

    ab_config = ABTestConfig(
        name="format_test_2024",
        variant_a=v1,
        variant_b=v2,
        traffic_to_b=0.2,  # 新バージョンに20%を流す
    )
    runner = ABTestRunner(ab_config)

    # 10人分のテスト結果をシミュレート
    for i in range(10):
        user_id = f"user_{i:03d}"
        variant, label = runner.select_variant(user_id)
        # 実際の本番環境ではここでLLM APIを呼び出します
        runner.record_result(
            user_id=user_id,
            variant_label=label,
            latency_ms=random.uniform(500, 1500),
            token_count=random.randint(100, 300),
            quality_score=random.uniform(0.7, 1.0),
        )

    summary = runner.summarize()
    print("\n[A/Bテスト 集計結果]")
    for label, stats in summary.items():
        print(f"  バリアント {label}: {json.dumps(stats, indent=4, ensure_ascii=False)}")

    # バージョン履歴の表示
    print("\n[バージョン履歴]")
    for pv in store.list_versions("customer_support"):
        print(f"  v{pv.version} | {pv.environment.value} | active={pv.is_active} | {pv.description}")


if __name__ == "__main__":
    demo()
