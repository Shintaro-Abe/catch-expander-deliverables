## IaCコード（Terraform または CloudFormation）

# Docker コンテナ on AWS ECS/Fargate — Terraform IaC サンプル

> **PoC 品質のサンプルコードです。本番環境への適用前に必ずセキュリティレビューとコスト試算を実施してください。**

---

## アーキテクチャ概要

```
Internet
   │
   ▼
[ALB] ── パブリックサブネット（2AZ）
   │
   ▼
[ECS Fargate Service] ── プライベートサブネット（2AZ）
   │                         │
   │                    [NAT Gateway]
   │                         │
[ECR] ◄──────────────── ECR Pull（非rootユーザー実行）
   │
[CloudWatch Logs]
[Secrets Manager]
```

| 構成要素 | 選択 | 理由 |
|---------|------|------|
| 実行基盤 | ECS on Fargate | サーバー管理不要・スケーラビリティ重視 |
| CPU アーキテクチャ | ARM64（Graviton） | x86 比 約20% コスト削減 |
| ネットワーク | awsvpc モード | タスクごとに独立した ENI（セキュリティ向上） |
| オートスケール | CPU 70% / Memory 80% | LLM 推論はメモリ消費が大きいため両方設定 |
| コスト最適化 | Fargate Spot 混在対応 | `enable_fargate_spot=true` で最大 70% 削減 |

---

## ファイル構成

```
.
├── main.tf        # VPC・ECS クラスター/サービス/タスク定義・ALB・IAM・オートスケーリング
├── ecr.tf         # ECR リポジトリ・ライフサイクルポリシー・リポジトリポリシー
├── variables.tf   # 入力変数定義
├── outputs.tf     # 出力値（ALB DNS / ECR URL / push コマンド等）
└── README.md      # このファイル
```

---

## 実装済みのセキュリティ対策

| 対策 | 実装箇所 | 効果 |
|------|---------|------|
| **非 root 実行**（UID 5000） | タスク定義 `user` フィールド | ホストへの影響を最小化 |
| **読み取り専用ファイルシステム** | `readonlyRootFilesystem: true` | ランタイムの改ざん防止 |
| **最小 Capability**（ALL DROP） | `linuxParameters.capabilities` | 不要な Linux 権限を排除 |
| **権限昇格防止** | `allowPrivilegeEscalation: false` | setuid バイナリ悪用防止 |
| **Secrets Manager 経由の機密注入** | `secrets` フィールド（コメント例示） | 平文の環境変数を使わない |
| **ECR イメージタグ IMMUTABLE** | `image_tag_mutability` | タグ上書きによる改ざん防止 |
| **プッシュ時脆弱性スキャン** | `scan_on_push: true` | HIGH/CRITICAL を早期検出 |
| **プライベートサブネット配置** | `assign_public_ip: false` | ECS タスクをインターネットから隔離 |
| **最小権限 IAM ロール** | 実行ロール / タスクロール分離 | 侵害時の影響範囲を限定 |
| **デプロイ失敗時自動ロールバック** | `deployment_circuit_breaker` | 本番影響を最小化 |

---

## 前提条件

- Terraform >= 1.6
- AWS CLI 設定済み（`aws configure` または IAM Identity Center）
- Docker / Docker Buildx（ARM64 クロスビルド用）

---

## 使い方

### 1. 初期化

```bash
terraform init
```

### 2. プラン確認

```bash
terraform plan -var="environment=dev"
```

### 3. デプロイ

```bash
terraform apply -var="environment=dev"
```

### 4. ECR へのイメージプッシュ

```bash
# terraform output でコマンドを確認
terraform output docker_push_commands
```

### 5. 削除

```bash
terraform destroy -var="environment=dev"
```

---

## 主要変数一覧

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| `project_name` | `llmops-demo` | リソース名プレフィックス |
| `environment` | `dev` | 環境名（dev/stg/prod） |
| `task_cpu` | `512` | Fargate タスク CPU ユニット |
| `task_memory_mib` | `1024` | Fargate タスクメモリ MiB |
| `desired_count` | `2` | ECS サービスの希望タスク数 |
| `enable_fargate_spot` | `false` | Spot 混在でコスト削減 |
| `container_image_tag` | `latest` | デプロイする ECR イメージタグ |

---

## CI/CD 連携（GitHub Actions）の流れ

```
1. コードプッシュ
2. Dockerfile ビルド（ARM64）
3. Trivy による脆弱性スキャン（HIGH/CRITICAL でブロック）
4. ECR へのプッシュ（コミットハッシュをタグに使用）
5. ECS タスク定義更新（新イメージ URI を注入）
6. ECS サービスへのデプロイ（circuit_breaker で失敗時自動ロールバック）
```

---

## コスト削減のポイント

- **ARM64（Graviton）**: x86 比 約20% 安価（同一スペック）
- **Fargate Spot**: 最大 70% 削減（`enable_fargate_spot=true`）。中断許容ワークロード向け
- **ECR ライフサイクルポリシー**: 古いイメージを自動削除してストレージコストを抑制

---

## 本番化チェックリスト

- [ ] S3 バックエンドで Terraform ステートを管理（`backend "s3"` ブロックを有効化）
- [ ] ALB リスナーを HTTPS に変更し ACM 証明書を設定
- [ ] ECR 暗号化を KMS に変更してキーポリシーを設定
- [ ] `enable_deletion_protection = true` を ALB に設定
- [ ] Amazon Inspector の Enhanced Scanning を有効化（`aws_inspector2_enabler` のコメントを外す）
- [ ] Secrets Manager にシークレットを登録し `secrets` フィールドを有効化
- [ ] CloudWatch アラームを設定して異常検知・通知を自動化
- [ ] GitHub Actions の AWS 認証を OIDC（一時認証情報）に変更


## プログラムコード（Python またはユーザープロファイルの技術スタック）

# Docker コンテナ実践ガイド（LLMOps編）

> **PoC品質**: このリポジトリは学習・検証目的のスケルトンです。本番利用前にセキュリティレビューと追加実装を行ってください。

---

## 概要

LLMOpsパイプラインを想定した **Dockerコンテナ化のベストプラクティス実装例**です。  
ローカル開発から AWS ECR/ECS Fargate へのデプロイまでをカバーします。

---

## ファイル構成

| ファイル | 種別 | 説明 |
|---|---|---|
| `Dockerfile` | コンテナ定義 | マルチステージビルド・非rootユーザー・最小イメージを実装した本番向けテンプレート |
| `docker-compose.yml` | ローカル開発環境 | LLM推論サービス + MLflow + PostgreSQL の3層構成。ネットワーク分離あり |
| `ecr_push.py` | CI/CDスクリプト | Dockerイメージのビルド → Trivyスキャン → ECRプッシュを自動化 |
| `ecs_deploy.py` | CI/CDスクリプト | ECSサービスをローリングアップデートでデプロイし、完了を待機 |

---

## アーキテクチャ図

```
[ ローカル開発 ]                    [ 本番 AWS ]
  docker compose up                   ECR (イメージレジストリ)
  ┌─────────────┐                      │
  │ llm-service  │◄──────────────────── │
  │ (port 8000) │       docker pull     │
  ├─────────────┤                      │
  │ mlflow       │   ECS Fargate ──────►│
  │ (port 5000) │   (タスク定義)
  ├─────────────┤
  │ postgres     │   ALB → ECS Service → Fargate Tasks
  └─────────────┘
  [frontend NW] + [backend NW(internal)]
```

---

## クイックスタート

### 1. ローカル開発環境の起動

```bash
# .envファイルを作成（シークレットはここで管理、Gitにコミット禁止）
cp .env.example .env
# MLFLOW_DB_PASSWORD=your-secure-password を設定

# コンテナ起動
docker compose up -d

# MLflow UI: http://localhost:5000
# LLM API:   http://localhost:8000
```

### 2. ECRへのイメージビルド&プッシュ

```bash
# 必要ライブラリのインストール
pip install boto3

# ビルド & ECRプッシュ（Trivyスキャンあり）
python ecr_push.py \
    --repo-name llm-inference \
    --region ap-northeast-1 \
    --tag $(git rev-parse --short HEAD) \
    --push-latest
```

### 3. ECSサービスのデプロイ

```bash
python ecs_deploy.py \
    --cluster llm-cluster \
    --service llm-inference-svc \
    --image-uri 123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/llm-inference:abc1234 \
    --region ap-northeast-1
```

---

## セキュリティのポイント

### Dockerfileの非rootユーザー設定

```dockerfile
# UID 5000 で専用ユーザーを作成
RUN groupadd -g 5000 appgroup \
    && useradd -u 5000 -g appgroup appuser
USER appuser
```

> **なぜUID 5000?**  
> システムユーザー（1〜999）や一般ユーザー（1000〜）と重複しない範囲を使うことで、  
> ボリュームマウント時の権限問題を回避しやすくなります。

### マルチステージビルドによるサイズ削減

```
シングルステージ（開発ツール込み）: ~1.2GB
マルチステージ（実行環境のみ）:    ~180MB  ← 約85%削減
Distroless使用時:                  ~50MB   ← さらに72%削減
```

### ネットワーク分離（docker-compose）

| ネットワーク | 外部アクセス | 用途 |
|---|---|---|
| `frontend` | 可能 | LLMサービス（ユーザー向けAPI） |
| `backend` | **不可** (`internal: true`) | DB・MLflow（内部サービス） |

---

## CI/CDパイプライン全体像

```
┌─────────────────────────────────────────────────────────────┐
│ GitHub Actions / CodeBuild                                   │
│                                                             │
│  1. git push → トリガー                                      │
│  2. ecr_push.py → ビルド → Trivyスキャン → ECRプッシュ      │
│  3. HIGH/CRITICAL検出 → パイプライン失敗（ブロック）         │
│  4. ecs_deploy.py → タスク定義更新 → ローリングアップデート │
│  5. サーキットブレーカー → 失敗時に自動ロールバック          │
└─────────────────────────────────────────────────────────────┘
```

---

## メリット・デメリット

### Fargate（サーバーレスコンテナ）

| 観点 | メリット | デメリット |
|---|---|---|
| **運用** | インフラ管理不要 | 細かいカスタマイズに限界あり |
| **コスト** | 使った分だけ課金 | 常時稼働なら EC2 より高い場合も |
| **スケール** | 自動スケール対応 | コールドスタートが数十秒発生 |
| **セキュリティ** | AWS管理の隔離環境 | GPUサポートなし |

### Docker（コンテナ全般）

| 観点 | メリット | デメリット |
|---|---|---|
| **再現性** | 環境差異ゼロ | Dockerfileの学習コスト |
| **速度** | 数秒で起動（VM比） | カーネル共有によるセキュリティリスク |
| **LLMOps** | モデル環境の固定化が容易 | 大容量イメージ（数GB〜）のビルド時間 |

---

## 前提条件・依存ライブラリ

```bash
pip install boto3>=1.34.0
```

| ツール | バージョン | 用途 |
|---|---|---|
| Docker | 24.0+ | コンテナビルド・実行 |
| Docker Compose | v2.x | ローカル開発環境 |
| AWS CLI | v2 | ECR認証・ECS操作 |
| Trivy | 0.50+ | 脆弱性スキャン |
| Python | 3.12 | スクリプト実行環境 |


---

📝 [Notionで詳細を見る]()
