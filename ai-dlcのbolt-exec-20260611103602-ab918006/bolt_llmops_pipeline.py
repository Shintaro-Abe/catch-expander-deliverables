# PoC品質 - 本番利用前にエラーハンドリング・ロギング・評価メトリクスの実装が必要

"""
Bolt × LLMOps 統合パイプライン

Bolt.new は AI-DLC（AI開発ライフサイクル）の上流フェーズ（プロトタイピング）に
特化したツールである。このモジュールでは Bolt が生成したコードを
LLMOps パイプラインへ引き渡すブリッジ層を実装する。

統合パターン:
  Bolt.new (プロトタイプ生成)
    → GitHub (コード管理)
    → GitHub Actions (CI/CD)
    → 専用 LLMOps ツール (LangSmith / MLflow / W&B)
    → 本番デプロイ

AI-DLC における位置づけ: プロトタイプ → 本番移行フェーズの橋渡し
"""

import os
import json
import hashlib
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable
from datetime import datetime, timezone

import anthropic


# ---- データモデル --------------------------------------------------------

@dataclass
class GenerationMetrics:
    """LLM コード生成の品質・コストメトリクス"""
    run_id: str
    prompt_hash: str           # 同一プロンプトの重複実行検出用
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int     # キャッシュヒット分（コスト削減済み）
    cache_creation_tokens: int
    latency_ms: float
    success: bool
    error_message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def estimated_cost_usd(self) -> float:
        """
        Claude Sonnet 4.6 の概算コスト計算。
        キャッシュリードは通常入力の10%のコストになる。
        実際の料金は Anthropic の公式ページを参照のこと。
        """
        INPUT_COST_PER_1M  = 3.00   # $3 / 1M tokens
        OUTPUT_COST_PER_1M = 15.00  # $15 / 1M tokens
        CACHE_READ_COST    = 0.30   # $0.30 / 1M tokens (90%オフ)

        cost = (
            (self.input_tokens / 1_000_000) * INPUT_COST_PER_1M
            + (self.output_tokens / 1_000_000) * OUTPUT_COST_PER_1M
            + (self.cache_read_tokens / 1_000_000) * CACHE_READ_COST
        )
        return round(cost, 6)


@dataclass
class PipelineStage:
    """LLMOps パイプラインの1ステージ"""
    name: str
    prompt_template: str
    validator: Callable[[str], bool] | None = None
    max_retries: int = 2


# ---- LLMOps パイプライン ------------------------------------------------

class BoltLLMOpsPipeline:
    """
    Bolt スタイルの AI コード生成を LLMOps パイプラインとして管理するクラス。

    主な機能:
    1. マルチステージ生成（要件定義 → 設計 → 実装 → テストコード）
    2. プロンプトキャッシュによるコスト最適化
    3. メトリクス収集（トークン数・レイテンシ・コスト）
    4. 生成結果のバリデーション + 自動リトライ
    """

    SYSTEM_PROMPT = """あなたはフルスタック Web 開発の専門家 AI です。
指示に従い、実装可能な高品質なコードを生成してください。
コードはすべてのファイルが揃った状態で提供し、不完全な実装は避けてください。"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self.client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self.model = model
        self._metrics_log: list[GenerationMetrics] = []

    # ---- コアメソッド ----------------------------------------------------

    def run_stage(self, stage: PipelineStage, context: dict[str, Any]) -> tuple[str, GenerationMetrics]:
        """
        単一ステージを実行する。バリデーション失敗時は自動リトライ。

        context: 前ステージの出力などを渡すテンプレート変数
        """
        prompt = stage.prompt_template.format(**context)
        prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:8]
        run_id = f"{stage.name}-{prompt_hash}-{int(time.time())}"

        last_error = ""
        for attempt in range(stage.max_retries + 1):
            metrics, output = self._call_llm(run_id, prompt, prompt_hash)

            if stage.validator is None or stage.validator(output):
                self._metrics_log.append(metrics)
                return output, metrics

            last_error = f"バリデーション失敗 (試行 {attempt + 1}/{stage.max_retries + 1})"
            # リトライ用にプロンプトにフィードバックを付加
            prompt = prompt + f"\n\n[前回の出力が要件を満たしませんでした。再生成してください]"

        metrics.success = False
        metrics.error_message = last_error
        self._metrics_log.append(metrics)
        raise RuntimeError(f"ステージ '{stage.name}' が最大リトライ数に達しました: {last_error}")

    def run_pipeline(self, stages: list[PipelineStage], initial_context: dict) -> dict:
        """
        複数ステージを順次実行し、前ステージの出力を次ステージに引き渡す。

        Bolt の「Standard モード（高速）」と「Max モード（複雑タスク）」の
        使い分けパターンを模倣する。
        """
        context = dict(initial_context)
        results = {}

        for stage in stages:
            print(f"[Pipeline] ステージ実行中: {stage.name}")
            output, metrics = self.run_stage(stage, context)
            results[stage.name] = output
            context[f"{stage.name}_output"] = output
            print(f"  完了 - {metrics.input_tokens}入力/{metrics.output_tokens}出力トークン "
                  f"(${metrics.estimated_cost_usd:.4f})")

        return results

    def _call_llm(self, run_id: str, prompt: str, prompt_hash: str) -> tuple[GenerationMetrics, str]:
        start = time.monotonic()
        success = True
        output = ""
        error_msg = ""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8192,
                system=[
                    {
                        "type": "text",
                        "text": self.SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},  # プロンプトキャッシュ有効化
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            )
            output = response.content[0].text
            usage = response.usage
            input_tokens  = usage.input_tokens
            output_tokens  = usage.output_tokens
            cache_read     = getattr(usage, "cache_read_input_tokens", 0)
            cache_creation = getattr(usage, "cache_creation_input_tokens", 0)

        except anthropic.APIError as exc:
            success = False
            error_msg = str(exc)
            input_tokens = output_tokens = cache_read = cache_creation = 0

        latency_ms = (time.monotonic() - start) * 1000

        metrics = GenerationMetrics(
            run_id=run_id,
            prompt_hash=prompt_hash,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            latency_ms=round(latency_ms, 2),
            success=success,
            error_message=error_msg,
        )
        return metrics, output

    # ---- メトリクス集計 --------------------------------------------------

    def get_metrics_summary(self) -> dict:
        """パイプライン全体のメトリクスサマリーを返す"""
        if not self._metrics_log:
            return {}

        total_cost    = sum(m.estimated_cost_usd for m in self._metrics_log)
        total_input   = sum(m.input_tokens for m in self._metrics_log)
        total_output  = sum(m.output_tokens for m in self._metrics_log)
        total_cache   = sum(m.cache_read_tokens for m in self._metrics_log)
        avg_latency   = sum(m.latency_ms for m in self._metrics_log) / len(self._metrics_log)
        success_rate  = sum(1 for m in self._metrics_log if m.success) / len(self._metrics_log)

        cache_savings_ratio = total_cache / max(total_input, 1)

        return {
            "total_runs": len(self._metrics_log),
            "success_rate": f"{success_rate:.1%}",
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "cache_hit_tokens": total_cache,
            "cache_savings_ratio": f"{cache_savings_ratio:.1%}",
            "total_estimated_cost_usd": round(total_cost, 4),
            "avg_latency_ms": round(avg_latency, 2),
        }

    def export_metrics(self, path: str = "bolt_metrics.json") -> None:
        """メトリクスを JSON ファイルに出力（MLflow / LangSmith へのインポート用）"""
        data = {
            "summary": self.get_metrics_summary(),
            "runs": [asdict(m) for m in self._metrics_log],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"メトリクス出力: {path}")


# ---- バリデーター例 -------------------------------------------------------

def has_package_json(output: str) -> bool:
    """package.json が生成に含まれているかチェック"""
    return "package.json" in output

def has_react_component(output: str) -> bool:
    """React コンポーネントが含まれているかチェック"""
    return "export default" in output and ("tsx" in output or "jsx" in output)


# ---- 使用例 --------------------------------------------------------------

def main() -> None:
    pipeline = BoltLLMOpsPipeline()

    # AI-DLC の上流から下流への多段生成パイプライン
    stages = [
        PipelineStage(
            name="requirements",
            prompt_template=(
                "以下のアプリ概要から技術要件を箇条書きで整理してください:\n\n"
                "アプリ概要: {app_description}"
            ),
        ),
        PipelineStage(
            name="scaffold",
            prompt_template=(
                "以下の技術要件に基づき、React + TypeScript のプロジェクト雛形を生成してください。\n\n"
                "要件:\n{requirements_output}\n\n"
                "package.json と src/App.tsx を含む最小限の実装を提供してください。"
            ),
            validator=has_package_json,
            max_retries=1,
        ),
        PipelineStage(
            name="test_code",
            prompt_template=(
                "以下のコードに対して Vitest を使ったユニットテストを生成してください:\n\n"
                "{scaffold_output}"
            ),
        ),
    ]

    initial_context = {
        "app_description": "タスク管理SaaS。チームメンバーにタスクを割り当て、進捗を追跡できる。"
    }

    try:
        results = pipeline.run_pipeline(stages, initial_context)
        print("\n=== パイプライン完了 ===")
        print(json.dumps(pipeline.get_metrics_summary(), ensure_ascii=False, indent=2))
        pipeline.export_metrics()
    except RuntimeError as exc:
        print(f"パイプラインエラー: {exc}")


if __name__ == "__main__":
    main()
