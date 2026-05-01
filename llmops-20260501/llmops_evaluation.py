# PoC品質 - 本番利用前に認証・エラーハンドリング・テストを追加してください
"""
LLMOps 評価フレームワーク
==========================
RAGシステムとLLM出力の品質を多角的に評価します。

【なぜLLM評価は難しいか】
従来のMLOps: 精度・F1スコアなど定量指標で自動評価できる
LLMOps:      「どれほど役立つか・安全か」という主観的判断を含む
             → LLM-as-Judge・人手評価・統計的手法の組み合わせが必要

【評価の3つのアプローチ】
1. 自動評価（RAGASスタイル）: LLMをジャッジとして使用
2. G-Eval（CoTアプローチ）:   Chain-of-Thoughtで評価基準を細分化
3. 人手評価との組み合わせ:   自動評価の校正と信頼性確保
"""

import json
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional


# =========================================================
# 評価メトリクスの定義
# =========================================================

class MetricName(str, Enum):
    """評価メトリクスの名前（RAGASスタイル）"""
    FAITHFULNESS = "faithfulness"          # 忠実性: 回答がコンテキストに基づいているか
    ANSWER_RELEVANCY = "answer_relevancy"  # 回答関連性: 質問に関連しているか
    CONTEXT_PRECISION = "context_precision"  # コンテキスト精度: 取得した文書の精度
    CONTEXT_RECALL = "context_recall"     # コンテキスト再現率: 必要な情報を取得できているか
    HALLUCINATION_RATE = "hallucination_rate"  # ハルシネーション率（低いほど良い）
    TOXICITY = "toxicity"                  # 毒性スコア（低いほど良い）


@dataclass
class EvaluationResult:
    """1件の評価結果"""
    metric: MetricName
    score: float              # 0〜1の範囲（高いほど良い。毒性・ハルシネーションは逆）
    reasoning: str            # スコアの根拠（LLMジャッジの説明）
    confidence: float = 1.0  # 評価の信頼度
    evaluated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def is_passing(self, threshold: float = 0.7) -> bool:
        """閾値を超えているか（ハルシネーション・毒性は逆転して評価）"""
        if self.metric in (MetricName.HALLUCINATION_RATE, MetricName.TOXICITY):
            return self.score <= (1.0 - threshold)
        return self.score >= threshold


@dataclass
class RAGTestCase:
    """
    RAGシステムの評価用テストケース。

    【各フィールドの説明】
    query:           ユーザーの質問
    retrieved_context: RAGが検索してきた参照文書リスト
    generated_answer: LLMが生成した回答
    ground_truth:    正解（人手でラベリング）
    """
    query: str
    retrieved_context: list[str]
    generated_answer: str
    ground_truth: Optional[str] = None
    metadata: dict = field(default_factory=dict)


# =========================================================
# RAGASスタイル評価器
# =========================================================

class RAGASEvaluator:
    """
    RAGASの主要4メトリクスを実装した評価クラス。

    【RAGASとは】
    RAGシステムの品質を体系的に評価するオープンソースフレームワーク。
    スコアは全て0〜1の範囲で出力します。

    【本番利用時の注意】
    - LLM-as-Judgeは本番クリティカルパスで同期実行してはいけません
    - バックグラウンドで非同期に、日次5%サンプリングで評価します
    - ジャッジモデルは評価対象モデルとは別のモデルを使うことが推奨
    """

    def __init__(self, judge_model_id: str = "anthropic.claude-3-haiku-20240307-v1:0"):
        """
        Args:
            judge_model_id: ジャッジとして使うLLMのモデルID
                           （本番では boto3 で Bedrock API を呼び出します）
        """
        self.judge_model_id = judge_model_id

    def evaluate_faithfulness(self, test_case: RAGTestCase) -> EvaluationResult:
        """
        忠実性（Faithfulness）を評価する。

        【忠実性とは】
        生成した回答が、取得したコンテキスト（参照文書）の内容に基づいているか。
        コンテキストにない情報を"作り出している"場合、ハルシネーションとして検出されます。

        計算方法:
          1. 回答を複数の主張（claim）に分解
          2. 各主張がコンテキストで支持されているかを確認
          3. 支持された主張数 / 総主張数 = 忠実性スコア
        """
        score, reasoning = self._mock_judge_faithfulness(test_case)
        return EvaluationResult(
            metric=MetricName.FAITHFULNESS,
            score=score,
            reasoning=reasoning,
        )

    def evaluate_answer_relevancy(self, test_case: RAGTestCase) -> EvaluationResult:
        """
        回答関連性（Answer Relevancy）を評価する。

        【計算方法】
        1. LLMジャッジが生成した回答から「この回答が答えている質問は何か？」を逆生成
        2. 逆生成した質問と元の質問のコサイン類似度を計算
        3. 複数回試行して平均を取る
        """
        score, reasoning = self._mock_judge_relevancy(test_case)
        return EvaluationResult(
            metric=MetricName.ANSWER_RELEVANCY,
            score=score,
            reasoning=reasoning,
        )

    def evaluate_context_precision(self, test_case: RAGTestCase) -> EvaluationResult:
        """
        コンテキスト精度（Context Precision）を評価する。

        【コンテキスト精度とは】
        RAGが取得してきた文書の中に、回答生成に役立った文書がどれだけあるか。
        不要な文書を大量に取得しているなら、検索の精度を改善する必要があります。

        計算方法:
          関連文書数 / 取得文書総数 = コンテキスト精度
        """
        score, reasoning = self._mock_judge_context_precision(test_case)
        return EvaluationResult(
            metric=MetricName.CONTEXT_PRECISION,
            score=score,
            reasoning=reasoning,
        )

    def evaluate_context_recall(self, test_case: RAGTestCase) -> EvaluationResult:
        """
        コンテキスト再現率（Context Recall）を評価する。

        正解回答に含まれる情報のうち、取得したコンテキストに含まれている割合。
        スコアが低い場合、ベクトル検索やチャンキング戦略の改善が必要です。
        Ground Truthが必要なため、自動計算にはラベル付きデータが必要です。
        """
        if not test_case.ground_truth:
            return EvaluationResult(
                metric=MetricName.CONTEXT_RECALL,
                score=0.0,
                reasoning="Ground Truthが設定されていないため評価不可。",
                confidence=0.0,
            )
        score, reasoning = self._mock_judge_context_recall(test_case)
        return EvaluationResult(
            metric=MetricName.CONTEXT_RECALL,
            score=score,
            reasoning=reasoning,
        )

    def evaluate_all(self, test_case: RAGTestCase) -> dict[MetricName, EvaluationResult]:
        """全メトリクスをまとめて評価する。"""
        return {
            MetricName.FAITHFULNESS: self.evaluate_faithfulness(test_case),
            MetricName.ANSWER_RELEVANCY: self.evaluate_answer_relevancy(test_case),
            MetricName.CONTEXT_PRECISION: self.evaluate_context_precision(test_case),
            MetricName.CONTEXT_RECALL: self.evaluate_context_recall(test_case),
        }

    # ---- PoC用モック実装（本番では実際のLLM API呼び出しに置き換え） ----

    def _mock_judge_faithfulness(self, tc: RAGTestCase) -> tuple[float, str]:
        context_len = sum(len(c) for c in tc.retrieved_context)
        score = min(0.95, 0.5 + (context_len / 2000))
        return round(score, 3), f"コンテキスト長{context_len}文字を参照し、回答の{score*100:.0f}%がコンテキストで支持されています。"

    def _mock_judge_relevancy(self, tc: RAGTestCase) -> tuple[float, str]:
        overlap = len(set(tc.query.split()) & set(tc.generated_answer.split()))
        score = min(0.98, 0.6 + overlap * 0.05)
        return round(score, 3), f"質問と回答のキーワード重複: {overlap}件。"

    def _mock_judge_context_precision(self, tc: RAGTestCase) -> tuple[float, str]:
        n = len(tc.retrieved_context)
        score = random.uniform(0.6, 0.95) if n > 0 else 0.0
        relevant = int(score * n)
        return round(score, 3), f"取得{n}件中{relevant}件が回答生成に使用されました。"

    def _mock_judge_context_recall(self, tc: RAGTestCase) -> tuple[float, str]:
        score = random.uniform(0.7, 0.99)
        return round(score, 3), "正解情報の大部分がコンテキストに含まれています。"


# =========================================================
# G-Eval（Chain-of-Thought評価）
# =========================================================

@dataclass
class GEvalCriteria:
    """
    G-Evalの評価基準定義。

    【G-Evalとは（2023 EMNLP論文）】
    Chain-of-Thought（CoT）を評価に応用したフレームワーク。
    LLMが自然言語の評価基準を構造化ステップに変換し、
    トークンレベルの確率で重み付けしたスコアを算出します。
    GPT-4バックボーンで人間評価との相関0.514を達成（従来手法を大幅に上回る）。
    """
    name: str
    criteria: str              # 評価基準の自然言語説明
    evaluation_steps: list[str]  # LLMが生成した評価ステップ（CoT）
    scale_min: int = 1
    scale_max: int = 5


class GEvalEvaluator:
    """G-Evalスタイルの評価器。"""

    # 典型的な評価基準のテンプレート
    CRITERIA_TEMPLATES = {
        "correctness": GEvalCriteria(
            name="Correctness",
            criteria="回答が事実として正確であり、質問に対して適切に答えているか評価してください。",
            evaluation_steps=[
                "1. 回答に含まれる事実の主張を全て列挙する",
                "2. 各主張を参照情報と照合して正確性を確認する",
                "3. 誤りや矛盾がある主張の数を数える",
                "4. 回答が質問の核心に答えているかを確認する",
                "5. 1〜5のスコアを付ける（5=完全に正確、1=多くの誤りあり）",
            ],
        ),
        "coherence": GEvalCriteria(
            name="Coherence",
            criteria="回答の論理的一貫性と読みやすさを評価してください。",
            evaluation_steps=[
                "1. 回答の構造（導入・本文・結論）を確認する",
                "2. 文章間の論理的なつながりを評価する",
                "3. 矛盾する記述がないかチェックする",
                "4. 読者にとって理解しやすい表現かを評価する",
                "5. 1〜5のスコアを付ける（5=非常に一貫性あり、1=支離滅裂）",
            ],
        ),
    }

    def evaluate(
        self,
        query: str,
        generated_output: str,
        criteria_name: str = "correctness",
        reference: Optional[str] = None,
    ) -> dict:
        """
        G-Evalスタイルで出力を評価する。

        【本番実装のポイント】
        1. LLMにevaluation_stepsを提示して逐次評価させる
        2. 各スコア（1〜5）のトークン確率を取得
        3. 確率で重み付けした期待値をスコアとして使用
           （例: P(1)=0.1, P(2)=0.2, P(3)=0.3, P(4)=0.3, P(5)=0.1 → 期待値3.1）
        """
        criteria = self.CRITERIA_TEMPLATES.get(criteria_name)
        if not criteria:
            raise ValueError(f"未知の評価基準: {criteria_name}")

        # PoC用モック: 本番では LLM API でトークン確率を取得
        token_probs = self._mock_get_token_probabilities(query, generated_output)
        weighted_score = sum(
            (i + 1) * prob for i, prob in enumerate(token_probs)
        )
        normalized_score = (weighted_score - 1) / (5 - 1)  # 0〜1 に正規化

        return {
            "criteria": criteria.name,
            "raw_score": round(weighted_score, 3),
            "normalized_score": round(normalized_score, 3),
            "token_probabilities": {str(i + 1): round(p, 4) for i, p in enumerate(token_probs)},
            "evaluation_steps_applied": criteria.evaluation_steps,
            "evaluated_at": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def _mock_get_token_probabilities(query: str, output: str) -> list[float]:
        """
        PoC用モック。本番では LLM API の logprobs を使用。
        トークン1〜5のそれぞれが生成される確率のリスト。
        """
        raw = [random.uniform(0.05, 0.4) for _ in range(5)]
        total = sum(raw)
        return [p / total for p in raw]


# =========================================================
# 評価パイプライン（非同期バッチ処理）
# =========================================================

class EvaluationPipeline:
    """
    評価を非同期バッチで実行するパイプライン。

    【重要な原則】
    LLM-as-Judgeは本番のクリティカルパスで実行してはいけません。
    バックグラウンドで非同期に、本番トラフィックの5%をサンプリングして評価します。

    【本番環境でのアーキテクチャ】
    本番リクエスト
      → LLM推論（同期: ユーザーに即座に返す）
      → ログをキュー（SQS等）に記録
    ↑評価パイプライン（非同期）
      ← SQSからメッセージを取得（5%サンプリング）
      → RAGAS / G-Eval で評価
      → 結果をMLflow / CloudWatch に送信
      → 閾値を下回ったらアラート発火
    """

    def __init__(
        self,
        ragas_evaluator: RAGASEvaluator,
        geval_evaluator: GEvalEvaluator,
        sample_rate: float = 0.05,
        passing_threshold: float = 0.7,
    ):
        self.ragas = ragas_evaluator
        self.geval = geval_evaluator
        self.sample_rate = sample_rate
        self.threshold = passing_threshold
        self._evaluation_log: list[dict] = []

    def should_evaluate(self) -> bool:
        """サンプリングレートに基づいて評価するかどうかを決定する。"""
        return random.random() < self.sample_rate

    def run_evaluation(self, test_case: RAGTestCase, force: bool = False) -> Optional[dict]:
        """
        テストケースを評価する。

        Args:
            test_case: 評価対象のテストケース
            force: True なら サンプリング判定をスキップして必ず評価

        Returns:
            評価結果の辞書（サンプリングでスキップされた場合は None）
        """
        if not force and not self.should_evaluate():
            return None

        # RAGAS評価
        ragas_results = self.ragas.evaluate_all(test_case)

        # G-Eval評価
        geval_result = self.geval.evaluate(
            query=test_case.query,
            generated_output=test_case.generated_answer,
            criteria_name="correctness",
        )

        # 合否判定
        failed_metrics = [
            metric.value for metric, result in ragas_results.items()
            if not result.is_passing(self.threshold)
        ]

        evaluation_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "query": test_case.query[:100],
            "ragas_scores": {
                metric.value: round(result.score, 4)
                for metric, result in ragas_results.items()
            },
            "geval_score": geval_result["normalized_score"],
            "overall_pass": len(failed_metrics) == 0,
            "failed_metrics": failed_metrics,
        }

        self._evaluation_log.append(evaluation_record)

        if failed_metrics:
            print(
                f"⚠️ 品質アラート: '{test_case.query[:40]}...' の以下のメトリクスが閾値未満: "
                f"{', '.join(failed_metrics)}"
            )

        return evaluation_record

    def get_quality_report(self) -> dict:
        """評価結果のサマリーレポートを生成する。"""
        if not self._evaluation_log:
            return {"status": "no_evaluations"}

        n = len(self._evaluation_log)
        pass_count = sum(1 for r in self._evaluation_log if r["overall_pass"])

        all_ragas_scores: dict[str, list[float]] = {}
        for record in self._evaluation_log:
            for metric, score in record["ragas_scores"].items():
                if metric not in all_ragas_scores:
                    all_ragas_scores[metric] = []
                all_ragas_scores[metric].append(score)

        avg_geval = sum(r["geval_score"] for r in self._evaluation_log) / n

        return {
            "total_evaluated": n,
            "pass_rate": round(pass_count / n, 4),
            "avg_ragas_scores": {
                metric: round(sum(scores) / len(scores), 4)
                for metric, scores in all_ragas_scores.items()
            },
            "avg_geval_score": round(avg_geval, 4),
            "threshold_used": self.threshold,
        }


# =========================================================
# デモ実行
# =========================================================

def demo():
    print("=" * 60)
    print("LLMOps 評価フレームワーク デモ")
    print("=" * 60)

    ragas = RAGASEvaluator()
    geval = GEvalEvaluator()

    pipeline = EvaluationPipeline(
        ragas_evaluator=ragas,
        geval_evaluator=geval,
        sample_rate=1.0,  # デモのため全件評価
        passing_threshold=0.7,
    )

    test_cases = [
        RAGTestCase(
            query="Pythonのリスト内包表記とは何ですか？",
            retrieved_context=[
                "リスト内包表記は、Pythonでリストを生成する簡潔な構文です。"
                "[式 for 変数 in イテラブル if 条件] の形式で記述します。",
                "従来のforループと同等の処理をより短く書けます。",
            ],
            generated_answer=(
                "リスト内包表記は[式 for 変数 in イテラブル]という構文で"
                "リストを生成するPythonの機能です。forループより簡潔に書けます。"
            ),
            ground_truth="リスト内包表記は[式 for 変数 in イテラブル if 条件]という構文です。",
        ),
        RAGTestCase(
            query="機械学習における過学習の対策は？",
            retrieved_context=[
                "過学習（Overfitting）とは、モデルが訓練データに過度に適合し、"
                "新しいデータに対して汎化できない状態です。",
            ],
            generated_answer=(
                "過学習の対策としては、Dropout、L1/L2正則化、"
                "早期停止（Early Stopping）、データ拡張などがあります。"
            ),
            ground_truth="正則化、Dropout、データ拡張、クロスバリデーションなどが主な対策です。",
        ),
    ]

    print("\n[評価実行]")
    for i, tc in enumerate(test_cases):
        print(f"\n--- テストケース {i + 1}: {tc.query[:40]} ---")
        result = pipeline.run_evaluation(tc, force=True)
        if result:
            print(f"  総合判定: {'✅ Pass' if result['overall_pass'] else '❌ Fail'}")
            print(f"  RAGASスコア:")
            for metric, score in result["ragas_scores"].items():
                status = "✅" if score >= 0.7 else "❌"
                print(f"    {status} {metric}: {score}")
            print(f"  G-Evalスコア: {result['geval_score']:.4f}")

    print("\n" + "=" * 40)
    print("[品質サマリーレポート]")
    report = pipeline.get_quality_report()
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    demo()
