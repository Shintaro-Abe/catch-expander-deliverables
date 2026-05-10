# PoC品質: このコードは学習・検証用のスケルトン実装です。本番利用前に認証・エラー処理・テストを追加してください。

"""
keda_scaledobject_manager.py
=============================
KEDA ScaledObject を Python から作成・更新・削除するための管理スクリプト。

【スクリプトの目的】
Kubernetesの kubectl コマンドの代わりに Python コードから ScaledObject を操作する。
CI/CDパイプラインや自動化スクリプトから動的にスケーリング設定を変更したい場合に有用。

【主な機能】
1. ScaledObject の作成（apply）
2. ScaledObject の削除
3. ScaledObject が生成した HPA の確認
4. ゼロスケール状態の確認とウォームアップ（0→1 強制スケールアップ）

【KEDA の二段階スケーリング（重要な設計）】
  ┌─────────────────────────────────────────────────────────┐
  │  フェーズ1: Activation Phase（0→1）                       │
  │    担当: KEDA Operator が直接 Deployment を操作            │
  │    条件: イベントソースが「アクティブ」と判定されたとき        │
  │                                                           │
  │  フェーズ2: Scaling Phase（1→N）                          │
  │    担当: KEDA が自動生成した HPA が制御                     │
  │    条件: HPA がKEDA Metrics Adapterからメトリクスを取得      │
  └─────────────────────────────────────────────────────────┘

【必要なパッケージ】
  pip install kubernetes pyyaml
"""

import sys
import time
import yaml
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

# -----------------------------------------------------------------------
# 定数
# -----------------------------------------------------------------------

KEDA_GROUP = "keda.sh"
KEDA_VERSION = "v1alpha1"
KEDA_SCALED_OBJECT_PLURAL = "scaledobjects"


# -----------------------------------------------------------------------
# ScaledObject テンプレート生成
# -----------------------------------------------------------------------

def build_sqs_scaled_object(
    name: str,
    namespace: str,
    deployment_name: str,
    queue_url: str,
    aws_region: str,
    queue_length: int = 5,
    min_replica_count: int = 0,
    max_replica_count: int = 10,
    polling_interval: int = 30,
    cooldown_period: int = 300,
    trigger_auth_name: Optional[str] = None,
) -> dict:
    """
    AWS SQS をトリガーとする ScaledObject の辞書を構築する。

    【パラメータ解説】
    - queue_length: 1Pod あたりの目標メッセージ数。
      例: メッセージ100件、queueLength=5 → 20Pod が目標値
    - min_replica_count: 0 にするとゼロスケールが有効になる。
      アイドル時（SQSキューが空）は Pod が 0 台になり、コスト削減できる。
    - cooldown_period: 最後のメッセージ処理後、0台にスケールダウンするまでの待機秒数。
      デフォルト300秒(5分)。短すぎるとスラッシング（急激な増減）が発生する。
    - polling_interval: SQSのキュー深度を確認する間隔(秒)。デフォルト30秒。
      短くするとコスト・APIコールが増えるため注意。

    Args:
        trigger_auth_name: TriggerAuthenticationリソース名。
                           IRSA/EKS Pod Identity を使う場合は不要（Noneのまま）。
    """
    triggers = [{
        "type": "aws-sqs-queue",
        "metadata": {
            "queueURL": queue_url,
            "queueLength": str(queue_length),
            "activationQueueLength": "0",  # キューが空でなければ即アクティベート
            "awsRegion": aws_region,
            "scaleOnInFlight": "true",  # 処理中メッセージもカウントに含む
        },
    }]

    # TriggerAuthenticationが指定された場合のみ認証参照を追加
    if trigger_auth_name:
        triggers[0]["authenticationRef"] = {"name": trigger_auth_name}

    return {
        "apiVersion": f"{KEDA_GROUP}/{KEDA_VERSION}",
        "kind": "ScaledObject",
        "metadata": {
            "name": name,
            "namespace": namespace,
        },
        "spec": {
            "scaleTargetRef": {"name": deployment_name},
            "minReplicaCount": min_replica_count,
            "maxReplicaCount": max_replica_count,
            "pollingInterval": polling_interval,
            "cooldownPeriod": cooldown_period,
            "triggers": triggers,
        },
    }


def build_kafka_scaled_object(
    name: str,
    namespace: str,
    deployment_name: str,
    bootstrap_servers: str,
    consumer_group: str,
    topic: str,
    lag_threshold: int = 10,
    min_replica_count: int = 0,
    max_replica_count: int = 20,
    polling_interval: int = 30,
    cooldown_period: int = 300,
) -> dict:
    """
    Apache Kafka をトリガーとする ScaledObject の辞書を構築する。

    【パラメータ解説】
    - lag_threshold: コンシューマーラグ（処理待ちメッセージ数）の閾値。
      ラグがこの値を超えると Pod をスケールアップする。
      例: lagThreshold=50, 現在ラグ=200 → ceil(200/50) = 4 Pod が目標
    - consumer_group: 監視対象のKafkaコンシューマーグループ名。
      スケール時にグループリバランスが発生するため、協調スティッキー
      リバランス（Cooperative Sticky）の採用を推奨。
    """
    return {
        "apiVersion": f"{KEDA_GROUP}/{KEDA_VERSION}",
        "kind": "ScaledObject",
        "metadata": {
            "name": name,
            "namespace": namespace,
        },
        "spec": {
            "scaleTargetRef": {"name": deployment_name},
            "minReplicaCount": min_replica_count,
            "maxReplicaCount": max_replica_count,
            "pollingInterval": polling_interval,
            "cooldownPeriod": cooldown_period,
            # フォールバック: メトリクス取得が3回連続失敗したら2台に戻す
            "fallback": {
                "failureThreshold": 3,
                "replicas": 2,
            },
            "triggers": [{
                "type": "kafka",
                "metadata": {
                    "bootstrapServers": bootstrap_servers,
                    "consumerGroup": consumer_group,
                    "topic": topic,
                    "lagThreshold": str(lag_threshold),
                    "offsetResetPolicy": "latest",
                },
            }],
        },
    }


def build_cron_scaled_object(
    name: str,
    namespace: str,
    deployment_name: str,
    timezone: str = "Asia/Tokyo",
    business_start_cron: str = "0 9 * * 1-5",
    business_end_cron: str = "0 18 * * 1-5",
    business_replicas: int = 10,
    min_replica_count: int = 1,
    max_replica_count: int = 20,
) -> dict:
    """
    Cron スケジュール をトリガーとする ScaledObject を構築する。

    【ユースケース】
    業務時間帯（平日9-18時）のみスケールアップし、夜間・週末はコストを削減する。
    Cronトリガーは他のトリガー（Kafka等）と組み合わせ可能。
    複数トリガーが同時にアクティブな場合、最大値が採用される。

    【パラメータ解説】
    - business_start_cron: スケールアップ開始時刻 (Cron形式)
    - business_end_cron: スケールダウン開始時刻 (Cron形式)
    - business_replicas: 業務時間中の目標Pod数
    """
    return {
        "apiVersion": f"{KEDA_GROUP}/{KEDA_VERSION}",
        "kind": "ScaledObject",
        "metadata": {
            "name": name,
            "namespace": namespace,
        },
        "spec": {
            "scaleTargetRef": {"name": deployment_name},
            "minReplicaCount": min_replica_count,
            "maxReplicaCount": max_replica_count,
            "triggers": [{
                "type": "cron",
                "metadata": {
                    "timezone": timezone,
                    "start": business_start_cron,
                    "end": business_end_cron,
                    "desiredReplicas": str(business_replicas),
                },
            }],
        },
    }


# -----------------------------------------------------------------------
# ScaledObject CRUD 操作
# -----------------------------------------------------------------------

class ScaledObjectManager:
    """KEDA ScaledObject の作成・更新・削除を管理するクラス。"""

    def __init__(self) -> None:
        self._custom_api = client.CustomObjectsApi()
        self._autoscaling_api = client.AutoscalingV2Api()
        self._apps_api = client.AppsV1Api()

    def apply(self, scaled_object: dict) -> dict:
        """
        ScaledObject を作成または更新する（kubectl apply 相当）。

        【内部動作】
        ScaledObject を作成すると、KEDA Operator がそれを検知し、
        対応する HPA リソースを自動生成する。生成された HPA は
        ScaledObject を ownerReference として持ち、ScaledObject削除時に
        ガベージコレクションで自動削除される。
        """
        name = scaled_object["metadata"]["name"]
        namespace = scaled_object["metadata"]["namespace"]

        try:
            # 既存リソースの確認
            existing = self._custom_api.get_namespaced_custom_object(
                group=KEDA_GROUP,
                version=KEDA_VERSION,
                namespace=namespace,
                plural=KEDA_SCALED_OBJECT_PLURAL,
                name=name,
            )
            # 既存の resourceVersion を引き継いで更新
            scaled_object["metadata"]["resourceVersion"] = (
                existing["metadata"]["resourceVersion"]
            )
            result = self._custom_api.replace_namespaced_custom_object(
                group=KEDA_GROUP,
                version=KEDA_VERSION,
                namespace=namespace,
                plural=KEDA_SCALED_OBJECT_PLURAL,
                name=name,
                body=scaled_object,
            )
            print(f"[INFO] ScaledObject '{name}' を更新しました。")
            return result

        except ApiException as e:
            if e.status == 404:
                # 新規作成
                result = self._custom_api.create_namespaced_custom_object(
                    group=KEDA_GROUP,
                    version=KEDA_VERSION,
                    namespace=namespace,
                    plural=KEDA_SCALED_OBJECT_PLURAL,
                    body=scaled_object,
                )
                print(f"[INFO] ScaledObject '{name}' を作成しました。")
                return result
            raise

    def delete(self, name: str, namespace: str) -> None:
        """
        ScaledObject を削除する。

        【注意】
        ScaledObject を削除すると、KEDA が自動生成した HPA も
        ownerReference の GC（ガベージコレクション）により自動削除される。
        Deployment 自体は削除されない。
        """
        try:
            self._custom_api.delete_namespaced_custom_object(
                group=KEDA_GROUP,
                version=KEDA_VERSION,
                namespace=namespace,
                plural=KEDA_SCALED_OBJECT_PLURAL,
                name=name,
            )
            print(f"[INFO] ScaledObject '{name}' を削除しました。")
        except ApiException as e:
            if e.status == 404:
                print(f"[WARN] ScaledObject '{name}' は存在しません。スキップします。")
            else:
                raise

    def get_managed_hpa(self, scaled_object_name: str, namespace: str) -> Optional[dict]:
        """
        ScaledObject が自動生成した HPA を取得する。

        KEDAが生成するHPAは "keda-hpa-{ScaledObject名}" という命名規則に従う。
        """
        hpa_name = f"keda-hpa-{scaled_object_name}"
        try:
            hpa = self._autoscaling_api.read_namespaced_horizontal_pod_autoscaler(
                name=hpa_name,
                namespace=namespace,
            )
            return {
                "name": hpa.metadata.name,
                "min_replicas": hpa.spec.min_replicas,
                "max_replicas": hpa.spec.max_replicas,
                "current_replicas": hpa.status.current_replicas or 0,
                "desired_replicas": hpa.status.desired_replicas or 0,
            }
        except ApiException as e:
            if e.status == 404:
                print(f"[INFO] HPA '{hpa_name}' はまだ生成されていません。")
                return None
            raise

    def warmup(self, deployment_name: str, namespace: str, timeout: int = 120) -> bool:
        """
        ゼロスケール状態の Deployment を 1 台に強制スケールアップする（ウォームアップ）。

        【コールドスタート問題】
        minReplicaCount=0 のとき、イベントなし → Pod が 0 台の状態から
        最初のリクエストを処理するまで 2〜15秒 以上かかることがある（コールドスタート）。
        事前にウォームアップしておくことで、初回リクエストの遅延を回避できる。

        【注意】
        直接 Deployment の replicas を変更するため、KEDA が再スケールするまでの間のみ有効。
        レイテンシSLAが厳しいサービスでは minReplicaCount=1 を推奨。

        Returns:
            True: ウォームアップ成功, False: タイムアウト
        """
        print(f"[INFO] Deployment '{deployment_name}' をウォームアップ中...")

        # Deployment の replicas を 1 に設定
        body = {"spec": {"replicas": 1}}
        self._apps_api.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=body,
        )

        # Pod が Running になるまで待機
        core_api = client.CoreV1Api()
        deadline = time.time() + timeout

        while time.time() < deadline:
            pods = core_api.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"app={deployment_name}",
            )
            running_pods = [
                p for p in pods.items
                if p.status.phase == "Running"
                and all(c.ready for c in (p.status.container_statuses or []))
            ]
            if running_pods:
                print(f"[INFO] Pod が Running になりました: {len(running_pods)}台")
                return True

            print(f"[INFO] Pod 起動待機中... ({int(deadline - time.time())}秒残り)")
            time.sleep(5)

        print(f"[ERROR] ウォームアップタイムアウト ({timeout}秒)")
        return False


# -----------------------------------------------------------------------
# 使用例
# -----------------------------------------------------------------------

def example_sqs_workflow() -> None:
    """
    AWS SQS をトリガーとした ScaledObject の作成・確認・削除のワークフロー例。

    【前提】
    - Kubernetesクラスターに KEDA がインストール済み
    - "worker" という名前の Deployment が "demo" Namespace に存在する
    - AWS IRSA (IAM Roles for Service Accounts) が設定済み
    - SQS に対する sqs:GetQueueAttributes 権限が KEDA Operator に付与済み

    【注意】
    実際の queueURL と awsRegion は環境に合わせて変更してください。
    """
    config.load_kube_config()  # ローカル実行時
    manager = ScaledObjectManager()

    namespace = "demo"
    so_name = "worker-sqs-scaler"
    deployment_name = "worker"

    # --- Step 1: ScaledObject を作成 ---
    print("\n=== Step 1: SQS ScaledObject を作成 ===")
    so = build_sqs_scaled_object(
        name=so_name,
        namespace=namespace,
        deployment_name=deployment_name,
        queue_url="https://sqs.ap-northeast-1.amazonaws.com/123456789012/my-task-queue",
        aws_region="ap-northeast-1",
        queue_length=5,        # 1 Pod あたり 5 メッセージを処理
        min_replica_count=0,   # アイドル時は 0 台（ゼロスケール有効）
        max_replica_count=20,
        cooldown_period=120,   # 最後のメッセージ処理後 120 秒で 0 台に縮退
    )
    print("作成する ScaledObject のマニフェスト:")
    print(yaml.dump(so, allow_unicode=True, default_flow_style=False))
    manager.apply(so)

    # --- Step 2: KEDA が自動生成した HPA を確認 ---
    print("\n=== Step 2: 自動生成された HPA を確認 ===")
    print("  KEDAが ScaledObject を検知し HPA を生成するまで数秒かかります...")
    time.sleep(5)

    hpa_info = manager.get_managed_hpa(so_name, namespace)
    if hpa_info:
        print(f"  HPA名: {hpa_info['name']}")
        print(f"  minReplicas: {hpa_info['min_replicas']}  (注: KEDAは常に1を設定)")
        print(f"  maxReplicas: {hpa_info['max_replicas']}")
        print(f"  現在のReplicas: {hpa_info['current_replicas']}")
    else:
        print("  ※ HPAが生成されていません。KEDAが正常にインストールされているか確認してください。")

    # --- Step 3: ウォームアップ（任意） ---
    print("\n=== Step 3: ウォームアップ（コールドスタート回避） ===")
    print("  ※ コールドスタートが問題になる場合のみ実行してください。")
    # warmup実行例 (コメントを外して実行):
    # manager.warmup(deployment_name, namespace, timeout=120)

    # --- Step 4: ScaledObject を削除 ---
    print("\n=== Step 4: ScaledObject を削除（クリーンアップ） ===")
    print("  ※ 実際に削除する場合は下のコメントを外してください。")
    # manager.delete(so_name, namespace)

    print("\n=== ワークフロー完了 ===")


if __name__ == "__main__":
    example_sqs_workflow()
