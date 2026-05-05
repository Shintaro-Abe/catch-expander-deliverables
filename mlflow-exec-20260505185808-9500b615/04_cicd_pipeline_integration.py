# PoC品質 - 本番利用前に認証・セキュリティ設定を必ず見直してください
"""
MLflow × CI/CD パイプライン統合チュートリアル
=============================================
MLflow Model Registry と GitHub Actions を組み合わせた
「コードが変更されるたびに自動でモデルが評価・デプロイされる」
パイプラインの実装パターンを示します。

【自動化フロー】
  1. データサイエンティストが実験 → Registry の champion を更新
  2. GitHub へのマージが CI/CD トリガー
  3. CI: テスト・品質チェック → CD: Registry から champion を取得 → Docker化 → ECS デプロイ
  4. CT（継続的トレーニング）: スケジュールでデータ更新を検知 → 自動再学習

【前提】
    pip install mlflow boto3 scikit-learn
    環境変数: MLFLOW_TRACKING_URI, AWS_DEFAULT_REGION, etc.
"""

import os
import json
import time
import hashlib
import subprocess
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

import mlflow
from mlflow import MlflowClient
from sklearn.datasets import load_iris
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score

# ── 設定（環境変数から取得）────────────────────────────────────────────────
TRACKING_URI   = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_NAME     = os.environ.get("MLFLOW_MODEL_NAME", "iris-classifier")
EXPERIMENT     = os.environ.get("MLFLOW_EXPERIMENT", "cicd-demo")
ACCURACY_GATE  = float(os.environ.get("ACCURACY_GATE", "0.90"))   # デプロイ最低精度

mlflow.set_tracking_uri(TRACKING_URI)
mlflow.set_experiment(EXPERIMENT)
client = MlflowClient(tracking_uri=TRACKING_URI)


@dataclass
class EvaluationResult:
    """モデル評価結果を型安全に保持するデータクラス"""
    run_id: str
    version: str
    accuracy: float
    f1_macro: float
    passed_gate: bool
    git_commit: str


# ════════════════════════════════════════════════════════════════════════════
# ステップ1: 自動学習パイプライン（CT: Continuous Training）
# ════════════════════════════════════════════════════════════════════════════
def continuous_training_pipeline() -> str:
    """
    データの変更を検知して自動再学習するパイプラインの核心部分。
    GitHub Actions の cron スケジュールで定期実行される想定。

    【GitHub Actions yaml 例（.github/workflows/ct.yml）】
    on:
      schedule:
        - cron: '0 2 * * *'   # 毎日2時に実行
      workflow_dispatch:       # 手動起動も可能

    Returns: 登録された Model Version 番号（文字列）
    """
    print("=== ステップ1: 自動学習（Continuous Training）===")

    # Gitコミットハッシュを取得してモデルの「血統」を記録する
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        git_commit = "local-" + hashlib.md5(str(time.time()).encode()).hexdigest()[:8]

    print(f"  Git commit: {git_commit}")

    # データ取得（本番では S3 や Feature Store から取得）
    iris = load_iris(as_frame=True)
    X_train, X_test, y_train, y_test = train_test_split(
        iris.data, iris.target, test_size=0.2, random_state=42, stratify=iris.target
    )

    # ハイパーパラメータ（本番では設定ファイルやMLflow Projects で管理）
    params = {
        "n_estimators": 150,
        "max_depth": 5,
        "learning_rate": 0.1,
        "random_state": 42,
    }

    mlflow.sklearn.autolog(disable=True)

    with mlflow.start_run(run_name=f"ct-run-{git_commit}") as run:
        # パイプライン情報をタグで記録（追跡可能性を確保）
        mlflow.set_tags({
            "git.commit":      git_commit,
            "trigger":         "scheduled_ct",
            "pipeline_stage":  "training",
            "dataset.version": "v2024-12",
            "timestamp":       datetime.utcnow().isoformat(),
        })
        mlflow.log_params(params)

        model = GradientBoostingClassifier(**params)
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        metrics = {
            "accuracy": accuracy_score(y_test, y_pred),
            "f1_macro": f1_score(y_test, y_pred, average="macro"),
        }
        mlflow.log_metrics(metrics)

        # Registry に候補バージョンとして登録（まだ champion ではない）
        mlflow.sklearn.log_model(
            sk_model=model,
            name="model",
            input_example=X_test.iloc[:3],
            registered_model_name=MODEL_NAME,
        )

        # 候補を示すタグを付与
        run_id = run.info.run_id
        print(f"  学習完了: accuracy={metrics['accuracy']:.4f}, run_id={run_id[:8]}...")

    # 最新登録バージョンを返す
    versions = client.search_model_versions(
        f"name='{MODEL_NAME}'", order_by=["version_number DESC"], max_results=1
    )
    latest_version = versions[0].version if versions else "1"
    print(f"  登録バージョン: v{latest_version}")
    return latest_version


# ════════════════════════════════════════════════════════════════════════════
# ステップ2: 品質ゲート（CI: Continuous Integration）
# ════════════════════════════════════════════════════════════════════════════
def quality_gate_check(version: str) -> EvaluationResult:
    """
    新しいモデルバージョンが最低品質基準を満たすか自動チェックします。
    基準を下回った場合は CI が失敗し、不完全なモデルは本番に出ない仕組みです。

    【チェック項目】
      1. 精度がしきい値（ACCURACY_GATE）を超えているか
      2. 現行 champion より性能が劣化していないか
      3. 推論レイテンシが許容範囲内か（本番では追加）
    """
    print(f"\n=== ステップ2: 品質ゲート（v{version}）===")

    iris = load_iris(as_frame=True)
    _, X_test, _, y_test = train_test_split(
        iris.data, iris.target, test_size=0.2, random_state=42, stratify=iris.target
    )

    # 評価対象のバージョンをロード
    model_uri = f"models:/{MODEL_NAME}/{version}"
    candidate = mlflow.pyfunc.load_model(model_uri)
    y_pred = candidate.predict(X_test)

    accuracy = accuracy_score(y_test, y_pred)
    f1 = f1_score(y_test, y_pred, average="macro")

    # ゲート判定
    passed = accuracy >= ACCURACY_GATE
    print(f"  精度: {accuracy:.4f} (ゲート: {ACCURACY_GATE}) → {'✓ 通過' if passed else '✗ 失敗'}")
    print(f"  F1マクロ: {f1:.4f}")

    # champion との比較（存在する場合）
    try:
        champion = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@champion")
        champion_acc = accuracy_score(y_test, champion.predict(X_test))
        regression = champion_acc - accuracy
        print(f"  現行 champion との差分: {regression:+.4f}")
        if regression > 0.02:  # 2%以上の精度劣化は失敗
            passed = False
            print("  ✗ champion より2%以上精度が低下しているため失敗")
    except Exception:
        print("  champion が未設定のためスキップ")

    # 評価結果をタグとして Registry に記録
    tag_value = "passed" if passed else "failed"
    client.set_model_version_tag(MODEL_NAME, str(version), "quality_gate", tag_value)
    client.set_model_version_tag(MODEL_NAME, str(version), "accuracy", f"{accuracy:.4f}")

    git_commit = os.environ.get("GITHUB_SHA", "local")[:8]

    return EvaluationResult(
        run_id=client.get_model_version(MODEL_NAME, str(version)).run_id or "",
        version=str(version),
        accuracy=accuracy,
        f1_macro=f1,
        passed_gate=passed,
        git_commit=git_commit,
    )


# ════════════════════════════════════════════════════════════════════════════
# ステップ3: 自動プロモーション（CD: Continuous Deployment）
# ════════════════════════════════════════════════════════════════════════════
def promote_to_production(result: EvaluationResult) -> bool:
    """
    品質ゲートを通過したモデルを champion に昇格させます。
    実際の ECS Fargate デプロイは GitHub Actions の後続ステップで行います。

    【デプロイ戦略】
    - Blue/Green デプロイ: champion 切り替えで即時全量切り替え
    - Canary デプロイ:     champion + canary の2エイリアスを使いトラフィック分割

    Returns: プロモーション成功かどうか
    """
    print(f"\n=== ステップ3: 本番プロモーション（v{result.version}）===")

    if not result.passed_gate:
        print("  品質ゲートを通過していないためプロモーション中止")
        return False

    # 旧 champion のバックアップ（ロールバック用に previous エイリアスを設定）
    try:
        old_champion = client.get_model_version_by_alias(MODEL_NAME, "champion")
        client.set_registered_model_alias(
            MODEL_NAME, "previous-champion", old_champion.version
        )
        print(f"  旧 champion v{old_champion.version} → 'previous-champion' に退避")
    except Exception:
        print("  旧 champion なし（初回デプロイ）")

    # 新バージョンを champion に設定
    client.set_registered_model_alias(MODEL_NAME, "champion", result.version)
    client.set_model_version_tag(MODEL_NAME, result.version, "deployment_git_commit", result.git_commit)
    client.set_model_version_tag(MODEL_NAME, result.version, "deployed_at", datetime.utcnow().isoformat())

    print(f"  ✓ v{result.version} を champion に設定")
    print(f"  git_commit: {result.git_commit}")

    # CI/CD システムへの通知（環境変数経由で GitHub Actions に伝達）
    deployment_info = {
        "model_name": MODEL_NAME,
        "version": result.version,
        "alias": "champion",
        "accuracy": result.accuracy,
        "git_commit": result.git_commit,
        "deployed_at": datetime.utcnow().isoformat(),
    }
    print(f"\n  デプロイ情報（GitHub Actions の output として設定）:")
    print(f"  {json.dumps(deployment_info, indent=4, ensure_ascii=False)}")

    # GitHub Actions では以下の形式で output を設定する
    # echo "model_version=$VERSION" >> $GITHUB_OUTPUT
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"model_version={result.version}\n")
            f.write(f"model_accuracy={result.accuracy:.4f}\n")

    return True


# ════════════════════════════════════════════════════════════════════════════
# ステップ4: ロールバック（障害時の自動復旧）
# ════════════════════════════════════════════════════════════════════════════
def rollback_champion():
    """
    本番で障害が発生した場合に previous-champion に即時ロールバックします。
    エイリアスの付け替えのみで完了するため、数秒でロールバック可能です。
    """
    print("\n=== ロールバック実行 ===")

    try:
        prev = client.get_model_version_by_alias(MODEL_NAME, "previous-champion")
        current = client.get_model_version_by_alias(MODEL_NAME, "champion")

        client.set_registered_model_alias(MODEL_NAME, "champion", prev.version)
        print(f"  ✓ champion を v{current.version} → v{prev.version} にロールバック")

        # 障害バージョンにタグを付けて追跡
        client.set_model_version_tag(MODEL_NAME, current.version, "incident_rollback", "true")
    except Exception as e:
        print(f"  ✗ ロールバック失敗（previous-champion が未設定？）: {e}")


# ════════════════════════════════════════════════════════════════════════════
# GitHub Actions ワークフロー定義の出力
# ════════════════════════════════════════════════════════════════════════════
def print_github_actions_workflow():
    """
    このスクリプトを呼び出す GitHub Actions ワークフロー定義（yaml）を
    参考として出力します。
    """
    workflow_yaml = """
# .github/workflows/mlops-pipeline.yml
name: MLOps Pipeline

on:
  push:
    branches: [main]
    paths:
      - 'src/**'
      - 'configs/**'
  schedule:
    - cron: '0 2 * * *'   # CT: 毎日2時に自動再学習

jobs:
  train-and-evaluate:
    runs-on: ubuntu-latest
    permissions:
      id-token: write     # AWS OIDC 認証（シークレット不要）
      contents: read

    outputs:
      model_version: ${{ steps.pipeline.outputs.model_version }}
      passed_gate:   ${{ steps.pipeline.outputs.passed_gate }}

    steps:
      - uses: actions/checkout@v4

      - name: AWS認証（OIDC・クレデンシャルをコードに書かない）
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.AWS_ROLE_ARN }}
          aws-region: ap-northeast-1

      - name: Python環境のセットアップ
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip

      - name: 依存関係のインストール
        run: pip install mlflow scikit-learn boto3

      - name: MLOpsパイプライン実行
        id: pipeline
        env:
          MLFLOW_TRACKING_URI: ${{ vars.MLFLOW_TRACKING_URI }}
          MLFLOW_MODEL_NAME: iris-classifier
          ACCURACY_GATE: "0.90"
          GITHUB_SHA: ${{ github.sha }}
        run: python 04_cicd_pipeline_integration.py

  deploy-to-ecs:
    needs: train-and-evaluate
    if: needs.train-and-evaluate.outputs.passed_gate == 'true'
    runs-on: ubuntu-latest

    steps:
      - name: ECRへDockerイメージをプッシュ
        env:
          MODEL_VERSION: ${{ needs.train-and-evaluate.outputs.model_version }}
        run: |
          # champion エイリアスのモデルを含むイメージをビルド
          docker build --build-arg MODEL_VERSION=$MODEL_VERSION -t iris-api .
          docker tag iris-api $ECR_URI:${{ github.sha }}
          docker push $ECR_URI:${{ github.sha }}

      - name: ECS Fargateへのローリングアップデート
        run: |
          # タスク定義を新イメージURIで更新し、ゼロダウンタイムでデプロイ
          aws ecs update-service \\
            --cluster production \\
            --service iris-api \\
            --force-new-deployment
"""
    print("\n=== GitHub Actions ワークフロー定義 ===")
    print(workflow_yaml)


# ── メイン実行（パイプライン全体の実行）────────────────────────────────────
if __name__ == "__main__":
    print("MLflow × CI/CD パイプライン統合デモ")
    print(f"トラッキングURI: {TRACKING_URI}")
    print(f"モデル名: {MODEL_NAME}")
    print(f"品質ゲート: accuracy >= {ACCURACY_GATE}\n")

    # CI/CD パイプライン全体を実行
    version = continuous_training_pipeline()
    result = quality_gate_check(version)
    promoted = promote_to_production(result)

    if promoted:
        print(f"\n✓ パイプライン成功: v{version} が本番に昇格しました")
    else:
        print(f"\n✗ パイプライン停止: v{version} は品質ゲートを通過しませんでした")
        # 必要に応じてロールバック
        # rollback_champion()

    print_github_actions_workflow()

    print(f"\nMLflow UI: {TRACKING_URI}")
    print(f"モデルレジストリ: {TRACKING_URI}/#/models/{MODEL_NAME}")
