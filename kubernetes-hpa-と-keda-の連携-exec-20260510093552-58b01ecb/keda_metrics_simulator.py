# PoC品質: このコードは学習・検証用のスケルトン実装です。本番利用前に認証・エラー処理・テストを追加してください。

"""
keda_metrics_simulator.py
==========================
KEDA の External Scaler (gRPC Pull型) のスタブ実装と、
HPA + KEDA の連携動作をシミュレートするスクリプト。

【このファイルの目的】
実際のKubernetesクラスターなしに、以下の動作を理解・確認できるシミュレーターを提供する:
1. KEDA の 2 フェーズスケーリング計算ロジック
2. HPA のレプリカ数計算式（desiredReplicas の算出）
3. activationThreshold と threshold の優先度ルール
4. cooldownPeriod を考慮したゼロスケール判定

【KEDA External Scaler (gRPC) について】
ビルトインスケーラー（Kafka・SQS等）が存在しない独自システムには、
gRPC サービスとして External Scaler を実装する。
KEDA は pollingInterval ごとに GetMetricsAndActivity を呼び出す（Pull型）。

【必要なパッケージ】
  pip install grpcio grpcio-tools  # External Scaler 実装時のみ
  (シミュレーターのみなら追加パッケージ不要)
"""

import math
import time
import random
from dataclasses import dataclass, field
from typing import Optional


# -----------------------------------------------------------------------
# データクラス定義
# -----------------------------------------------------------------------

@dataclass
class ScalerConfig:
    """
    ScaledObject の設定を表すデータクラス。

    【各フィールドの意味】
    - threshold: HPA が 1 → N のスケールアウトを行う際の1Pod あたりのメトリクス閾値。
                 例: SQS queueLength=5 → 1 Pod あたり 5 メッセージが目標
    - activation_threshold: 0 → 1 のアクティベーションを行うメトリクス閾値。
                            この値を下回ると Pod は 0 台のまま維持される。
                            【罠】activation_threshold > threshold の場合、
                            threshold を超えても activation_threshold 未満なら 0 台が維持される。
    - min_replica_count: 最小レプリカ数。0 でゼロスケール有効。
    - cooldown_period_sec: 最後にアクティブだった後、0 台にスケールダウンするまでの秒数。
    - polling_interval_sec: メトリクス確認間隔（秒）。
    """
    name: str
    threshold: float              # 1 Pod あたりの目標メトリクス値
    activation_threshold: float   # 0→1 アクティベーション閾値
    min_replica_count: int = 0
    max_replica_count: int = 10
    cooldown_period_sec: int = 300
    polling_interval_sec: int = 30


@dataclass
class ScalerState:
    """スケーラーのランタイム状態を追跡するデータクラス。"""
    current_replicas: int = 0
    last_active_at: Optional[float] = None  # 最後にアクティブだったUnix時刻
    scale_events: list[dict] = field(default_factory=list)


# -----------------------------------------------------------------------
# KEDA スケーリングロジック シミュレーター
# -----------------------------------------------------------------------

class KedaScalingSimulator:
    """
    KEDA + HPA の 2 フェーズスケーリングロジックをシミュレートするクラス。

    【2フェーズスケーリングの概要】
    ┌─────────────────────────────────────────────────────────────────┐
    │ Phase 1: Activation Phase（KEDA Operator 担当）                  │
    │   - メトリクス値 > activation_threshold → 0 から 1 にスケールアップ │
    │   - メトリクス値 <= 0 かつ cooldown_period 経過 → 0 にスケールダウン │
    │                                                                   │
    │ Phase 2: Scaling Phase（HPA 担当）                               │
    │   - desired = ceil(current × metric / threshold)                 │
    │   - min_replicas(=1) ≤ desired ≤ max_replicas                    │
    └─────────────────────────────────────────────────────────────────┘
    """

    def __init__(self, config: ScalerConfig) -> None:
        self.config = config
        self.state = ScalerState()

    def is_active(self, metric_value: float) -> bool:
        """
        イベントソースがアクティブかどうかを判定する（KEDA Operator の IsActive 相当）。

        【activationThreshold の罠】
        activation_threshold が threshold より大きい場合、
        metric_value が threshold を超えていても activation_threshold 未満なら
        Pod は 0 台のまま（ゼロスケール状態）が維持される。

        例: threshold=10, activation_threshold=50, metric_value=40 の場合
          → HPA は「40/10=4 Pod 必要」と計算するが
          → KEDA は「40 < 50 なので非アクティブ」と判定 → 0 台維持
        """
        return metric_value > self.config.activation_threshold

    def compute_desired_replicas_hpa(self, metric_value: float) -> int:
        """
        HPA のレプリカ数計算式を実装する。

        【公式】
        desiredReplicas = ceil(currentReplicas × currentMetric / desiredMetric)

        【ゼロ除算の問題】
        currentReplicas=0 のとき、HPA はこの計算ができない。
        これが HPA 単独でゼロスケールを実現できない根本原因。
        → KEDA は 0↔1 のトランジションを Operator が直接制御することで解決する。
        """
        if self.state.current_replicas == 0:
            # 0 Pod のとき HPA は計算不能 → KEDA が 1 に直接スケールする
            return 1 if self.is_active(metric_value) else 0

        desired = math.ceil(
            self.state.current_replicas * metric_value / self.config.threshold
        )
        # HPA の min/max クランプ（minReplicas は KEDA が 1 に設定）
        hpa_min_replicas = 1
        return max(hpa_min_replicas, min(desired, self.config.max_replica_count))

    def tick(self, metric_value: float, current_time: Optional[float] = None) -> dict:
        """
        1 ポーリングサイクルのスケーリング判定を実行する。

        Args:
            metric_value: 現在のメトリクス値（例: SQS メッセージ数、Kafka ラグ）
            current_time: テスト用の現在時刻 (None の場合は time.time() を使用)

        Returns:
            スケーリング判定結果の辞書
        """
        now = current_time or time.time()
        prev_replicas = self.state.current_replicas
        active = self.is_active(metric_value)

        if active:
            self.state.last_active_at = now

        # --- Phase 1: Activation Phase (0 ↔ 1) ---
        if self.state.current_replicas == 0:
            if active:
                # KEDA Operator が Deployment を 0 → 1 に直接スケールアップ
                self.state.current_replicas = 1
                action = "activate (0→1)"
            else:
                action = "idle (0 維持)"

        # --- Phase 2 + Cooldown Check ---
        else:
            if not active and self.state.last_active_at is not None:
                elapsed_since_last_active = now - self.state.last_active_at
                if elapsed_since_last_active >= self.config.cooldown_period_sec:
                    # cooldownPeriod 経過後にゼロスケール
                    self.state.current_replicas = self.config.min_replica_count
                    action = f"cooldown完了 → {self.config.min_replica_count}台 (ゼロスケール)"
                else:
                    remaining = self.config.cooldown_period_sec - elapsed_since_last_active
                    # まだクールダウン中 → HPA が通常スケーリングを継続
                    desired = self.compute_desired_replicas_hpa(metric_value)
                    self.state.current_replicas = desired
                    action = f"cooldown中 (残{remaining:.0f}s) → {desired}台"
            else:
                # 通常スケーリング: HPA の計算式に従う
                desired = self.compute_desired_replicas_hpa(metric_value)
                self.state.current_replicas = desired
                action = f"HPA スケーリング → {desired}台"

        event = {
            "time": now,
            "metric_value": metric_value,
            "is_active": active,
            "prev_replicas": prev_replicas,
            "current_replicas": self.state.current_replicas,
            "action": action,
        }
        self.state.scale_events.append(event)
        return event


# -----------------------------------------------------------------------
# シナリオ実行
# -----------------------------------------------------------------------

def simulate_sqs_workload(
    config: ScalerConfig,
    duration_steps: int = 60,
    seed: int = 42,
) -> None:
    """
    SQS キューのメッセージ数変動をシミュレートし、スケーリング動作を確認する。

    シナリオ:
      Step  0- 9: キュー空（アイドル状態）
      Step 10-29: バーストトラフィック（大量メッセージ到着）
      Step 30-44: 処理中（メッセージ減少）
      Step 45-59: キュー再び空 → cooldownPeriod 待機 → 0 台

    Args:
        config: スケーラー設定
        duration_steps: シミュレーションのステップ数（1 ステップ = pollingInterval 秒）
        seed: 乱数シード（再現性確保のため）
    """
    random.seed(seed)
    simulator = KedaScalingSimulator(config)

    print(f"\n{'=' * 70}")
    print(f"シナリオ: SQS ワークロード シミュレーション")
    print(f"設定: threshold={config.threshold}, activationThreshold={config.activation_threshold}")
    print(f"      minReplicas={config.min_replica_count}, maxReplicas={config.max_replica_count}")
    print(f"      cooldownPeriod={config.cooldown_period_sec}s")
    print(f"{'=' * 70}")
    print(f"{'Step':>5} {'時刻(仮想)':>10} {'メッセージ数':>10} {'Active':>8} {'前台数':>6} {'後台数':>6} {'アクション'}")
    print("-" * 70)

    # 仮想時刻を使ってシミュレート（実際の時間は経過させない）
    sim_start = 0.0

    for step in range(duration_steps):
        sim_time = sim_start + step * config.polling_interval_sec

        # メッセージ数の推移を定義
        if step < 10:
            metric = 0                                  # アイドル
        elif step < 20:
            metric = random.randint(80, 150)            # バースト
        elif step < 35:
            metric = max(0, metric - random.randint(10, 25))  # 処理中（漸減）
        else:
            metric = 0                                  # アイドル（クールダウン待ち）

        event = simulator.tick(metric_value=float(metric), current_time=sim_time)

        # 台数変化があった行を強調
        marker = " ◀ 変化あり" if event["prev_replicas"] != event["current_replicas"] else ""
        print(
            f"{step:>5} {sim_time:>10.0f}s {metric:>10} "
            f"{'Yes' if event['is_active'] else 'No':>8} "
            f"{event['prev_replicas']:>6} {event['current_replicas']:>6} "
            f"{event['action']}{marker}"
        )

    print(f"\n[サマリー] 総スケールイベント数: {len(simulator.state.scale_events)} ステップ")
    changes = [e for e in simulator.state.scale_events if e["prev_replicas"] != e["current_replicas"]]
    print(f"[サマリー] レプリカ数変化: {len(changes)} 回")


def demonstrate_activation_threshold_trap() -> None:
    """
    activationThreshold > threshold の場合の挙動（既知の罠）を実演する。

    【重要な注意点】
    activation_threshold=50, threshold=10 の設定でメトリクス値=40 のとき:
    - HPA の計算: ceil(40/10) = 4 Pod が必要
    - KEDA の判定: 40 < 50 なのでアクティブでない → 0 台を維持
    → HPA がスケールしたくても KEDA が 0 台のまま固定してしまう！
    """
    print(f"\n{'=' * 70}")
    print("activationThreshold > threshold の罠 デモ")
    print(f"{'=' * 70}")

    config = ScalerConfig(
        name="trap-demo",
        threshold=10.0,           # 1 Pod あたり 10 メッセージ
        activation_threshold=50.0, # 50 メッセージ未満は 0 台維持（threshold より大きい！）
        min_replica_count=0,
        max_replica_count=10,
        cooldown_period_sec=60,
    )
    simulator = KedaScalingSimulator(config)

    test_cases = [0, 5, 10, 40, 50, 51, 100]
    print(f"\n{'メトリクス値':>12} {'IsActive':>10} {'HPA計算値':>10} {'実際の台数':>12} {'説明'}")
    print("-" * 60)

    for metric in test_cases:
        simulator.state.current_replicas = 0  # 毎回ゼロスケール状態からテスト
        simulator.state.last_active_at = None
        event = simulator.tick(float(metric), current_time=float(len(simulator.state.scale_events) * 30))

        hpa_desired = math.ceil(metric / config.threshold) if metric > 0 else 0
        explanation = ""
        if metric > config.threshold and not event["is_active"]:
            explanation = "← 罠! HPAは必要だがKEDAが0台維持"
        elif event["is_active"]:
            explanation = "アクティブ → スケールアップ可能"
        else:
            explanation = "非アクティブ → 0台維持"

        print(
            f"{metric:>12} {'Yes' if event['is_active'] else 'No':>10} "
            f"{hpa_desired:>10} {event['current_replicas']:>12} {explanation}"
        )

    print(f"\n【対策】activationThreshold は threshold 以下に設定するか、")
    print(f"        両者の意図を明確に理解した上で設定する。")


# -----------------------------------------------------------------------
# メイン実行
# -----------------------------------------------------------------------

if __name__ == "__main__":
    # --- シナリオ1: 通常の SQS ワークロードシミュレーション ---
    normal_config = ScalerConfig(
        name="sqs-worker",
        threshold=5.0,            # 1 Pod あたり 5 メッセージ
        activation_threshold=1.0, # 1 メッセージ以上でアクティベート
        min_replica_count=0,
        max_replica_count=20,
        cooldown_period_sec=150,  # シミュレーション短縮のため 150 秒に設定
        polling_interval_sec=30,
    )
    simulate_sqs_workload(normal_config, duration_steps=50)

    # --- シナリオ2: activationThreshold の罠デモ ---
    demonstrate_activation_threshold_trap()
