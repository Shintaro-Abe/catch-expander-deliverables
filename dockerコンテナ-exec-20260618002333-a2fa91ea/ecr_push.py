"""
PoC品質: このスクリプトは学習・検証目的のスケルトンです。
本番利用前にエラーハンドリング・ロギング・テストを追加してください。

DockerイメージをビルドしてAmazon ECRへプッシュするスクリプト。

LLMOps CI/CDパイプラインの「ビルド & プッシュ」フェーズを担う。
GitHub Actions や CodeBuild から呼び出すことを想定。

使用例:
    python ecr_push.py \\
        --repo-name llm-inference \\
        --region ap-northeast-1 \\
        --tag $(git rev-parse --short HEAD)

必要なIAM権限:
    - ecr:GetAuthorizationToken
    - ecr:BatchCheckLayerAvailability
    - ecr:InitiateLayerUpload
    - ecr:UploadLayerPart
    - ecr:CompleteLayerUpload
    - ecr:PutImage
    - ecr:CreateRepository (初回作成時のみ)
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass
class ECRConfig:
    """ECR操作に必要な設定値をまとめるデータクラス。"""
    repo_name: str
    region: str
    tag: str
    dockerfile: str = "Dockerfile"
    build_context: str = "."
    # Trivyスキャン: HIGH/CRITICALが検出された場合にプッシュをブロックするか
    block_on_critical: bool = True
    extra_tags: list[str] = field(default_factory=list)


def get_ecr_client(region: str):
    """boto3 ECRクライアントを返す。認証はIAMロール/環境変数から自動取得。"""
    return boto3.client("ecr", region_name=region)


def get_account_id(session: boto3.Session) -> str:
    """現在のAWSアカウントIDを取得する。"""
    sts = session.client("sts")
    return sts.get_caller_identity()["Account"]


def ensure_repository(ecr_client, repo_name: str) -> str:
    """ECRリポジトリが存在しなければ作成し、リポジトリURIを返す。"""
    try:
        resp = ecr_client.describe_repositories(repositoryNames=[repo_name])
        uri = resp["repositories"][0]["repositoryUri"]
        logger.info("既存のECRリポジトリを使用: %s", uri)
        return uri
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
        logger.info("リポジトリが存在しないため作成します: %s", repo_name)
        resp = ecr_client.create_repository(
            repositoryName=repo_name,
            imageScanningConfiguration={"scanOnPush": True},  # プッシュ時自動スキャン
            encryptionConfiguration={"encryptionType": "AES256"},
        )
        uri = resp["repository"]["repositoryUri"]
        logger.info("ECRリポジトリを作成しました: %s", uri)
        return uri


def ecr_login(ecr_client, registry: str) -> None:
    """ECRへのdockerログインを実行する（トークンは12時間有効）。"""
    logger.info("ECRへの認証を実行中...")
    token_resp = ecr_client.get_authorization_token()
    auth_data = token_resp["authorizationData"][0]

    # authorizationToken は "AWS:password" を base64エンコードした文字列
    decoded = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
    username, password = decoded.split(":", 1)

    _run_command([
        "docker", "login",
        "--username", username,
        "--password-stdin",
        registry,
    ], input_text=password)
    logger.info("ECR認証成功")


def build_image(config: ECRConfig, full_image_uri: str) -> None:
    """Dockerイメージをビルドする。"""
    logger.info("Dockerイメージをビルド中: %s", full_image_uri)
    cmd = [
        "docker", "build",
        "-f", config.dockerfile,
        "-t", full_image_uri,
    ]
    # 追加タグ（latest等）を付与
    for extra_tag in config.extra_tags:
        cmd += ["-t", extra_tag]
    cmd.append(config.build_context)

    _run_command(cmd)
    logger.info("ビルド完了: %s", full_image_uri)


def scan_image_with_trivy(image_uri: str, block_on_critical: bool) -> bool:
    """
    Trivyで脆弱性スキャンを実行する。

    Returns:
        True: スキャンをパスした（またはTrivyが未インストールでスキップ）
        False: HIGH/CRITICALが検出されブロックが有効な場合
    """
    # Trivyがインストールされているか確認
    result = subprocess.run(["which", "trivy"], capture_output=True)
    if result.returncode != 0:
        logger.warning("Trivyが見つかりません。スキャンをスキップします。本番環境では必ずスキャンを実施してください。")
        return True

    logger.info("Trivyによる脆弱性スキャンを実行中: %s", image_uri)
    scan_cmd = [
        "trivy", "image",
        "--no-progress",
        "--format", "json",
        "--output", "/tmp/trivy-result.json",
        "--severity", "HIGH,CRITICAL",
        image_uri,
    ]
    result = subprocess.run(scan_cmd, capture_output=True, text=True)

    # スキャン結果を読み込んでサマリーを表示
    try:
        with open("/tmp/trivy-result.json") as f:
            scan_result = json.load(f)
        total_vulns = sum(
            len(r.get("Vulnerabilities") or [])
            for r in scan_result.get("Results", [])
        )
        logger.info("Trivy スキャン完了: HIGH/CRITICAL 件数 = %d", total_vulns)

        if total_vulns > 0 and block_on_critical:
            logger.error(
                "HIGH/CRITICALの脆弱性が %d 件検出されました。プッシュをブロックします。"
                " /tmp/trivy-result.json で詳細を確認してください。",
                total_vulns,
            )
            return False
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Trivyスキャン結果の読み込みに失敗: %s", e)

    return True


def push_image(image_uri: str) -> None:
    """ECRへイメージをプッシュする。"""
    logger.info("ECRへプッシュ中: %s", image_uri)
    _run_command(["docker", "push", image_uri])
    logger.info("プッシュ完了: %s", image_uri)


def _run_command(cmd: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
    """サブプロセスコマンドを実行し、失敗時は例外を投げる。"""
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=False,  # 出力をターミナルに流す
    )
    if result.returncode != 0:
        raise RuntimeError(f"コマンド失敗 (exit={result.returncode}): {' '.join(cmd)}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="DockerイメージをECRへビルド&プッシュ")
    parser.add_argument("--repo-name", required=True, help="ECRリポジトリ名")
    parser.add_argument("--region", default="ap-northeast-1", help="AWSリージョン")
    parser.add_argument("--tag", required=True, help="イメージタグ（例: gitコミットハッシュ）")
    parser.add_argument("--dockerfile", default="Dockerfile", help="Dockerfileのパス")
    parser.add_argument("--context", default=".", help="ビルドコンテキストのパス")
    parser.add_argument("--no-scan", action="store_true", help="Trivyスキャンをスキップ")
    parser.add_argument("--push-latest", action="store_true", help="latestタグも付与してプッシュ")
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    account_id = get_account_id(session)
    ecr_client = get_ecr_client(args.region)
    registry = f"{account_id}.dkr.ecr.{args.region}.amazonaws.com"

    config = ECRConfig(
        repo_name=args.repo_name,
        region=args.region,
        tag=args.tag,
        dockerfile=args.dockerfile,
        build_context=args.context,
        block_on_critical=not args.no_scan,
    )

    # 1. リポジトリの確認・作成
    repo_uri = ensure_repository(ecr_client, config.repo_name)
    full_image_uri = f"{repo_uri}:{config.tag}"

    # 2. ECR認証
    ecr_login(ecr_client, registry)

    # 3. ビルド
    extra_tags = [f"{repo_uri}:latest"] if args.push_latest else []
    config.extra_tags = extra_tags
    build_image(config, full_image_uri)

    # 4. 脆弱性スキャン（Trivy）
    if not args.no_scan:
        scan_passed = scan_image_with_trivy(full_image_uri, config.block_on_critical)
        if not scan_passed:
            logger.error("脆弱性スキャン失敗のためプッシュを中止します。")
            sys.exit(1)

    # 5. ECRへプッシュ
    push_image(full_image_uri)
    for extra in extra_tags:
        push_image(extra)

    logger.info(
        "完了。イメージURI: %s\n"
        "次のステップ: ecs_deploy.py --image-uri %s でECSサービスを更新してください。",
        full_image_uri,
        full_image_uri,
    )


if __name__ == "__main__":
    main()
