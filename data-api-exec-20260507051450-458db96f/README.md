## IaCコード（Terraform または CloudFormation）

# AWS Data API — Terraform IaC サンプル（PoC品質）

> **⚠️ PoC品質**: 学習・検証目的のサンプルコードです。本番環境へ適用する前に、セキュリティレビュー・コスト見積もり・パラメータ調整を必ず実施してください。

## 概要

このTerraformコードは、AWSのData APIサービス群を組み合わせたサーバーレスAPIアーキテクチャを構築します。

### 構成するAWSリソース

| リソース | 役割 |
|---------|------|
| **API Gateway REST API** | RESTエンドポイント（WAF・Cognito認証・キャッシュ対応） |
| **AWS AppSync** | GraphQL API（リアルタイム・複数データソース統合） |
| **Aurora Serverless v2** | RDB（RDS Data API経由でHTTPSからSQL実行） |
| **DynamoDB** | NoSQL KVS（ミリ秒レイテンシのCRUD操作） |
| **Lambda** | バックエンドロジック（Aurora Data API・DynamoDB呼び出し） |
| **Cognito User Pool** | ユーザー認証（JWT発行・API認可） |
| **WAF v2** | Webファイアウォール（SQLi・XSS・レート制限） |

### アーキテクチャ図

```
クライアント（ブラウザ/モバイル）
      │
      ├─[REST API]──▶ WAF ──▶ API Gateway REST API
      │                              │ (Cognito JWT認証)
      │                              ▼
      │                         Lambda関数
      │                         ├──▶ DynamoDB（NoSQL）
      │                         └──▶ Aurora Serverless v2
      │                               (RDS Data API / HTTPS)
      │
      └─[GraphQL]──▶ AppSync GraphQL API
                         │ (Cognito / IAM / API Key 認証)
                         ├──▶ DynamoDB（直接統合）
                         └──▶ Lambda（RDS Data API）
```

---

## APIプロトコル別 メリット・デメリット

### REST API（API Gateway）

| 観点 | メリット | デメリット |
|------|---------|-----------|
| **コスト** | HTTP APIなら$1/100万（最安） | REST APIは$3.5/100万（高め） |
| **学習コスト** | HTTP知識だけで実装可能 | 高度な機能（WAF・マッピング）は学習が必要 |
| **互換性** | あらゆるHTTPクライアントで利用可能 | Over-fetching（不要データの取得）が発生しやすい |
| **デバッグ** | curlやブラウザで直接確認できる | — |
| **機能** | WAF・APIキー・キャッシュが使える | Under-fetchingで複数リクエストが必要になることも |

### GraphQL API（AppSync）

| 観点 | メリット | デメリット |
|------|---------|-----------|
| **データ効率** | 必要なフィールドのみ取得（over-fetching解消） | クエリ設計の複雑さ（スキーマ・リゾルバー） |
| **リアルタイム** | WebSocketサブスクリプションが組み込み済み | コスト高（$4/100万、REST HTTP APIの4倍） |
| **開発速度** | フロントエンドがバックエンドに依存しない | GraphQL固有の学習コスト |
| **型安全性** | SDL（スキーマ）による厳密な型定義 | N+1問題（DataLoaderなどの対策が必要） |

### Aurora Serverless v2 + RDS Data API

| 観点 | メリット | デメリット |
|------|---------|-----------|
| **接続管理** | HTTP接続のため接続プール不要 | Writerインスタンスのみ対応（Readerへのルーティング不可） |
| **サーバーレス連携** | LambdaをVPCに置かずにDBアクセス可能 | レスポンスサイズ上限1MiB |
| **コスト（開発環境）** | 最小0.5 ACU（未使用時は自動縮小） | 常時起動するRDSより高コストになる場合あり |
| **スケーリング** | トラフィックに応じて自動スケール | T系インスタンスクラス非対応 |

### DynamoDB

| 観点 | メリット | デメリット |
|------|---------|-----------|
| **パフォーマンス** | ミリ秒単位の一定レイテンシ | 複雑なJOINクエリには不向き |
| **スケール** | ほぼ無制限の水平スケール | アクセスパターンを事前設計する必要あり |
| **コスト（スパイク）** | On-Demandモードでスパイクを自動吸収 | Provisionedより6〜7倍高コスト |
| **コスト（安定）** | Provisionedモードで予約割引（最大77%） | トラフィックを事前予測する必要あり |

---

## 前提条件

- Terraform >= 1.6
- AWS CLI 設定済み（`aws configure`）
- 必要なIAM権限（IAM・RDS・DynamoDB・Lambda・API Gateway・AppSync・Cognito・WAF・CloudWatch）

---

## デプロイ手順

```bash
# 1. 初期化（プロバイダープラグインのダウンロード）
terraform init

# 2. 変更内容の確認（実際には何も変更されません）
terraform plan

# 3. デプロイ実行（約5〜10分かかります）
terraform apply

# 4. 出力値の確認
terraform output

# 5. APIキーの確認（機密値のため -raw オプションが必要）
terraform output -raw api_key_value
terraform output -raw appsync_api_key
```

---

## 主要パラメータ（variables.tf）

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| `project_name` | `data-api-poc` | リソース名プレフィックス |
| `environment` | `dev` | 環境名 |
| `aurora_min_capacity` | `0.5` | Aurora最小ACU（コスト最小化） |
| `aurora_max_capacity` | `16` | Aurora最大ACU（スパイク上限） |
| `dynamodb_billing_mode` | `PAY_PER_REQUEST` | `PAY_PER_REQUEST` または `PROVISIONED` |
| `api_gateway_cache_enabled` | `false` | REST APIキャッシュ（追加コスト発生） |
| `api_throttle_rate_limit` | `1000` | APIスロットリング（RPS） |
| `lambda_provisioned_concurrency` | `0` | コールドスタート対策（0=無効） |
| `waf_enabled` | `true` | WAFの有効/無効 |
| `waf_rate_limit` | `2000` | 5分間のIPごとのリクエスト上限 |

---

## コスト目安（東京リージョン / 月100万リクエスト想定）

| サービス | 月額コスト目安 | 注記 |
|---------|--------------|------|
| API Gateway REST API | ~$3.5 | リクエスト料のみ |
| API Gateway HTTP API | ~$1.0 | シンプルAPI向け最安 |
| AppSync | ~$4.0 | クエリ/ミューテーション |
| Lambda (256MB, 200ms) | ~$8.3 | 100万実行 |
| Aurora Serverless v2 | ~$3.6〜 | 0.5ACU×24h×30日 |
| DynamoDB (On-Demand) | ~$1.25 | 100万読み取り |
| WAF | ~$5.0 + $1/100万 | Web ACL固定費込み |

> **💡 コスト最適化のヒント**: 開発環境ではWAFを無効（`waf_enabled = false`）、Aurora最小ACU=0.5、DynamoDBはOn-Demandモードで始めることを推奨します。

---

## 削除方法

```bash
# 全リソースを削除（取り消し不可。データも失われます）
terraform destroy
```

---

## ファイル構成

```
.
├── main.tf          # コアインフラ（VPC・Aurora・DynamoDB・Lambda・Cognito）
├── api_gateway.tf   # REST API（API Gateway・WAF・使用量プラン）
├── appsync.tf       # GraphQL API（AppSync・スキーマ・リゾルバー）
├── variables.tf     # 全パラメータ定義
├── outputs.tf       # デプロイ後に確認できる重要な値
└── README.md        # このファイル
```


## プログラムコード（Python またはユーザープロファイルの技術スタック）

# AWS Data API — サンプルコード集

> **⚠️ PoC（概念実証）品質**
> このリポジトリのコードは学習・検証目的のスケルトン実装です。
> 本番環境への適用前に、セキュリティレビュー・エラーハンドリング・テストを十分に実施してください。

---

## 概要

AWS の主要な Data API サービス（API Gateway / AppSync / Aurora Data API / DynamoDB API）の実装パターンをまとめたサンプル集です。

---

## ファイル構成

| ファイル | サービス | 説明 |
|---|---|---|
| `lambda_rest_handler.py` | API Gateway + Lambda | REST API ハンドラー。Cognito JWT 認証・スロットリング対応 |
| `dynamodb_data_api.py`   | DynamoDB API        | Classic API・PartiQL・トランザクション・バッチ操作 |
| `aurora_data_api.py`     | Aurora Data API     | RDS Data API 経由の SQL 実行・トランザクション |
| `appsync_graphql_resolver.py` | AppSync + Lambda | GraphQL リゾルバー。マルチ認証モード対応 |

---

## サービス選定ガイド

### どの API スタイルを選ぶべきか？

| ユースケース | 推奨 | 理由 |
|---|---|---|
| シンプルな REST API（低コスト優先） | API Gateway **HTTP API** | $1/100万リクエスト（REST APIの約1/3.5） |
| WAF・APIキー・キャッシュが必要 | API Gateway **REST API** | エンタープライズ向け制御機能が充実 |
| GraphQL / リアルタイム更新 | **AppSync** | サブスクリプション・オフライン同期・複数データソース統合 |
| サーバーレスから RDB へアクセス | **Aurora Data API** | VPC・接続プール管理不要 |
| 高速 KVS CRUD | **DynamoDB** | ミリ秒レイテンシ・フルマネージド |

### コスト比較（リクエスト単価）

```
DynamoDB On-Demand (読み取り):  $0.125 / 100万 RRU
API Gateway HTTP API:           $1.00  / 100万リクエスト
AppSync Query/Mutation:         $4.00  / 100万リクエスト
API Gateway REST API:           $3.50  / 100万リクエスト
Aurora Data API ExecuteStatement: $0.35 / 100万リクエスト
```

---

## 環境変数

各ファイルは以下の環境変数を参照します（`.env` や Lambda 環境変数に設定してください）。

```bash
# lambda_rest_handler.py / dynamodb_data_api.py
DYNAMODB_TABLE=your-table-name

# aurora_data_api.py
AURORA_CLUSTER_ARN=arn:aws:rds:ap-northeast-1:123456789012:cluster:your-cluster
DB_SECRET_ARN=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:your-secret
DB_NAME=app_db

# 共通
AWS_DEFAULT_REGION=ap-northeast-1
```

---

## 認証方式の選択指針

| シナリオ | 推奨認証方式 |
|---|---|
| B2C ユーザー認証 | **Cognito User Pools** オーソライザー |
| AWS サービス間（内部） | **IAM 認証**（SigV4） |
| 外部 OIDC（Auth0, Okta） | **JWT オーソライザー**（HTTP API） |
| カスタム認可ロジック | **Lambda Authorizer**（REQUEST 型） |
| GraphQL マルチ認証 | **Cognito + IAM + API Key** の組み合わせ |
| 開発・テスト | **API Key** |

---

## セキュリティ チェックリスト

- [ ] シークレット・認証情報はコードにハードコードせず **Secrets Manager / 環境変数** で管理
- [ ] API Gateway に **WAF** を設定（OWASP Top 10 対応のマネージドルール）
- [ ] スロットリング（バーストリミット・レートリミット）を適切に設定し `429` を返す
- [ ] DynamoDB の `UpdateItem` / `DeleteItem` は **ConditionExpression** でデータ所有者を検証
- [ ] Aurora Data API の SQL は **パラメータ化クエリ**（`:param_name`）を使用し SQL インジェクションを防止
- [ ] AppSync の **マルチ認証モード**でフィールドレベルのアクセス制御を設定

---

## パフォーマンス最適化ポイント

### Lambda コールドスタート対策

```python
# NG: ハンドラー内で毎回初期化（コールドスタート・ウォームアップ両方で遅延が発生）
def handler(event, context):
    dynamodb = boto3.resource("dynamodb")  # ← 毎回初期化

# OK: ハンドラー外で初期化（ウォームコンテナ時は再利用）
dynamodb = boto3.resource("dynamodb")
def handler(event, context):
    ...
```

### DynamoDB On-Demand vs Provisioned

| 選択基準 | On-Demand | Provisioned |
|---|---|---|
| トラフィック予測 | 困難・スパイク型 | 安定・予測可能 |
| 月間リクエスト | ～1000万 | 1000万超 |
| コスト効率 | 高め | 低め（安定時は約6倍安価） |
| 予約割引 | なし | 1年:54%割引 / 3年:77%割引 |

---

## 参考リンク

- [Amazon API Gateway ドキュメント](https://docs.aws.amazon.com/apigateway/)
- [AWS AppSync ドキュメント](https://docs.aws.amazon.com/appsync/)
- [Aurora RDS Data API ドキュメント](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html)
- [DynamoDB API リファレンス](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/)


---

📝 [Notionで詳細を見る]()
