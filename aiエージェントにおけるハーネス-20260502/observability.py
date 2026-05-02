# PoC品質 - 本番利用前に OpenTelemetry SDK や AWS CloudWatch Logs への接続を実装すること
"""
可観測性（Observability）モジュール

AIエージェントハーネスの「4つのテレメトリタイプ」を実装:
  1. 分散トレース  : エージェント実行の全体フローをスパン単位で追跡
  2. 構造化ログ   : ツール呼び出し・決定記録を一貫したフォーマットで記録
  3. メトリクス   : レイテンシ・トークン使用量・エラー率を集計
  4. 評価シグナル : タスク成功率・安全性準拠の構造化計測

OpenTelemetry GenAI Semantic Convention 準拠を目指した設計。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

@dataclass
class Span:
    """
    分散トレースの最小単位「スパン」。
    1つのエージェント実行を root span とし、
    LLM呼び出し・ツール実行を child span として木構造で表現する。
    """
    span_id: str
    name: str
    parent_id: Optional[str] = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: Optional[float] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "running"  # "running" | "ok" | "error"


@dataclass
class Metric:
    """集計メトリクスのカウンタ/ヒストグラム"""
    name: str
    value: float
    unit: str
    timestamp: float = field(default_factory=time.time)
    labels: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 可観測性コレクター
# ---------------------------------------------------------------------------

class ObservabilityCollector:
    """
    ハーネス全体のテレメトリを収集・集計するコレクター。

    本番環境では以下に転送する実装を追加すること:
      - トレース: AWS X-Ray / Jaeger / Datadog APM
      - メトリクス: CloudWatch / Prometheus / Datadog Metrics
      - ログ: CloudWatch Logs / Elasticsearch
      - 評価: 独自評価ダッシュボード / LangSmith
    """

    def __init__(self, service_name: str = "agent-harness") -> None:
        self.service_name = service_name
        self._spans: dict[str, Span] = {}
        self._metrics: list[Metric] = []

        # 集計カウンター
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_tool_calls = 0
        self._total_llm_calls = 0
        self._total_errors = 0
        self._session_count = 0

    # ------------------------------------------------------------------
    # 分散トレース
    # ------------------------------------------------------------------

    def start_span(
        self,
        name: str,
        attributes: Optional[dict[str, Any]] = None,
        parent: Optional[Span] = None,
    ) -> Span:
        """
        新しいスパンを開始する。

        Parameters
        ----------
        name : str
            スパン名（例: "agent_run", "llm_call", "tool_call"）
        attributes : dict, optional
            スパンに付与する属性（モデル名・ツール名・セッションID等）
        parent : Span, optional
            親スパン（child span の場合に指定）

        Returns
        -------
        Span
            開始したスパンオブジェクト（end_span に渡して終了させること）
        """
        span = Span(
            span_id=str(uuid.uuid4())[:8],
            name=name,
            parent_id=parent.span_id if parent else None,
            attributes={
                "service": self.service_name,
                **(attributes or {}),
            },
        )
        self._spans[span.span_id] = span

        logger.debug(
            json.dumps(
                {
                    "event": "span_start",
                    "span_id": span.span_id,
                    "name": name,
                    "parent_id": span.parent_id,
                    **span.attributes,
                },
                ensure_ascii=False,
            )
        )
        return span

    def end_span(
        self,
        span: Span,
        result_attributes: Optional[dict[str, Any]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        """
        スパンを終了し、テレメトリを記録する。

        Parameters
        ----------
        span : Span
            終了するスパン
        result_attributes : dict, optional
            終了時に追加する属性（トークン数・stop_reason等）
        error : Exception, optional
            エラーが発生した場合の例外オブジェクト
        """
        span.end_time = time.monotonic()
        span.status = "error" if error else "ok"

        if result_attributes:
            span.attributes.update(result_attributes)

        duration_ms = (span.end_time - span.start_time) * 1000

        # メトリクスの自動集計
        if span.name == "llm_call":
            self._total_llm_calls += 1
            input_tokens = result_attributes.get("input_tokens", 0) if result_attributes else 0
            output_tokens = result_attributes.get("output_tokens", 0) if result_attributes else 0
            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens
            self._record_metric(
                "llm_call_duration_ms", duration_ms, "ms",
                {"model": span.attributes.get("model", "unknown")}
            )
            self._record_metric("llm_input_tokens", input_tokens, "tokens")
            self._record_metric("llm_output_tokens", output_tokens, "tokens")

        elif span.name == "tool_call":
            self._total_tool_calls += 1
            if error or (result_attributes and result_attributes.get("is_error")):
                self._total_errors += 1
            self._record_metric(
                "tool_call_duration_ms", duration_ms, "ms",
                {"tool": span.attributes.get("tool", "unknown")}
            )

        elif span.name == "agent_run":
            self._session_count += 1

        log_record = {
            "event": "span_end",
            "span_id": span.span_id,
            "name": span.name,
            "status": span.status,
            "duration_ms": round(duration_ms, 2),
            **span.attributes,
        }
        if error:
            log_record["error"] = str(error)
            logger.error(json.dumps(log_record, ensure_ascii=False))
        else:
            logger.info(json.dumps(log_record, ensure_ascii=False))

    # ------------------------------------------------------------------
    # 構造化ログ（ツール呼び出し・決定記録）
    # ------------------------------------------------------------------

    def log_tool_call(
        self,
        tool_name: str,
        tool_input: dict,
        tool_result: Any,
        session_id: str,
        is_error: bool = False,
    ) -> None:
        """
        ツール呼び出しの入出力を構造化ログとして記録する。

        センシティブデータのマスク処理:
          本番では tool_input / tool_result の機密フィールド
          （パスワード・APIキー・個人情報等）をマスクすること。
        """
        record = {
            "event": "tool_call",
            "session_id": session_id,
            "tool": tool_name,
            "is_error": is_error,
            # セキュリティ: 本番では以下をサニタイズ/マスクする
            "input_summary": self._truncate(str(tool_input), 200),
            "result_summary": self._truncate(str(tool_result), 200),
            "timestamp": time.time(),
        }
        if is_error:
            logger.error(json.dumps(record, ensure_ascii=False))
        else:
            logger.info(json.dumps(record, ensure_ascii=False))

    def log_decision(
        self,
        decision: str,
        reason: str,
        session_id: str,
        context: Optional[dict] = None,
    ) -> None:
        """エージェントの判断・決定を記録する（監査ログ用途）"""
        record = {
            "event": "agent_decision",
            "session_id": session_id,
            "decision": decision,
            "reason": reason,
            "context": context or {},
            "timestamp": time.time(),
        }
        logger.info(json.dumps(record, ensure_ascii=False))

    # ------------------------------------------------------------------
    # メトリクス
    # ------------------------------------------------------------------

    def _record_metric(
        self,
        name: str,
        value: float,
        unit: str,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """内部メトリクスを記録する"""
        self._metrics.append(
            Metric(name=name, value=value, unit=unit, labels=labels or {})
        )

    def get_summary(self) -> dict[str, Any]:
        """
        収集したテレメトリのサマリーを返す。
        ダッシュボード表示・コスト最適化・パフォーマンス分析に使用する。
        """
        return {
            "sessions": self._session_count,
            "llm_calls": self._total_llm_calls,
            "tool_calls": self._total_tool_calls,
            "errors": self._total_errors,
            "error_rate": (
                self._total_errors / self._total_tool_calls
                if self._total_tool_calls > 0
                else 0.0
            ),
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_tokens": self._total_input_tokens + self._total_output_tokens,
            # トークンコスト概算（モデルによって異なるため適宜調整すること）
            "estimated_cost_usd": self._estimate_cost(),
        }

    def _estimate_cost(self) -> float:
        """
        トークン使用量からコストを概算する（スケルトン）。
        実際の単価は Anthropic の公式料金ページを参照すること。
        """
        # claude-opus-4-7 の概算単価（2026年時点の参考値）
        # 本番では実際のモデル別単価テーブルを参照すること
        INPUT_PRICE_PER_M_TOKENS = 15.0   # $15/M input tokens（参考値）
        OUTPUT_PRICE_PER_M_TOKENS = 75.0  # $75/M output tokens（参考値）

        input_cost = (self._total_input_tokens / 1_000_000) * INPUT_PRICE_PER_M_TOKENS
        output_cost = (self._total_output_tokens / 1_000_000) * OUTPUT_PRICE_PER_M_TOKENS
        return round(input_cost + output_cost, 6)

    # ------------------------------------------------------------------
    # 評価シグナル（Evaluations）
    # ------------------------------------------------------------------

    def record_evaluation(
        self,
        session_id: str,
        task_success: bool,
        accuracy_score: Optional[float] = None,
        safety_compliant: bool = True,
        notes: str = "",
    ) -> None:
        """
        タスク成功率・正確性・安全性準拠を記録する評価シグナル。

        2025年以降の本番エージェントにはこの評価記録がベースライン要件。
        CI/CD パイプラインに組み込んで回帰を早期検出すること。
        """
        record = {
            "event": "evaluation",
            "session_id": session_id,
            "task_success": task_success,
            "accuracy_score": accuracy_score,
            "safety_compliant": safety_compliant,
            "notes": notes,
            "timestamp": time.time(),
        }
        logger.info(json.dumps(record, ensure_ascii=False))

        # メトリクスとして集計
        self._record_metric("task_success", float(task_success), "bool")
        if accuracy_score is not None:
            self._record_metric("accuracy_score", accuracy_score, "score")
        self._record_metric("safety_compliant", float(safety_compliant), "bool")

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """文字列を指定長に切り詰める（センシティブデータのサイズ制御用）"""
        return text[:max_len] + "..." if len(text) > max_len else text

    def export_traces(self) -> list[dict]:
        """収集済みスパンをエクスポートする（外部トレースバックエンドへの転送用）"""
        return [
            {
                "span_id": s.span_id,
                "name": s.name,
                "parent_id": s.parent_id,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "duration_ms": (
                    round((s.end_time - s.start_time) * 1000, 2)
                    if s.end_time
                    else None
                ),
                "status": s.status,
                "attributes": s.attributes,
            }
            for s in self._spans.values()
        ]


# ---------------------------------------------------------------------------
# 使用例
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    obs = ObservabilityCollector(service_name="demo-agent")

    # エージェント実行全体の root span
    root = obs.start_span("agent_run", {"session_id": "sess-001"})

    # LLM呼び出しの child span
    llm_span = obs.start_span("llm_call", {"model": "claude-opus-4-7"}, parent=root)
    time.sleep(0.05)  # 推論時間のシミュレーション
    obs.end_span(llm_span, {"input_tokens": 512, "output_tokens": 128, "stop_reason": "tool_use"})

    # ツール実行の child span
    tool_span = obs.start_span("tool_call", {"tool": "read_file"}, parent=root)
    time.sleep(0.01)  # ツール実行時間のシミュレーション
    obs.end_span(tool_span, {"is_error": False})
    obs.log_tool_call("read_file", {"path": "main.py"}, "def main(): ...", "sess-001")

    # 2回目の LLM呼び出し（最終応答）
    llm_span2 = obs.start_span("llm_call", {"model": "claude-opus-4-7"}, parent=root)
    time.sleep(0.03)
    obs.end_span(llm_span2, {"input_tokens": 640, "output_tokens": 256, "stop_reason": "end_turn"})

    # root span の終了
    obs.end_span(root, {"turns": 2, "status": "success"})

    # 評価シグナルの記録
    obs.record_evaluation("sess-001", task_success=True, accuracy_score=0.95)

    # サマリー表示
    print("\n=== テレメトリサマリー ===")
    print(json.dumps(obs.get_summary(), ensure_ascii=False, indent=2))
