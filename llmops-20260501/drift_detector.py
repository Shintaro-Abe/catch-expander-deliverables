# PoC品質 - 本番利用前に認証・エラーハンドリング・テストを追加してください
"""
ドリフト検知モジュール
======================
LLMOpsにおける「ドリフト（品質劣化）」を統計的手法で検知します。

【4種類のドリフト】
1. データドリフト:      入力データの統計的分布が変化（新しいスラングの出現など）
2. コンセプトドリフト:  入出力の関係性が変化（言葉の意味の進化など）
3. プロンプトドリフト:  プロンプト未変更でもLLMの挙動が変化（モデルのサイレント更新）
4. モデルドリフト:      学習データが古くなり性能低下（6ヶ月放置でエラー率35%上昇の事例あり）

【検知の2層アプローチ】
Layer 1: 統計的検知（自動化・高速）→ Wasserstein距離、PSI、KLダイバージェンス
Layer 2: LLMセマンティック分析（解釈・根本原因特定）→ LLM-as-Judgeで分類
"""

import json
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


# =========================================================
# 統計的ドリフト検知（Layer 1）
# =========================================================

class StatisticalDriftDetector:
    """
    埋め込みベクトルの統計的分布を比較してドリフトを検知するクラス。

    【埋め込みベクトルとは】
    テキストを数値のリスト（ベクトル）に変換したもの。
    意味が近いテキスト同士は、ベクトル空間でも近い位置に来ます。
    例: "今日の天気" と "明日の気象" は近いベクトル、
        "今日の天気" と "株式投資" は遠いベクトル。
    """

    @staticmethod
    def wasserstein_distance_1d(dist_a: list[float], dist_b: list[float]) -> float:
        """
        1次元Wasserstein距離（Earth Mover's Distance）を計算する。

        【Wasserstein距離とは】
        2つの確率分布の「差異」を測る指標。
        「AをBの形に変えるために砂をどれだけ運ぶ必要があるか」というイメージ。
        値が大きいほど分布の差異が大きい（ドリフトが大きい）。

        多次元の埋め込みの場合は、各次元を独立に計算して平均を取ります。
        """
        sorted_a = sorted(dist_a)
        sorted_b = sorted(dist_b)

        if len(sorted_a) != len(sorted_b):
            # サイズが異なる場合は等間隔にリサンプリング
            min_len = min(len(sorted_a), len(sorted_b))
            step_a = len(sorted_a) / min_len
            step_b = len(sorted_b) / min_len
            sorted_a = [sorted_a[int(i * step_a)] for i in range(min_len)]
            sorted_b = [sorted_b[int(i * step_b)] for i in range(min_len)]

        return sum(abs(a - b) for a, b in zip(sorted_a, sorted_b)) / len(sorted_a)

    @staticmethod
    def population_stability_index(
        baseline: list[float],
        current: list[float],
        n_bins: int = 10,
    ) -> float:
        """
        PSI（Population Stability Index: 母集団安定性指数）を計算する。

        【PSIの解釈基準】
        PSI < 0.1:  分布は安定（変化なし）
        PSI 0.1〜0.2: 軽微な変化あり（注意して監視）
        PSI > 0.2:  重大な変化あり（モデルの再評価が必要）
        """
        all_values = baseline + current
        min_val = min(all_values)
        max_val = max(all_values)
        bin_width = (max_val - min_val) / n_bins or 1.0

        def get_bin_counts(data: list[float]) -> list[float]:
            counts = [0] * n_bins
            for v in data:
                idx = min(int((v - min_val) / bin_width), n_bins - 1)
                counts[idx] += 1
            # 0除算を避けるため最小値を設定（ラプラス平滑化）
            total = len(data)
            return [max(c / total, 1e-6) for c in counts]

        baseline_pct = get_bin_counts(baseline)
        current_pct = get_bin_counts(current)

        psi = sum(
            (curr - base) * math.log(curr / base)
            for base, curr in zip(baseline_pct, current_pct)
        )
        return psi

    @staticmethod
    def kl_divergence(
        baseline: list[float],
        current: list[float],
        n_bins: int = 10,
    ) -> float:
        """
        KLダイバージェンス（カルバック・ライブラー情報量）を計算する。

        【KLダイバージェンスとは】
        確率分布Pと確率分布Qがどれだけ異なるかを測る非対称な指標。
        KL(P||Q) = 0 なら完全に同じ分布。値が大きいほど差異が大きい。
        非対称（KL(P||Q) ≠ KL(Q||P)）なので注意。
        """
        all_values = baseline + current
        min_val = min(all_values)
        max_val = max(all_values)
        bin_width = (max_val - min_val) / n_bins or 1.0

        def to_dist(data: list[float]) -> list[float]:
            counts = [0.0] * n_bins
            for v in data:
                idx = min(int((v - min_val) / bin_width), n_bins - 1)
                counts[idx] += 1
            total = len(data)
            return [max(c / total, 1e-9) for c in counts]

        p = to_dist(baseline)
        q = to_dist(current)

        return sum(pi * math.log(pi / qi) for pi, qi in zip(p, q))

    @staticmethod
    def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        """
        コサイン類似度を計算する（2つのベクトルの方向の類似度）。

        値の範囲: -1〜1
          1.0: 完全に同じ方向（意味が近い）
          0.0: 直交（無関係）
         -1.0: 反対方向（意味が反対）
        """
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a ** 2 for a in vec_a))
        norm_b = math.sqrt(sum(b ** 2 for b in vec_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


# =========================================================
# ドリフト監視システム
# =========================================================

@dataclass
class DriftAlert:
    """ドリフト検知時のアラート情報"""
    timestamp: str
    metric_name: str          # 使用した検知手法
    baseline_value: float     # ベースライン期間の値
    current_value: float      # 現在の値
    threshold: float          # アラート閾値
    severity: str             # "warning" または "critical"
    message: str              # アラートメッセージ


@dataclass
class DriftMonitorConfig:
    """
    ドリフト監視の設定。

    【閾値の設定指針】
    Wasserstein距離: 0.1を超えたら要注意、0.2を超えたら要対応
    PSI:            0.1を超えたら軽微、0.2を超えたら重大
    KL距離:         0.1を超えたら要確認
    """
    wasserstein_warning: float = 0.1
    wasserstein_critical: float = 0.2
    psi_warning: float = 0.1
    psi_critical: float = 0.2
    kl_warning: float = 0.1
    kl_critical: float = 0.2
    sample_rate: float = 0.05   # 本番トラフィックのサンプリング率（5%）


class DriftMonitor:
    """
    本番環境のLLM入力分布を継続監視するクラス。

    【実装の流れ】
    1. ベースライン確立: 安定期間の埋め込みをサンプリングして保存
    2. 本番モニタリング: 本番リクエストを一定割合でサンプリング
    3. 分布比較: Wasserstein距離・PSI・KL距離で定量化
    4. アラート: 閾値超過時に通知（CloudWatch Alarm / PagerDuty / Slack等へ連携）
    5. セマンティック分析: アラート後にLLM-as-Judgeで根本原因を特定
    """

    def __init__(
        self,
        config: DriftMonitorConfig,
        alert_handler: Optional[Callable[[DriftAlert], None]] = None,
    ):
        self.config = config
        self.detector = StatisticalDriftDetector()
        self.alert_handler = alert_handler or self._default_alert_handler
        self._baseline: list[list[float]] = []
        self._current_window: list[list[float]] = []
        self._alerts: list[DriftAlert] = []

    def set_baseline(self, embeddings: list[list[float]]) -> None:
        """ベースライン期間の埋め込みベクトル群を登録する。"""
        self._baseline = embeddings
        print(f"[DriftMonitor] ベースライン設定完了: {len(embeddings)} サンプル")

    def add_sample(self, embedding: list[float]) -> None:
        """
        本番リクエストの埋め込みをサンプリングして追加する。

        サンプリング率（デフォルト5%）を適用するため、
        全リクエストではなく一部のみを記録します。
        これにより推論レイテンシへの影響を最小化します。
        """
        if random.random() < self.config.sample_rate:
            self._current_window.append(embedding)

    def check_drift(self, min_samples: int = 30) -> list[DriftAlert]:
        """
        現在のウィンドウとベースラインを比較してドリフトを検知する。

        Args:
            min_samples: 検査に必要な最小サンプル数（統計的有意性を確保）
        """
        if len(self._current_window) < min_samples:
            print(f"[DriftMonitor] サンプル不足: {len(self._current_window)}/{min_samples}")
            return []
        if not self._baseline:
            print("[DriftMonitor] ベースラインが未設定です。")
            return []

        new_alerts = []

        # 各次元の値を1次元リストとして抽出（多次元埋め込みを各次元に分解）
        dim = len(self._baseline[0])
        for d in range(min(dim, 5)):  # PoC用に最初の5次元のみチェック
            baseline_vals = [emb[d] for emb in self._baseline]
            current_vals = [emb[d] for emb in self._current_window]

            # Wasserstein距離
            w_dist = self.detector.wasserstein_distance_1d(baseline_vals, current_vals)
            new_alerts.extend(self._evaluate_metric(
                f"wasserstein_dim_{d}", w_dist,
                self.config.wasserstein_warning,
                self.config.wasserstein_critical,
            ))

        # PSI（全次元の平均を使用）
        baseline_norms = [
            math.sqrt(sum(v ** 2 for v in emb)) for emb in self._baseline
        ]
        current_norms = [
            math.sqrt(sum(v ** 2 for v in emb)) for emb in self._current_window
        ]
        psi = self.detector.population_stability_index(baseline_norms, current_norms)
        new_alerts.extend(self._evaluate_metric(
            "psi_norm", psi,
            self.config.psi_warning,
            self.config.psi_critical,
        ))

        # KLダイバージェンス
        kl = self.detector.kl_divergence(baseline_norms, current_norms)
        new_alerts.extend(self._evaluate_metric(
            "kl_divergence_norm", kl,
            self.config.kl_warning,
            self.config.kl_critical,
        ))

        for alert in new_alerts:
            self._alerts.append(alert)
            self.alert_handler(alert)

        return new_alerts

    def _evaluate_metric(
        self,
        metric_name: str,
        value: float,
        warning_threshold: float,
        critical_threshold: float,
    ) -> list[DriftAlert]:
        alerts = []
        if value >= critical_threshold:
            alerts.append(DriftAlert(
                timestamp=datetime.utcnow().isoformat(),
                metric_name=metric_name,
                baseline_value=0.0,
                current_value=value,
                threshold=critical_threshold,
                severity="critical",
                message=(
                    f"【重大】{metric_name}={value:.4f} が閾値{critical_threshold}を超えました。"
                    "モデルの再評価と根本原因分析が必要です。"
                ),
            ))
        elif value >= warning_threshold:
            alerts.append(DriftAlert(
                timestamp=datetime.utcnow().isoformat(),
                metric_name=metric_name,
                baseline_value=0.0,
                current_value=value,
                threshold=warning_threshold,
                severity="warning",
                message=(
                    f"【警告】{metric_name}={value:.4f} が警告閾値{warning_threshold}を超えました。"
                    "継続監視を強化してください。"
                ),
            ))
        return alerts

    @staticmethod
    def _default_alert_handler(alert: DriftAlert) -> None:
        """デフォルトのアラートハンドラー（標準出力）。本番では CloudWatch / Slack 等に連携。"""
        severity_icon = "🚨" if alert.severity == "critical" else "⚠️"
        print(f"{severity_icon} [{alert.severity.upper()}] {alert.message}")

    def reset_window(self) -> None:
        """現在のサンプリングウィンドウをリセット（定期的に呼び出す）。"""
        self._current_window = []

    def get_alert_history(self) -> list[DriftAlert]:
        return self._alerts.copy()


# =========================================================
# プロンプトドリフト検知
# =========================================================

class PromptDriftDetector:
    """
    プロンプトドリフトを検知するクラス。

    【プロンプトドリフトとは】
    プロンプトを変更していないにもかかわらず、LLMの出力挙動が変化する現象。
    原因: GPT-4のサイレント更新（スタンフォード大研究で素数判定精度が84%→51%に低下）など。

    対策: モデルバージョンを明示的に固定する（例: gpt-4o-2024-08-06）
    """

    def __init__(self, reference_outputs: list[str], quality_scorer: Optional[Callable] = None):
        """
        Args:
            reference_outputs: 品質の基準となる過去の出力サンプル
            quality_scorer: 出力品質を0〜1で評価する関数（省略時はダミーを使用）
        """
        self.reference_outputs = reference_outputs
        self.quality_scorer = quality_scorer or self._dummy_scorer
        self._quality_history: list[dict] = []

    def evaluate_output(self, prompt: str, output: str, timestamp: Optional[str] = None) -> dict:
        """
        新しいLLM出力の品質を評価し、履歴に記録する。

        【注意】LLM-as-Judgeは本番クリティカルパスで同期実行してはいけません。
        バックグラウンドで非同期に実行し、日次5%サンプリングで評価します。
        """
        score = self.quality_scorer(prompt, output, self.reference_outputs)
        record = {
            "timestamp": timestamp or datetime.utcnow().isoformat(),
            "quality_score": score,
            "output_length": len(output),
        }
        self._quality_history.append(record)
        return record

    def detect_quality_drift(self, window_size: int = 50) -> dict:
        """
        品質スコアの時系列から品質ドリフトを検知する。

        直近 window_size 件と全履歴の平均を比較します。
        """
        if len(self._quality_history) < window_size:
            return {"status": "insufficient_data", "window_size": window_size}

        all_scores = [r["quality_score"] for r in self._quality_history]
        recent_scores = all_scores[-window_size:]

        overall_avg = sum(all_scores) / len(all_scores)
        recent_avg = sum(recent_scores) / len(recent_scores)
        degradation = overall_avg - recent_avg  # 正の値 = 品質低下

        result = {
            "overall_avg": round(overall_avg, 4),
            "recent_avg": round(recent_avg, 4),
            "degradation": round(degradation, 4),
            "window_size": window_size,
            "total_samples": len(self._quality_history),
        }

        if degradation > 0.15:
            result["status"] = "critical_drift"
            result["recommendation"] = "モデルバージョンの確認と再評価を実施してください"
        elif degradation > 0.05:
            result["status"] = "warning_drift"
            result["recommendation"] = "継続監視を強化し、根本原因を調査してください"
        else:
            result["status"] = "stable"
            result["recommendation"] = "品質は安定しています"

        return result

    @staticmethod
    def _dummy_scorer(prompt: str, output: str, references: list[str]) -> float:
        """PoC用ダミースコアラー。本番では LLM-as-Judge や RAGAS に置き換えてください。"""
        return random.uniform(0.6, 1.0)


# =========================================================
# デモ実行
# =========================================================

def demo():
    print("=" * 60)
    print("ドリフト検知システム デモ")
    print("=" * 60)

    config = DriftMonitorConfig(
        wasserstein_warning=0.05,
        wasserstein_critical=0.1,
        psi_warning=0.08,
        psi_critical=0.15,
        sample_rate=1.0,  # デモのため全件サンプリング
    )

    monitor = DriftMonitor(config)

    # ベースライン: 正規分布に近い埋め込みベクトル（安定期間）
    dim = 8
    baseline_embeddings = [
        [random.gauss(0.0, 1.0) for _ in range(dim)]
        for _ in range(100)
    ]
    monitor.set_baseline(baseline_embeddings)

    print("\n[シナリオ1] 安定期間（ドリフトなし）")
    for _ in range(50):
        emb = [random.gauss(0.0, 1.0) for _ in range(dim)]
        monitor.add_sample(emb)
    alerts = monitor.check_drift(min_samples=30)
    print(f"  アラート数: {len(alerts)}")
    monitor.reset_window()

    print("\n[シナリオ2] ドリフト発生（分布が平均0→2にシフト）")
    for _ in range(50):
        emb = [random.gauss(2.0, 1.5) for _ in range(dim)]  # 分布がシフト
        monitor.add_sample(emb)
    alerts = monitor.check_drift(min_samples=30)
    print(f"  アラート数: {len(alerts)}")

    print("\n" + "-" * 40)
    print("品質ドリフト検知デモ")
    print("-" * 40)

    prompt_detector = PromptDriftDetector(
        reference_outputs=["良い回答例1", "良い回答例2"],
    )

    # 最初は高品質（スコア0.85〜1.0）
    for i in range(60):
        ts = f"2024-01-{(i // 30) + 1:02d}T12:00:00"
        score = random.uniform(0.85, 1.0) if i < 50 else random.uniform(0.5, 0.7)
        prompt_detector._quality_history.append({
            "timestamp": ts,
            "quality_score": score,
            "output_length": random.randint(100, 500),
        })

    result = prompt_detector.detect_quality_drift(window_size=10)
    print(f"\n[品質ドリフト分析結果]")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    demo()
