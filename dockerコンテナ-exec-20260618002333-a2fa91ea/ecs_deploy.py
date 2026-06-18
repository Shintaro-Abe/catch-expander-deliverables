"""
PoC品質: このスクリプトは学習・検証目的のスケルトンです。
本番利用前にエラーハンドリング・ロギング・テストを追加してください。

Amazon ECS サービスを新しいECRイメージで更新するデプロイスクリプト。

LLMOps CI/CDパイプラインの「デプロイ」フェーズを担う。
ecr_push.py でビルド&プッシュ後に呼び出すことを想定。

使用例:
    python ecs_deploy.py \\
        --cluster llm-cluster \\
        --service llm-inference-svc \\
        --image-uri 123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/llm-inference:abc1234 \\
        --region ap-northeast-1

デプロイ戦略:
    1. 現在のタスク定義を取得
    2. イメージURIを差し替えた新リビジョンを登録
    3. ECSサービスを新リビジョンで更新（ローリングアップデート）
    4. デプロイ完了まで待機（サーキットブレーカーにより失敗時は自動ロールバック）

必要なIAM権限:
    - ecs:DescribeTaskDefinition
    - ecs:RegisterTaskDefinition
    - ecs:UpdateService
    - ecs:DescribeServices
    - iam:PassRole (タスク実行ロール/タスクロール)
"""

from __future__ import annotations

import argparse
import copy
import logging
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# デプロイ完了待機のポーリング間隔（秒）
POLL_INTERVAL_SEC = 15
# デプロイタイムアウト（秒）: Fargateのコールドスタートを考慮して余裕を持たせる
DEPLOY_TIMEOUT_SEC = 600


def get_ecs_client(region: str):
    return boto3.client("ecs", region_name=region)


def get_current_task_definition(ecs_client, cluster: str, service: str) -> dict[str, Any]:
    """ECSサービスが現在使用しているタスク定義を取得する。"""
    resp = ecs_client.describe_services(cluster=cluster, services=[service])
    services = resp.get("services", [])
    if not services:
        raise ValueError(f"ECSサービスが見つかりません: cluster={cluster}, service={service}")

    task_def_arn = services[0]["taskDefinition"]
    logger.info("現在のタスク定義: %s", task_def_arn)

    resp = ecs_client.describe_task_definition(taskDefinition=task_def_arn)
    return resp["taskDefinition"]


def register_new_task_definition(
    ecs_client,
    task_def: dict[str, Any],
    new_image_uri: str,
    container_name: str | None = None,
) -> str:
    """
    タスク定義のイメージURIを差し替えた新リビジョンを登録する。

    container_name が None の場合、最初のコンテナ定義のイメージを差し替える。

    Returns:
        新しいタスク定義ARN
    """
    # boto3の describe_task_definition が返すキーのうち、
    # register_task_definition に渡せないメタデータキーを除去
    register_keys = {
        "family", "taskRoleArn", "executionRoleArn", "networkMode",
        "containerDefinitions", "volumes", "placementConstraints",
        "requiresCompatibilities", "cpu", "memory", "tags",
        "pidMode", "ipcMode", "proxyConfiguration",
        "inferenceAccelerators", "ephemeralStorage", "runtimePlatform",
    }
    new_task_def = {k: v for k, v in task_def.items() if k in register_keys}

    # コンテナ定義のイメージを差し替え
    container_defs = copy.deepcopy(new_task_def["containerDefinitions"])
    updated = False
    for container in container_defs:
        if container_name is None or container["name"] == container_name:
            old_image = container["image"]
            container["image"] = new_image_uri
            logger.info("イメージを更新: %s → %s", old_image, new_image_uri)
            updated = True
            if container_name is not None:
                break

    if not updated:
        raise ValueError(f"コンテナ '{container_name}' がタスク定義内に見つかりません。")

    new_task_def["containerDefinitions"] = container_defs

    resp = ecs_client.register_task_definition(**new_task_def)
    new_arn = resp["taskDefinition"]["taskDefinitionArn"]
    logger.info("新しいタスク定義を登録: %s", new_arn)
    return new_arn


def update_service(
    ecs_client,
    cluster: str,
    service: str,
    task_def_arn: str,
) -> None:
    """ECSサービスを新しいタスク定義で更新（ローリングアップデート開始）。"""
    logger.info(
        "ECSサービスを更新: cluster=%s, service=%s, task_def=%s",
        cluster, service, task_def_arn,
    )
    ecs_client.update_service(
        cluster=cluster,
        service=service,
        taskDefinition=task_def_arn,
        # デプロイ設定: ゼロダウンタイムのためminHealthyPercent=100
        deploymentConfiguration={
            "minimumHealthyPercent": 100,
            "maximumPercent": 200,
            # サーキットブレーカー有効化: 失敗時に自動ロールバック
            "deploymentCircuitBreaker": {
                "enable": True,
                "rollback": True,
            },
        },
        forceNewDeployment=True,  # タスク定義が同一でも強制的に新デプロイを開始
    )
    logger.info("ECSサービス更新を開始しました。デプロイ完了を待機中...")


def wait_for_deployment(
    ecs_client,
    cluster: str,
    service: str,
    timeout_sec: int = DEPLOY_TIMEOUT_SEC,
) -> bool:
    """
    ECSデプロイが完了するまでポーリングして待機する。

    Returns:
        True: デプロイ成功
        False: タイムアウトまたはデプロイ失敗
    """
    elapsed = 0
    while elapsed < timeout_sec:
        resp = ecs_client.describe_services(cluster=cluster, services=[service])
        svc = resp["services"][0]
        deployments = svc.get("deployments", [])

        # デプロイ状況を表示
        for dep in deployments:
            status = dep["status"]
            running = dep.get("runningCount", 0)
            desired = dep.get("desiredCount", 0)
            pending = dep.get("pendingCount", 0)
            failed_tasks = dep.get("failedTasks", 0)

            logger.info(
                "[%s] status=%s, running=%d/%d, pending=%d, failed=%d",
                dep["taskDefinition"].split("/")[-1],
                status, running, desired, pending, failed_tasks,
            )

            # サーキットブレーカーがロールバックを開始した場合
            if dep.get("rolloutState") == "FAILED":
                logger.error("デプロイ失敗。ECSがロールバックを実行しています。")
                return False

        # PRIMARY デプロイが1つだけでrunning==desiredなら完了
        primary_deps = [d for d in deployments if d["status"] == "PRIMARY"]
        if (
            len(deployments) == 1
            and primary_deps
            and primary_deps[0]["runningCount"] == primary_deps[0]["desiredCount"]
            and primary_deps[0]["pendingCount"] == 0
        ):
            logger.info("デプロイ完了！ running=%d", primary_deps[0]["runningCount"])
            return True

        time.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC

    logger.error("デプロイがタイムアウト (%d秒) しました。", timeout_sec)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="ECSサービスを新しいイメージでデプロイ")
    parser.add_argument("--cluster", required=True, help="ECSクラスター名またはARN")
    parser.add_argument("--service", required=True, help="ECSサービス名またはARN")
    parser.add_argument("--image-uri", required=True, help="新しいECRイメージURI（タグ含む）")
    parser.add_argument("--container-name", default=None, help="更新するコンテナ名（省略時は最初のコンテナ）")
    parser.add_argument("--region", default="ap-northeast-1", help="AWSリージョン")
    parser.add_argument("--no-wait", action="store_true", help="デプロイ完了を待機しない")
    parser.add_argument("--timeout", type=int, default=DEPLOY_TIMEOUT_SEC, help="待機タイムアウト秒数")
    args = parser.parse_args()

    ecs_client = get_ecs_client(args.region)

    # 1. 現在のタスク定義を取得
    current_task_def = get_current_task_definition(ecs_client, args.cluster, args.service)

    # 2. イメージを差し替えた新リビジョンを登録
    new_task_def_arn = register_new_task_definition(
        ecs_client,
        current_task_def,
        args.image_uri,
        args.container_name,
    )

    # 3. サービスを更新してローリングアップデート開始
    update_service(ecs_client, args.cluster, args.service, new_task_def_arn)

    if args.no_wait:
        logger.info("--no-wait が指定されたため、デプロイ完了の待機をスキップします。")
        return

    # 4. デプロイ完了を待機
    success = wait_for_deployment(
        ecs_client, args.cluster, args.service, args.timeout
    )

    if not success:
        logger.error(
            "デプロイに失敗しました。AWSコンソールまたは以下のコマンドで詳細を確認してください:\n"
            "  aws ecs describe-services --cluster %s --services %s --region %s",
            args.cluster, args.service, args.region,
        )
        raise SystemExit(1)

    logger.info(
        "デプロイ成功！\n"
        "  クラスター : %s\n"
        "  サービス   : %s\n"
        "  イメージ   : %s",
        args.cluster, args.service, args.image_uri,
    )


if __name__ == "__main__":
    main()
