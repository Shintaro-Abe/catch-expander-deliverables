# PoC品質: このコードは学習・検証用のスケルトン実装です。本番利用前に認証・エラー処理・テストを追加してください。

"""
keda_hpa_monitor.py
====================
KEDA ScaledObject と Kubernetes HPA の状態を監視するモニタリングスクリプト。

【前提知識】
- HPA (Horizontal Pod Autoscaler): Kubernetesに組み込まれた自動スケーラー。
  CPU/メモリなどのメトリクスに基づいてPod数を自動調整する。
- KEDA (Kubernetes Event-driven Autoscaling): HPAを拡張するOSSコンポーネント。
  Kafka・SQS・Prometheusなど60種類以上の外部イベントソースに基づいてスケールできる。
  また、HPAでは不可能な「0台へのスケールダウン（ゼロスケール）」を実現する。

【スクリプトの動作】
1. Kubernetesクラスターに接続する
2. 指定NamespaceのHPAを一覧表示する
3. KEDAが管理するScaledObjectを一覧表示する
4. 各ScaledObjectとHPAの紐付き状態を確認する
5. --watchフラグで定期的にポーリングして状態変化を監視する

【必要なパッケージ】
  pip install kubernetes rich
"""

import argparse
import time
import sys
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# -----------------------------------------------------------------------
# 定数定義
# -----------------------------------------------------------------------

# KEDAのカスタムリソース定義情報
KEDA_GROUP = "keda.sh"
KEDA_VERSION = "v1alpha1"
KEDA_SCALED_OBJECT_PLURAL = "scaledobjects"

# デフォルト設定
DEFAULT_NAMESPACE = "default"
DEFAULT_POLL_INTERVAL = 30  # 秒


# -----------------------------------------------------------------------
# クラスター接続
# -----------------------------------------------------------------------

def load_kube_config(in_cluster: bool = False) -> None:
    """
    Kubernetesクラスターへの接続設定を読み込む。

    Args:
        in_cluster: Trueの場合、Pod内から実行（ServiceAccountを使用）。
                    Falseの場合、~/.kube/config を使用（ローカル開発向け）。
    """
    if in_cluster:
        # Pod内部から実行する場合（本番・CI環境）
        config.load_incluster_config()
        print("[INFO] クラスター内設定を読み込みました (in-cluster config)")
    else:
        # ローカルのkubeconfigを使用する場合（開発環境）
        config.load_kube_config()
        print("[INFO] ローカルkubeconfigを読み込みました")


# -----------------------------------------------------------------------
# HPA 取得・表示
# -----------------------------------------------------------------------

def fetch_hpas(namespace: str) -> list[dict]:
    """
    指定NamespaceのHPA（Horizontal Pod Autoscaler）一覧を取得する。

    【HPAの役割】
    KEDAはScaledObjectを作成すると、内部で対応するHPAを自動生成する。
    このHPAの minReplicas は常に 1 に設定され、0↔1 のトランジションは
    KEDAオペレーターが直接Deploymentを操作することで行う。

    Returns:
        HPAの情報リスト（name, namespace, minReplicas, maxReplicas, currentReplicas, etc.）
    """
    autoscaling_v2 = client.AutoscalingV2Api()
    result = []

    try:
        hpa_list = autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(namespace=namespace)
        for hpa in hpa_list.items:
            conditions = []
            if hpa.status.conditions:
                for cond in hpa.status.conditions:
                    if cond.status == "True":
                        conditions.append(cond.type)

            result.append({
                "name": hpa.metadata.name,
                "namespace": hpa.metadata.namespace,
                "min_replicas": hpa.spec.min_replicas,
                "max_replicas": hpa.spec.max_replicas,
                "current_replicas": hpa.status.current_replicas or 0,
                "desired_replicas": hpa.status.desired_replicas or 0,
                "conditions": ", ".join(conditions) if conditions else "—",
                # ownerReferences を確認してKEDAが管理するHPAかどうか判定
                "managed_by_keda": _is_managed_by_keda(hpa),
            })
    except ApiException as e:
        print(f"[ERROR] HPA取得失敗 (namespace={namespace}): {e.reason}")

    return result


def _is_managed_by_keda(hpa) -> bool:
    """ownerReferences を見てKEDAが管理するHPAかどうか判定する。"""
    if not hpa.metadata.owner_references:
        return False
    for ref in hpa.metadata.owner_references:
        if ref.api_version == f"{KEDA_GROUP}/{KEDA_VERSION}" and ref.kind == "ScaledObject":
            return True
    return False


# -----------------------------------------------------------------------
# KEDA ScaledObject 取得・表示
# -----------------------------------------------------------------------

def fetch_scaled_objects(namespace: str) -> list[dict]:
    """
    KEDAのScaledObjectカスタムリソースを取得する。

    【ScaledObjectとは】
    KEDAのCRD（カスタムリソース定義）。アプリケーションと外部イベントソース
    （Kafka・SQS・Prometheusなど）を紐づける設定を記述する。
    ScaledObjectを作成すると、KEDAオペレーターが対応するHPAを自動生成・管理する。

    Returns:
        ScaledObjectの情報リスト
    """
    custom_api = client.CustomObjectsApi()
    result = []

    try:
        so_list = custom_api.list_namespaced_custom_object(
            group=KEDA_GROUP,
            version=KEDA_VERSION,
            namespace=namespace,
            plural=KEDA_SCALED_OBJECT_PLURAL,
        )

        for so in so_list.get("items", []):
            metadata = so.get("metadata", {})
            spec = so.get("spec", {})
            status = so.get("status", {})

            # トリガー（イベントソース）の種別を抽出
            trigger_types = [
                t.get("type", "unknown")
                for t in spec.get("triggers", [])
            ]

            result.append({
                "name": metadata.get("name", "—"),
                "namespace": metadata.get("namespace", "—"),
                "target": spec.get("scaleTargetRef", {}).get("name", "—"),
                "min_replicas": spec.get("minReplicaCount", 0),
                "max_replicas": spec.get("maxReplicaCount", 100),
                "triggers": ", ".join(trigger_types) if trigger_types else "—",
                "polling_interval": spec.get("pollingInterval", 30),
                "cooldown_period": spec.get("cooldownPeriod", 300),
                # status.conditions からアクティブ状態を取得
                "ready": _get_scaled_object_ready_state(status),
                "active": _get_scaled_object_active_state(status),
            })

    except ApiException as e:
        if e.status == 404:
            print("[WARN] KEDAがインストールされていないか、ScaledObjectCRDが存在しません。")
        else:
            print(f"[ERROR] ScaledObject取得失敗 (namespace={namespace}): {e.reason}")

    return result


def _get_scaled_object_ready_state(status: dict) -> str:
    """ScaledObjectのReady状態を取得する。"""
    for cond in status.get("conditions", []):
        if cond.get("type") == "Ready":
            return "Ready" if cond.get("status") == "True" else "NotReady"
    return "Unknown"


def _get_scaled_object_active_state(status: dict) -> str:
    """
    ScaledObjectのActive状態を取得する。

    【Activeとは】
    KEDAが外部イベントソース（Kafkaのラグ、SQSのメッセージ数など）を検知し、
    「スケールアップすべき」と判断している状態。Active=True のとき、
    KEDAはDeploymentのreplicasを0から1に引き上げ（アクティベーションフェーズ）、
    その後はHPAが1→N台のスケーリングを引き継ぐ。
    """
    for cond in status.get("conditions", []):
        if cond.get("type") == "Active":
            return "Active" if cond.get("status") == "True" else "Idle"
    return "Unknown"


# -----------------------------------------------------------------------
# 表示ロジック
# -----------------------------------------------------------------------

def print_hpa_table(hpas: list[dict], console: Optional["Console"] = None) -> None:
    """HPA一覧をテーブル形式で表示する。"""
    if not hpas:
        print("  (HPAなし)")
        return

    if RICH_AVAILABLE and console:
        table = Table(title="HPA 一覧", box=box.ROUNDED, show_lines=True)
        table.add_column("名前", style="cyan")
        table.add_column("最小", justify="right")
        table.add_column("最大", justify="right")
        table.add_column("現在", justify="right")
        table.add_column("目標", justify="right")
        table.add_column("KEDA管理", justify="center")
        table.add_column("状態")

        for h in hpas:
            table.add_row(
                h["name"],
                str(h["min_replicas"]),
                str(h["max_replicas"]),
                str(h["current_replicas"]),
                str(h["desired_replicas"]),
                "[green]YES[/green]" if h["managed_by_keda"] else "no",
                h["conditions"],
            )
        console.print(table)
    else:
        print(f"{'名前':<40} {'最小':>4} {'最大':>4} {'現在':>4} {'目標':>4} {'KEDA管理':>8} {'状態'}")
        print("-" * 80)
        for h in hpas:
            managed = "YES" if h["managed_by_keda"] else "no"
            print(
                f"{h['name']:<40} {h['min_replicas']:>4} {h['max_replicas']:>4} "
                f"{h['current_replicas']:>4} {h['desired_replicas']:>4} {managed:>8} {h['conditions']}"
            )


def print_scaled_object_table(scaled_objects: list[dict], console: Optional["Console"] = None) -> None:
    """ScaledObject一覧をテーブル形式で表示する。"""
    if not scaled_objects:
        print("  (ScaledObjectなし)")
        return

    if RICH_AVAILABLE and console:
        table = Table(title="KEDA ScaledObject 一覧", box=box.ROUNDED, show_lines=True)
        table.add_column("名前", style="cyan")
        table.add_column("スケール対象")
        table.add_column("トリガー種別", style="yellow")
        table.add_column("最小", justify="right")
        table.add_column("最大", justify="right")
        table.add_column("Poll間隔", justify="right")
        table.add_column("Cooldown", justify="right")
        table.add_column("状態")

        for so in scaled_objects:
            active_display = "[green]Active[/green]" if so["active"] == "Active" else "[grey50]Idle[/grey50]"
            ready_display = "[green]Ready[/green]" if so["ready"] == "Ready" else "[red]NotReady[/red]"
            table.add_row(
                so["name"],
                so["target"],
                so["triggers"],
                str(so["min_replicas"]),
                str(so["max_replicas"]),
                f"{so['polling_interval']}s",
                f"{so['cooldown_period']}s",
                f"{ready_display} / {active_display}",
            )
        console.print(table)
    else:
        print(f"{'名前':<35} {'対象':<20} {'トリガー':<20} {'最小':>4} {'最大':>4} {'状態'}")
        print("-" * 100)
        for so in scaled_objects:
            print(
                f"{so['name']:<35} {so['target']:<20} {so['triggers']:<20} "
                f"{so['min_replicas']:>4} {so['max_replicas']:>4} {so['ready']}/{so['active']}"
            )


# -----------------------------------------------------------------------
# メイン処理
# -----------------------------------------------------------------------

def monitor_once(namespace: str, console: Optional["Console"] = None) -> None:
    """1回分の監視サイクルを実行する。"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    separator = "=" * 70

    if RICH_AVAILABLE and console:
        console.rule(f"[bold blue]{timestamp}  Namespace: {namespace}")
    else:
        print(f"\n{separator}")
        print(f"  {timestamp}  Namespace: {namespace}")
        print(separator)

    # --- HPA 一覧 ---
    print("\n【HPA 一覧】")
    print("  ※ KEDA管理=YESのHPAはScaledObjectが自動生成したものです (minReplicas=1固定)")
    hpas = fetch_hpas(namespace)
    print_hpa_table(hpas, console)

    # --- ScaledObject 一覧 ---
    print("\n【KEDA ScaledObject 一覧】")
    print("  ※ minReplicaCount=0 のとき、イベントなし→0台(ゼロスケール)が有効です")
    scaled_objects = fetch_scaled_objects(namespace)
    print_scaled_object_table(scaled_objects, console)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="KEDA ScaledObject と HPA の状態を監視するモニタリングツール (PoC品質)"
    )
    parser.add_argument(
        "-n", "--namespace",
        default=DEFAULT_NAMESPACE,
        help=f"監視対象のNamespace (デフォルト: {DEFAULT_NAMESPACE})",
    )
    parser.add_argument(
        "--in-cluster",
        action="store_true",
        help="Pod内から実行する場合に指定 (in-cluster config使用)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="指定した間隔で繰り返し監視する",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"--watch 時の監視間隔（秒）。デフォルト: {DEFAULT_POLL_INTERVAL}",
    )
    args = parser.parse_args()

    load_kube_config(in_cluster=args.in_cluster)
    console = Console() if RICH_AVAILABLE else None

    if args.watch:
        print(f"[INFO] 監視開始: {args.interval}秒ごとに更新 (Ctrl+C で停止)")
        try:
            while True:
                monitor_once(args.namespace, console)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[INFO] 監視を停止しました。")
            sys.exit(0)
    else:
        monitor_once(args.namespace, console)


if __name__ == "__main__":
    main()
