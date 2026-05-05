## プログラムコード（Python またはユーザープロファイルの技術スタック）

# MLflow 実践ガイド

> **PoC品質** — 本番環境への適用前に認証・セキュリティ・インフラ設定を必ず見直してください。

## 概要

このリポジトリは、**MLflow**（機械学習実験管理ツール）の主要機能と
AWS環境での実践的な活用パターンをPythonコードで示したものです。

---

## ファイル構成

| ファイル | 内容 | 難易度 |
|---|---|---|
| `01_tracking_basics.py` | 実験追跡の基礎（autolog・手動ログ・ネストRun） | ★☆☆ |
| `02_model_registry.py` | モデルバージョン管理・champion/challenger パターン | ★★☆ |
| `03_aws_sagemaker_integration.py` | SageMakerとの統合（フルマネージド・Training Job・デプロイ） | ★★★ |
| `04_cicd_pipeline_integration.py` | GitHub Actions × MLflow による CI/CD 自動化 | ★★★ |

---

## MLflow とは

**MLflow** は機械学習のライフサイクル全体を管理するオープンソースプラットフォームです。
Databricksが開発し、Apache 2.0ライセンスで公開されています（ベンダーロックインなし）。

### 4つのコアコンポーネント

```
実験する → [Tracking] → モデルを保存 → [Models] → 登録する → [Model Registry] → デプロイ
                                                         ↑
                                               [Projects] でコードを再現可能にする
```

| コンポーネント | 役割 | 主なAPI |
|---|---|---|
| **Tracking** | パラメータ・メトリクス・アーティファクトの記録 | `log_param()`, `log_metric()`, `autolog()` |
| **Models** | フレームワーク非依存なモデル保存形式（Flavor） | `log_model()`, `load_model()` |
| **Model Registry** | バージョン管理・エイリアス・ライフサイクル管理 | `register_model()`, `set_alias()` |
| **Projects** | 実行環境のコード標準パッケージ化 | `MLproject` ファイル, `mlflow run` |

---

## クイックスタート

### 1. インストール

```bash
# 基本インストール
pip install mlflow scikit-learn pandas

# 本番環境向け（PostgreSQL + S3 + 認証）
pip install 'mlflow[auth]' psycopg2 boto3
```

### 2. ローカルサーバーの起動

```bash
# SQLiteをバックエンドとして使うシンプルな起動（開発用）
mlflow server --host 0.0.0.0 --port 5000

# ブラウザで http://localhost:5000 を開く
```

### 3. サンプルコードの実行順序

```bash
# 基礎から順に実行
python 01_tracking_basics.py
python 02_model_registry.py
python 03_aws_sagemaker_integration.py   # AWS環境が必要
python 04_cicd_pipeline_integration.py
```

---

## AWS上でのホスティング方法の比較

| 方式 | 管理負荷 | コスト | スケーラビリティ | おすすめ用途 |
|---|---|---|---|---|
| **EC2（手動）** | 高（OS管理必要） | 低 | 手動 | PoC・小規模 |
| **ECS Fargate** | 中（コンテナ管理） | 中 | 自動 | 中規模・カスタム要件あり |
| **SageMaker Managed MLflow** | なし（完全マネージド） | 中〜高 | 自動 | 標準的なMLOps基盤 |

### SageMaker Managed MLflow のサーバーサイズ

| サイズ | 推奨チーム規模 | 持続スループット | バーストスループット |
|---|---|---|---|
| Small  | 〜25名 | 25 TPS | 50 TPS |
| Medium | 〜50名 | 50 TPS | 100 TPS |
| Large  | 〜100名 | 100 TPS | 200 TPS |

---

## champion / challenger パターン

モデルの本番管理には **エイリアス** を使います。
バージョン番号をハードコードすることなく、エイリアスを付け替えるだけで
ノーダウンタイムの本番切り替えが可能です。

```python
from mlflow import MlflowClient
client = MlflowClient()

# 現行本番（champion）を設定
client.set_registered_model_alias("my-model", "champion", version=3)

# コードでは常にエイリアスで参照（バージョン番号不要）
model = mlflow.pyfunc.load_model("models:/my-model@champion")
```

```
v1 (archived)   ← previous-champion
v2 (approved)   ← champion（現行本番）
v3 (pending)    ← challenger（テスト中）
```

---

## MLflow のメリット・デメリット

### メリット ✓

| メリット | 詳細 |
|---|---|
| **フレームワーク非依存** | scikit-learn / PyTorch / TensorFlow / XGBoost など15以上に対応 |
| **ベンダーロックインなし** | Apache 2.0。AWS移行・他クラウド移行も自由 |
| **autolog の手軽さ** | 1行追加するだけで全パラメータ・メトリクスを自動記録 |
| **AWS深い統合** | SageMaker Managed MLflow（2024年GA）でインフラ管理が不要 |
| **LLMOps対応（v3.x）** | LangGraph・AutoGen・LlamaIndex のトレーシング、プロンプト管理に対応 |
| **CI/CD親和性** | GitHub Actions との統合が容易。モデルのバージョン番号を動的取得可能 |

### デメリット・注意点 ✗

| デメリット | 詳細 |
|---|---|
| **認証が弱い（OSS版）** | 4段階の権限しかなく、チーム間のアクセス分離が難しい |
| **大規模での命名規則崩壊** | 500以上の実験が積み上がると整理が難しくなる。命名規則の事前策定が必須 |
| **コラボレーション機能の欠如** | コメント・承認ワークフロー・レビュー機能なし。Slack等で補完が必要 |
| **自己ホストの隠れたコスト** | DB設定・バックアップ・HA・セキュリティパッチが必要。SageMaker Managed推奨 |
| **UIの可視化機能が限定的** | W&Bのような高品質なインタラクティブダッシュボードはない |
| **セキュリティ脆弱性** | CVE-2024-27132（CVSS 7.2）が報告済み。常に最新版へのアップデートが必要 |
| **スケーリング難度** | 5人規模では問題ないが50人以上では設計の見直しが必要になるケースが多い |

---

## 競合ツールとの比較（2025年時点）

| ツール | 実験管理 | モデル管理 | パイプライン | LLMOps | OSS | AWS統合 |
|---|---|---|---|---|---|---|
| **MLflow** | ◎ | ◎ | △ | ◎(v3) | ✓ | ◎（Managed） |
| **W&B** | ◎ | ○ | △ | ◎(Weave) | ✗（SaaS） | ○ |
| **DVC** | △ | △ | ○ | ✗ | ✓ | ○（S3） |
| **Kubeflow** | △ | ○ | ◎ | △ | ✓ | ○（EKS） |
| ~~Neptune.ai~~ | ~~◎~~ | ~~○~~ | ~~✗~~ | ~~△~~ | ~~✗~~ | — |

> ⚠️ **Neptune.ai は2026年3月5日にサービス終了済み**（OpenAIに買収）。
> 移行先としてMLflow・W&Bが公式に案内されていました。

---

## 本番チェックリスト

```
[ ] バックエンドDB: PostgreSQL または MySQL（SQLiteは開発専用）
[ ] mlflow db upgrade コマンドでマイグレーション済み
[ ] アーティファクトストア: S3（またはGCS/ADLS）に設定済み
[ ] 認証: basic-auth または AWS Cognito/SigV4 を有効化
[ ] TLS: Nginx等でHTTPS終端を設定（HTTPのまま公開しない）
[ ] シークレット: ハードコードせず環境変数/Secrets Managerを使用
[ ] エイリアス: Staging/Productionステージの代わりにchampion/challengerエイリアスを使用
[ ] CI/CD: モデルバージョンをハードコードせずエイリアスで動的取得
[ ] ロールバック手順: previous-championエイリアスによる即時復旧を確認済み
```

---

## 参考リンク

- [MLflow公式ドキュメント](https://mlflow.org/docs/latest/)
- [AWS SageMaker Managed MLflow](https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow.html)
- [MLflow GitHub](https://github.com/mlflow/mlflow)


## IaCコード（Terraform または CloudFormation）

# MLflow on AWS — Terraform IaC（PoC品質）

> **⚠️ PoC品質について**
> このコードは概念実証（Proof of Concept）を目的としたスケルトン実装です。
> 本番環境での利用前に、セキュリティ・可用性・コスト設計を必ず見直してください。

---

## アーキテクチャ概要

```
インターネット
     │
     ▼
[ALB: パブリックサブネット]
     │ HTTP :80
     ▼
[ECS Fargate: プライベートサブネット]
  MLflow サーバー (コンテナ)
     │              │
     ▼              ▼
[RDS PostgreSQL]  [S3 バケット]
 バックエンドストア   アーティファクトストア
 (実験メタデータ)    (モデルファイル等)
```

| レイヤー | サービス | 役割 |
|---------|---------|------|
| 公開層 | Application Load Balancer | 外部トラフィックの受け口 |
| 実行層 | ECS Fargate | MLflow サーバー（コンテナ） |
| データ層 | RDS PostgreSQL | 実験・Run のメタデータ保存 |
| ストレージ層 | S3 | モデルファイル・アーティファクト保存 |
| 認証情報管理 | Secrets Manager | DB パスワードの安全な管理 |
| 監視 | CloudWatch Logs | コンテナログの収集 |

---

## ファイル構成

```
.
├── main.tf        # VPC・ネットワーク・S3・RDS・IAM の定義
├── ecs.tf         # ALB・ECS クラスター・タスク定義・サービスの定義
├── variables.tf   # 入力変数の定義
├── outputs.tf     # 出力値（接続先 URL 等）
└── README.md      # 本ドキュメント
```

---

## 前提条件

| 項目 | バージョン / 条件 |
|------|-----------------|
| Terraform | >= 1.5 |
| AWS CLI | 設定済み（`aws configure` 実行済み） |
| AWS アカウント | 作成済み |
| 権限 | `AdministratorAccess` または同等の権限 |

---

## デプロイ手順

### 1. 初期化

```bash
terraform init
```

### 2. 実行計画の確認

```bash
terraform plan
```

### 3. デプロイ実行

```bash
terraform apply
```

デプロイには約 **10〜15 分**かかります（RDS の起動が最も時間がかかります）。

### 4. 接続確認

```bash
# MLflow UI の URL を取得
terraform output mlflow_ui_url

# ブラウザで開く（例）
open $(terraform output -raw mlflow_ui_url)
```

### 5. クライアント設定

```bash
# 環境変数を設定
export MLFLOW_TRACKING_URI=$(terraform output -raw mlflow_tracking_uri)

# Python から接続テスト
python - <<'EOF'
import mlflow
print("MLflow version:", mlflow.__version__)
mlflow.set_experiment("test-experiment")
with mlflow.start_run():
    mlflow.log_param("test_param", "hello_mlflow")
    mlflow.log_metric("test_metric", 1.0)
print("接続成功！MLflow UI を確認してください。")
EOF
```

---

## 変数のカスタマイズ

`terraform.tfvars` ファイルを作成して変数をオーバーライドできます:

```hcl
# terraform.tfvars (Git にコミットしないこと)
project_name        = "my-mlflow"
environment         = "staging"
aws_region          = "ap-northeast-1"
mlflow_image        = "ghcr.io/mlflow/mlflow:v2.16.0"
db_instance_class   = "db.t3.small"
desired_count       = 2

# 重要: 本番では社内/VPN の CIDR に絞ること
allowed_cidr_blocks = ["203.0.113.0/24"]
```

---

## MLflow 4大コンポーネントと本構成の対応

| コンポーネント | 役割 | 本構成での実装 |
|--------------|------|--------------|
| **Tracking** | 実験・パラメータ・メトリクスの記録 | ECS 上の MLflow サーバー + RDS |
| **Projects** | 再現可能なコードパッケージ化 | クライアント側（Terraform 対象外） |
| **Models** | フレームワーク非依存のモデル保存形式 | S3 アーティファクトストア |
| **Model Registry** | バージョン管理・エイリアス管理 | ECS 上の MLflow サーバー + RDS |

---

## メリット・デメリット

### ✅ この構成のメリット

| 観点 | 内容 |
|------|------|
| **コスト効率** | Fargate はタスク稼働時間のみ課金。アイドル時のコストを抑制できる |
| **管理負荷が低い** | OS・ミドルウェアのパッチ適用が不要。コンテナ管理のみで運用できる |
| **スケーラビリティ** | Auto Scaling（`ecs.tf` のコメントを参照）を有効化することで自動スケールアウト可能 |
| **セキュリティ基盤** | RDS はプライベートサブネット内に配置。Secrets Manager でパスワードを安全に管理 |
| **再現性** | Terraform により環境をコードで管理し、完全に再現可能 |
| **OSS** | ベンダーロックインなし。SageMaker Managed MLflow への移行も容易 |

### ❌ この構成のデメリット・注意点

| 観点 | 内容 |
|------|------|
| **認証機能が限定的** | MLflow OSS の認証は基本的な HTTP Basic Auth のみ（4権限レベル）。チームが拡大すると権限管理が困難になる |
| **UIの貧弱さ** | W&B などと比べ、高度なビジュアライゼーションやコメント機能がない |
| **運用コスト** | 自己ホスト構成のためサーバー管理・バックアップ・DR 計画が必要。SageMaker Managed MLflow の利用も検討すること |
| **HTTP のみ** | 本構成は HTTP（PoC用）。本番では ACM + HTTPS リスナーへの変更が必須 |
| **NAT Gateway 費用** | プライベートサブネットからのインターネットアクセスに NAT Gateway 費用が発生する（約 $32/月〜） |
| **チームコラボレーション機能の欠如** | 承認ワークフロー・レビュー機能がない。大規模チームでは命名規則の崩壊が起きやすい |

---

## 本番環境への移行チェックリスト

- [ ] `allowed_cidr_blocks` を社内/VPN の CIDR に限定
- [ ] ALB リスナーを HTTPS に変更（ACM 証明書を発行）
- [ ] `deletion_protection = true`（RDS）
- [ ] `skip_final_snapshot = false`（RDS）
- [ ] Terraform 状態ファイルを S3 + DynamoDB（backend "s3"）に移行
- [ ] `--app-name basic-auth` で MLflow 認証を有効化
- [ ] Multi-AZ RDS または Aurora Serverless に移行
- [ ] Auto Scaling の有効化（`ecs.tf` のコメントアウト部分を解除）
- [ ] WAF（AWS WAF）を ALB に追加
- [ ] CloudWatch アラーム・ダッシュボードの設定

---

## SageMaker Managed MLflow との比較

| 観点 | 本構成（ECS Fargate） | SageMaker Managed MLflow |
|------|----------------------|--------------------------|
| インフラ管理 | 自己管理（Terraform） | AWS がフルマネージド |
| カスタマイズ性 | 高 | 低 |
| IAM 統合 | 手動設定 | ネイティブ統合済み |
| コスト | インフラ費用（NAT GW・RDS・Fargate） | SageMaker 利用料（MLflow 追加料金なし） |
| 推奨用途 | カスタム要件がある中〜大規模 | 標準的な MLOps 基盤 |

---

## クリーンアップ

```bash
# すべてのリソースを削除（注意: S3 バケット内のデータも削除されます）
terraform destroy
```

---

## 参考リンク

- [MLflow 公式ドキュメント](https://mlflow.org/docs/latest/index.html)
- [AWS SageMaker Managed MLflow](https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow.html)
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)


---

📝 [Notionで詳細を見る]()
