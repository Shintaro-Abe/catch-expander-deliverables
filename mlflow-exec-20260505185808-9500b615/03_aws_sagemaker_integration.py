# PoC品質 - 本番利用前に認証・セキュリティ設定を必ず見直してください
"""
MLflow × AWS SageMaker 統合チュートリアル
==========================================
AWS上でMLflowを活用する3つのパターンを示します。

パターンA: SageMaker Managed MLflow（フルマネージド・推奨）
  - AWSがインフラを管理。2024年6月GA、2025年末にサーバーレス対応。
  - IAMベースのアクセス制御が標準で組み込まれている。

パターンB: SageMakerトレーニングジョブ → セルフホスト型MLflowサーバー
  - ECS Fargate等で自前ホストしたMLflowにジョブ結果を記録するパターン。

パターンC: MLflow Model Registry → SageMaker Endpointへのデプロイ
  - Registry に登録されたモデルをSageMaker推論エンドポイントに展開する。

【前提】
    pip install mlflow boto3 sagemaker
    AWS認証情報がセットされていること（IAMロール or 環境変数）
    ハードコードされたシークレットは含めていません
"""

import os
import boto3
import mlflow
import mlflow.sagemaker
from mlflow import MlflowClient

# ── 共通設定（環境変数から取得・シークレットはコードに書かない）──────────────
AWS_REGION          = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1")
S3_ARTIFACT_BUCKET  = os.environ.get("MLFLOW_S3_BUCKET", "your-mlflow-artifacts-bucket")
MODEL_NAME          = "iris-classifier"


# ════════════════════════════════════════════════════════════════════════════
# パターンA: SageMaker Managed MLflow（推奨）
# ════════════════════════════════════════════════════════════════════════════
def pattern_a_managed_mlflow():
    """
    SageMaker Managed MLflow を使う場合、トラッキングサーバーのURIは
    SageMaker Studio のコンソールまたは API から取得できます。

    【特徴】
      ✓ インフラ管理不要（AWSが自動スケーリング）
      ✓ IAMによるRBAC（全REST APIがIAMアクションで制御可能）
      ✓ CloudTrailによる全操作の監査ログ
      ✓ SageMaker Pipelines / Studio とネイティブ統合
      ✗ カスタマイズの自由度はセルフホストより低い
    """
    print("=== パターンA: SageMaker Managed MLflow ===")

    # SageMaker Studio ドメインが作成済みの場合、MLflow App URIを取得する
    sm_client = boto3.client("sagemaker", region_name=AWS_REGION)

    # --- トラッキングサーバー一覧を取得（存在確認）---
    try:
        servers = sm_client.list_mlflow_tracking_servers()
        for server in servers.get("TrackingServerSummaries", []):
            print(f"  サーバー名: {server['TrackingServerName']}")
            print(f"  サイズ: {server['TrackingServerSize']}")   # Small/Medium/Large
            print(f"  ステータス: {server['TrackingServerStatus']}")
            print(f"  URL: {server['TrackingServerUrl']}")
    except Exception as e:
        print(f"  ※ SageMaker Managed MLflow が未設定: {e}")
        print("  → SageMaker Studio コンソールからトラッキングサーバーを作成してください")
        return

    # --- Managed MLflow サーバーへの接続 ---
    tracking_url = servers["TrackingServerSummaries"][0]["TrackingServerUrl"]

    # SigV4署名でMLflowサーバーへアクセス（IAM認証）
    # boto3のIAMロールが自動的に適用されるため、クレデンシャルをコードに書く必要なし
    os.environ["MLFLOW_TRACKING_AWS_SIGV4"] = "True"
    os.environ["MLFLOW_TRACKING_URI"] = tracking_url

    mlflow.set_tracking_uri(tracking_url)
    mlflow.set_experiment("managed-mlflow-demo")

    from sklearn.datasets import load_iris
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score

    iris = load_iris(as_frame=True)
    X_train, X_test, y_train, y_test = train_test_split(
        iris.data, iris.target, test_size=0.2, random_state=42
    )

    with mlflow.start_run(run_name="managed-mlflow-run"):
        model = LogisticRegression(max_iter=1000)
        model.fit(X_train, y_train)
        acc = accuracy_score(y_test, model.predict(X_test))

        mlflow.log_param("solver", "lbfgs")
        mlflow.log_metric("accuracy", acc)

        # アーティファクトは顧客アカウントのS3に保存される
        mlflow.sklearn.log_model(
            sk_model=model,
            name="model",
            registered_model_name=MODEL_NAME,
        )
        print(f"  記録完了: accuracy={acc:.4f}")


# ════════════════════════════════════════════════════════════════════════════
# パターンB: SageMaker Training Job → セルフホスト型 MLflow
# ════════════════════════════════════════════════════════════════════════════
def pattern_b_training_job_tracking():
    """
    SageMaker トレーニングジョブ内でMLflowを使って実験を記録します。
    ジョブの実行環境（コンテナ）内から外部のMLflowサーバーへ書き込みます。

    【ポイント】
      - トレーニングジョブのIAMロールに、MLflowサーバーへのアクセス権限が必要
      - ECS Fargate上のMLflowサーバーの場合、API GatewayのURLを指定
      - SigV4認証を使う場合は MLFLOW_TRACKING_AWS_SIGV4=True を設定
    """
    print("\n=== パターンB: SageMaker Training Job からの追跡 ===")

    # トレーニングジョブのコンテナに渡す環境変数（ハードコード不可）
    MLFLOW_SERVER_URL = os.environ.get("MLFLOW_TRACKING_URI", "http://your-mlflow-server:5000")

    # train.py として実行される想定のコード（概念説明用）
    training_script_content = '''
#!/usr/bin/env python
# train.py - SageMaker コンテナ内で実行されるスクリプト

import os
import mlflow
from sklearn.ensemble import RandomForestClassifier
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

# 環境変数からMLflowサーバーを取得（コンテナ起動時に注入）
mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT_NAME", "sagemaker-training"))

# SageMaker が /opt/ml/input/config/hyperparameters.json に書くパラメータを読む
import json
with open("/opt/ml/input/config/hyperparameters.json") as f:
    hyperparams = json.load(f)

n_estimators = int(hyperparams.get("n_estimators", 100))
max_depth = int(hyperparams.get("max_depth", 5))

iris = load_iris(as_frame=True)
X_train, X_test, y_train, y_test = train_test_split(
    iris.data, iris.target, test_size=0.2, random_state=42
)

with mlflow.start_run():
    mlflow.log_params({"n_estimators": n_estimators, "max_depth": max_depth})
    mlflow.set_tag("job_name", os.environ.get("TRAINING_JOB_NAME", "local"))

    model = RandomForestClassifier(n_estimators=n_estimators, max_depth=max_depth)
    model.fit(X_train, y_train)

    acc = accuracy_score(y_test, model.predict(X_test))
    mlflow.log_metric("accuracy", acc)

    mlflow.sklearn.log_model(
        sk_model=model,
        name="model",
        registered_model_name="iris-classifier",
    )
    # SageMaker の規約: 成果物を /opt/ml/model/ に保存
    import joblib, os
    os.makedirs("/opt/ml/model", exist_ok=True)
    joblib.dump(model, "/opt/ml/model/model.joblib")
    print(f"Training complete: accuracy={acc:.4f}")
'''

    print("  train.py のサンプル内容を表示しました（実際のジョブでは上記コードを実行）")
    print(f"  MLFLOW_TRACKING_URI: {MLFLOW_SERVER_URL}")

    # SageMaker SDK でトレーニングジョブを起動するコード（概念説明）
    launch_code = f"""
import sagemaker
from sagemaker.sklearn import SKLearn

# IAMロール（トレーニングジョブのコンテナが使う権限）
role = sagemaker.get_execution_role()

estimator = SKLearn(
    entry_point="train.py",
    framework_version="1.2-1",
    instance_type="ml.m5.large",
    role=role,
    environment={{
        "MLFLOW_TRACKING_URI": "{MLFLOW_SERVER_URL}",
        "MLFLOW_EXPERIMENT_NAME": "sagemaker-training",
        "MLFLOW_TRACKING_AWS_SIGV4": "True",  # IAM認証
    }},
    hyperparameters={{
        "n_estimators": 200,
        "max_depth": 7,
    }},
)
estimator.fit()
"""
    print("\n  起動コード例:")
    print(launch_code)


# ════════════════════════════════════════════════════════════════════════════
# パターンC: MLflow Model → SageMaker Endpoint へのデプロイ
# ════════════════════════════════════════════════════════════════════════════
def pattern_c_deploy_to_sagemaker():
    """
    MLflow Model Registry に登録されたモデルを
    SageMaker のリアルタイム推論エンドポイントとしてデプロイします。

    【デプロイの流れ】
    Registry (champion) → mlflow.sagemaker.deploy() → SageMaker Endpoint

    【注意点】
      - IAMロールにSageMaker・ECR・S3の権限が必要
      - 初回デプロイは10〜15分かかる（Dockerイメージのビルドが発生）
      - コストがかかるため、不要なエンドポイントは削除すること
    """
    print("\n=== パターンC: SageMaker Endpoint へのデプロイ ===")

    MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    IAM_ROLE_ARN = os.environ.get("SAGEMAKER_ROLE_ARN", "arn:aws:iam::123456789012:role/SageMakerRole")
    ENDPOINT_NAME = "iris-classifier-endpoint"

    # mlflow.sagemaker を使ったデプロイ（概念コード）
    deploy_code = f"""
# champion エイリアスのモデルを SageMaker にデプロイ
mlflow.sagemaker.deploy(
    app_name="{ENDPOINT_NAME}",
    model_uri="models:/{MODEL_NAME}@champion",  # エイリアスで指定
    execution_role_arn="{IAM_ROLE_ARN}",
    region_name="{AWS_REGION}",
    mode=mlflow.sagemaker.REPLACE_ENDPOINTS,  # ローリングアップデート
    instance_type="ml.m5.large",
    instance_count=1,
    # タグでデプロイ情報を管理
    tags={{
        "model_name": "{MODEL_NAME}",
        "alias": "champion",
    }},
)
"""
    print("  デプロイコード例:")
    print(deploy_code)

    # エンドポイント呼び出しのサンプル
    inference_code = """
import boto3, json
import pandas as pd

runtime = boto3.client("sagemaker-runtime", region_name=AWS_REGION)

# 推論リクエスト（pandas DataFrameをJSONに変換）
sample_input = pd.DataFrame([[5.1, 3.5, 1.4, 0.2]], columns=iris.feature_names)

response = runtime.invoke_endpoint(
    EndpointName="iris-classifier-endpoint",
    ContentType="application/json",
    Body=sample_input.to_json(orient="split"),
)
result = json.loads(response["Body"].read())
print(f"予測クラス: {result}")
"""
    print("  推論コード例:")
    print(inference_code)

    # エンドポイント削除（コスト管理）
    cleanup_code = """
# 不要になったエンドポイントは必ず削除してコストを削減
sm = boto3.client("sagemaker")
sm.delete_endpoint(EndpointName="iris-classifier-endpoint")
"""
    print("  クリーンアップ:")
    print(cleanup_code)


# ── CloudTrail / EventBridge 連携（監査ログ）───────────────────────────────
def show_monitoring_setup():
    """
    SageMaker Managed MLflow は全操作が CloudTrail に記録され、
    EventBridge でイベントをトリガーにアクション（Slack通知等）を起こせます。
    """
    print("\n=== 監視・監査設定（Managed MLflow）===")

    eventbridge_rule = """
# EventBridge ルール例: champion エイリアス更新時に Lambda を起動して Slack 通知
{
  "source": ["aws.sagemaker"],
  "detail-type": ["SageMaker Model Package State Change"],
  "detail": {
    "ModelApprovalStatus": ["Approved"]
  }
}
"""
    print("  EventBridge ルール例（モデル承認時の自動通知）:")
    print(eventbridge_rule)

    print("  CloudTrail で記録される操作例:")
    print("    - sagemaker-mlflow:CreateExperiment")
    print("    - sagemaker-mlflow:CreateRun")
    print("    - sagemaker-mlflow:RegisterModel")
    print("    - sagemaker-mlflow:SetRegisteredModelAlias")


# ── メイン ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("MLflow × AWS SageMaker 統合パターン\n")
    print("注意: このスクリプトはAWS環境（SageMakerドメイン等）が必要です")
    print("各パターンのコードを参照して、実際の環境に合わせて調整してください\n")

    # パターンAはSageMaker環境が必要なため概念説明のみ実行
    # pattern_a_managed_mlflow()  # SageMaker Managed MLflow 環境で有効化
    pattern_b_training_job_tracking()
    pattern_c_deploy_to_sagemaker()
    show_monitoring_setup()
