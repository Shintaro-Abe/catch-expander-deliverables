## IaCコード（Terraform または CloudFormation）

# LLMOps on AWS — Terraform IaC（PoC）

> **⚠️ PoC品質**: このコードは学習・検証目的のスケルトン実装です。本番環境へ適用する前に、セキュリティレビュー・コスト試算・負荷試験を必ず実施してください。

---

## 概要

このTerraformコードは、**LLMOps（Large Language Model Operations）** 基盤をAWS上に構築します。

**LLMOps** とは、大規模言語モデル（LLM）の開発・デプロイ・監視・コスト最適化を一貫して管理するためのプラクティスと仕組みの総称です。従来のMLOpsを継承しつつ、プロンプト管理・ハルシネーション検知・非決定論的出力の評価など、LLM固有の課題に対応します。

---

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────────────┐
│                        クライアント（アプリ・ブラウザ）                    │
└─────────────────────┬───────────────────────────────────────────────┘
                      │ HTTPS POST /v1/infer
                      ▼
┌─────────────────────────────────────┐
│       API Gateway (REST API)         │ ← HTTPエンドポイント
└─────────────────────┬───────────────┘
                      │ Lambda Proxy統合
                      ▼
┌─────────────────────────────────────┐
│     Lambda: llmops-{env}-inference   │ ← Python 3.12 推論ハンドラー
│  ┌────────────────────────────────┐ │
│  │ validate → retrieve → infer   │ │
│  │ → evaluate → log_low_quality  │ │
│  └────────────────────────────────┘ │
└──────┬─────────────────┬────────────┘
       │                 │
       ▼                 ▼
┌────────────┐   ┌───────────────────────────┐
│  Amazon    │   │  OpenSearch Serverless     │
│  Bedrock   │   │  （ベクトルDB: RAG検索用）   │
│  Claude    │   └───────────────────────────┘
└────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│               Step Functions: LLMパイプライン（バッチ・非同期処理）          │
│  [入力検証] → [RAG検索] → [Bedrock推論] → [品質評価] → [閾値判定]         │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                   データストア（S3）                                     │
│   datasets-bucket │ prompts-bucket │ artifacts-bucket                │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    CloudWatch モニタリング                               │
│   メトリクス・アラーム・ダッシュボード │ SNS アラート通知                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## ファイル構成

| ファイル | 役割 |
|---|---|
| `variables.tf` | 全変数の定義（リージョン・モデルID・アラート設定など） |
| `main.tf` | 基盤リソース（S3・IAM・OpenSearch・Step Functions） |
| `lambda_api.tf` | 推論エンドポイント（Lambda + API Gateway） |
| `monitoring.tf` | 監視・アラート（CloudWatch ダッシュボード・アラーム・SNS） |

---

## 構築するAWSリソース一覧

### ストレージ（Amazon S3）

| バケット | 用途 |
|---|---|
| `{project}-{env}-datasets-{account}` | 評価用データセット・学習データ保存 |
| `{project}-{env}-prompts-{account}` | プロンプトテンプレート（バージョニング有効）|
| `{project}-{env}-artifacts-{account}` | ファインチューニング済みモデルアーティファクト |

> **ポイント**: プロンプトテンプレートをS3で管理し、バージョニングを有効にすることで、プロンプトの変更履歴を保持し、問題発生時にロールバックできます。

### 推論エンドポイント（Lambda + API Gateway）

- **Lambda関数**: Amazon Bedrockを呼び出すサーバーレス推論ハンドラー
  - **プロンプトキャッシング**対応（Anthropic `cache_control`）でトークンコストを最大90%削減
  - X-Rayトレーシング有効（リクエストの流れを可視化）
- **API Gateway**: REST APIエンドポイント `/v1/infer`（POST）

### RAGパイプライン（OpenSearch Serverless）

- **ベクトルDB**: `VECTORSEARCH`タイプのコレクション
  - テキストを数値ベクトルに変換（埋め込み）して意味的に近いドキュメントを高速検索
  - LLMが社内情報・最新情報を参照して回答精度を向上（RAG: Retrieval-Augmented Generation）

### LLMパイプライン（Step Functions）

```
ValidateInput → RetrieveContext → InvokeBedrockModel → EvaluateResponse
                                                              ↓
                                                   CheckQualityThreshold
                                                    ↙            ↘
                                             ReturnResponse   LogLowQuality
                                                                    ↓
                                                             ReturnResponse
```

| ステップ | 説明 |
|---|---|
| `ValidateInput` | 入力クエリのバリデーション（空・長さチェック）|
| `RetrieveContext` | OpenSearch Serverlessへのベクトル検索（RAG） |
| `InvokeBedrockModel` | Amazon Bedrockでテキスト生成（Claudeモデル）|
| `EvaluateResponse` | LLM-as-Judgeによる品質評価（Faithfulness・Relevancy）|
| `CheckQualityThreshold` | 品質スコアの閾値判定（デフォルト: 0.7）|
| `LogLowQuality` | 低品質レスポンスをCloudWatch Logsに記録 |

### モニタリング（CloudWatch）

| アラーム | トリガー条件 |
|---|---|
| Lambda エラー数 | 1分間に5件以上のエラー |
| Lambda P99レイテンシ | P99が25秒を超過 |
| Lambda スロットリング | 1件でもスロットリング発生 |
| API Gateway 5xxエラー | 1分間に10件以上 |
| **低品質レスポンス** | **5分間に10件以上（プロンプトドリフト検知）** |
| パイプライン失敗 | 1分間に3件以上の実行失敗 |

> **ポイント**: 低品質レスポンスアラームは**プロンプトドリフト**（モデルのサイレント更新やユーザーパターン変化による品質低下）の早期検知に特に重要です。

---

## 前提条件

- **Terraform** >= 1.5.0
- **AWS CLI** 設定済み（`aws configure` または IAM Role）
- **Amazon Bedrock モデルアクセス** を事前に有効化
  - AWSコンソール > Amazon Bedrock > モデルアクセス > `anthropic.claude-3-5-sonnet-20241022-v2:0` を有効化

---

## デプロイ手順

### 1. 初期化

```bash
terraform init
```

### 2. 変数設定（推奨: `terraform.tfvars` ファイルを作成）

```hcl
# terraform.tfvars（Gitにコミットしないこと）
project_name   = "myllmops"
environment    = "dev"
aws_region     = "ap-northeast-1"
bedrock_model_id = "anthropic.claude-3-5-sonnet-20241022-v2:0"
alert_email    = "your-email@example.com"
```

### 3. 実行計画の確認

```bash
terraform plan -var-file="terraform.tfvars"
```

### 4. デプロイ

```bash
terraform apply -var-file="terraform.tfvars"
```

### 5. エンドポイントへのリクエスト例

```bash
# terraform output で API URLを取得
API_URL=$(terraform output -raw api_endpoint)

# 推論リクエスト
curl -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d '{"query": "LLMOpsとMLOpsの違いを教えてください"}'
```

---

## 主要変数

| 変数名 | デフォルト値 | 説明 |
|---|---|---|
| `project_name` | `llmops` | リソース名プレフィックス |
| `environment` | `dev` | 環境名（dev/staging/prod）|
| `aws_region` | `ap-northeast-1` | デプロイ先リージョン |
| `bedrock_model_id` | `anthropic.claude-3-5-sonnet-20241022-v2:0` | 使用するLLMモデル |
| `quality_score_threshold` | `0.7` | 低品質判定の閾値（0.0〜1.0）|
| `alert_email` | `""` | アラート通知先メール |
| `log_retention_days` | `30` | CloudWatch Logsの保持期間 |

---

## コスト概算（月次・PoC規模）

| サービス | 概算コスト | 備考 |
|---|---|---|
| Lambda | $1〜5 | 1M リクエスト以内は無料枠あり |
| API Gateway | $3〜10 | 1M呼び出しあたり$3.50 |
| OpenSearch Serverless | $175〜 | 最小構成でも固定費が発生 |
| Step Functions | $1〜5 | 状態遷移数に応じた従量課金 |
| CloudWatch | $5〜15 | ダッシュボード・アラーム・ログ |
| Amazon Bedrock | 従量課金 | Claude Sonnet: $3/Mトークン（入力）|

> **コスト削減のヒント**: Anthropicのプロンプトキャッシングを活用すると、繰り返し使うシステムプロンプトのトークンコストを最大**90%削減**できます。

---

## 本番化に向けてのチェックリスト

- [ ] API Gatewayの認証を追加（AWS IAM または Amazon Cognito）
- [ ] OpenSearch Serverlessのネットワークポリシーをプライベート（VPCエンドポイント）に変更
- [ ] S3バケットのライフサイクルポリシーでコスト最適化
- [ ] `_retrieve_context()` 関数にOpenSearch Serverlessへの実際のベクトル検索を実装
- [ ] `_evaluate_response()` 関数にLLM-as-Judgeの実際の評価ロジックを実装
- [ ] Terraform Stateをリモートバックエンド（S3 + DynamoDB）に移行
- [ ] セキュリティグループ・VPC設定の追加
- [ ] AWS WAFをAPI Gatewayに適用（プロンプトインジェクション対策）
- [ ] Amazon Bedrock Guardrailsでコンテンツフィルタリングを設定

---

## 参考リンク

- [Amazon Bedrock ドキュメント](https://docs.aws.amazon.com/bedrock/)
- [OpenSearch Serverless ベクトル検索](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vector-search.html)
- [AWS Step Functions ドキュメント](https://docs.aws.amazon.com/step-functions/)
- [Anthropic プロンプトキャッシング](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)


## プログラムコード（Python またはユーザープロファイルの技術スタック）

# LLMOps 実装サンプル集

> **PoC品質のコードです。本番利用前に認証・エラーハンドリング・テストを必ず追加してください。**

LLM（大規模言語モデル）の開発・運用サイクル全体を管理する **LLMOps** の主要コンポーネントをPythonで実装したサンプルです。

---

## ファイル構成

| ファイル | 役割 | 主要機能 |
|---------|------|----------|
| `prompt_manager.py` | プロンプト管理 | バージョン管理・A/Bテスト・カナリアデプロイ |
| `drift_detector.py` | ドリフト検知 | Wasserstein距離・PSI・KLダイバージェンス |
| `cost_optimizer.py` | コスト最適化 | セマンティックキャッシュ・モデルルーティング |
| `llmops_evaluation.py` | 品質評価 | RAGAS・G-Eval・LLM-as-Judge |

---

## LLMOpsとMLOpsの違い

| 比較軸 | MLOps | LLMOps |
|--------|-------|--------|
| **主な成果物** | モデルバイナリ | プロンプト・埋め込み・ベクトルDB |
| **コスト構造** | 訓練コストが主体 | トークン課金（推論コスト）が主体 |
| **評価手法** | 精度・F1スコア | LLM-as-Judge・人間評価・RAGAS |
| **出力の性質** | 決定論的・一貫性あり | 非決定論的・コンテキスト依存 |

---

## クイックスタート

### 必要なパッケージ

```bash
pip install boto3          # AWS SDK（Amazon Bedrock連携）
pip install mlflow         # 実験トラッキング
```

> 本サンプルはサードパーティライブラリへの依存を最小化しています。  
> LLM API呼び出し部分（`_mock_*` メソッド）を実際の実装に置き換えてください。

### 各モジュールの実行

```bash
# プロンプト管理のデモ
python prompt_manager.py

# ドリフト検知のデモ
python drift_detector.py

# コスト最適化のデモ
python cost_optimizer.py

# 評価フレームワークのデモ
python llmops_evaluation.py
```

---

## 各モジュールの説明

### 1. プロンプト管理（`prompt_manager.py`）

LLMOpsでは **プロンプトがコードと同等の重要な成果物** です。

```python
from prompt_manager import PromptVersion, PromptEnvironment, PromptStore

store = PromptStore()
v1 = PromptVersion(
    name="customer_support",
    version="1.0.0",
    template="{{company_name}}のサポートです。{{user_query}}",
    variables=["company_name", "user_query"],
    environment=PromptEnvironment.PRODUCTION,
)
store.save(v1)
rendered = v1.render(company_name="株式会社ABC", user_query="注文状況を教えて")
```

**主要機能:**
- **セマンティックバージョニング**: `X.Y.Z` 形式（Major=構造変更 / Minor=機能追加 / Patch=修正）
- **不変性の原則**: 一度保存したバージョンは変更不可（変更は必ず新バージョンで）
- **A/Bテスト**: ユーザーIDベースの一貫したバリアント振り分け
- **カナリアデプロイ**: 最初は新バージョンに5〜10%のトラフィックをルーティング

---

### 2. ドリフト検知（`drift_detector.py`）

**プロンプトドリフト**とは、プロンプトを変更していないのにLLMの挙動が変化する現象です。  
（例: GPT-4の素数判定精度が84%→51%に低下したスタンフォード大学の研究事例）

```python
from drift_detector import DriftMonitor, DriftMonitorConfig

config = DriftMonitorConfig(wasserstein_warning=0.1, psi_critical=0.2)
monitor = DriftMonitor(config)
monitor.set_baseline(baseline_embeddings)  # 安定期間の埋め込みを登録

# 本番リクエストを5%サンプリングで監視
monitor.add_sample(new_embedding)
alerts = monitor.check_drift()
```

**検知手法の使い分け:**

| 手法 | 用途 | 推奨閾値 |
|------|------|----------|
| **Wasserstein距離** | 埋め込み分布のドリフト定量化 | warning: 0.1 / critical: 0.2 |
| **PSI** | 母集団の安定性測定 | warning: 0.1 / critical: 0.2 |
| **KLダイバージェンス** | 確率分布の差異測定 | warning: 0.1 |

---

### 3. コスト最適化（`cost_optimizer.py`）

```python
from cost_optimizer import SemanticCache, ModelRouter, ModelConfig, LLMCostOptimizer

cache = SemanticCache(similarity_threshold=0.85)
router = ModelRouter([
    ModelConfig("bedrock/haiku",  input_cost_per_1k=0.00025, ..., capability_level=1),
    ModelConfig("bedrock/opus",   input_cost_per_1k=0.015,   ..., capability_level=3),
])
optimizer = LLMCostOptimizer(cache, router, budget_manager)

result = optimizer.process(query, embedding, complexity_score=0.3)
```

**マルチティアキャッシュアーキテクチャ:**

```
リクエスト
  ↓ 1. セマンティックキャッシュ検索（ヒット → API呼び出しゼロ: 最大69%削減）
  ↓ 2. モデルルーティング（安価なモデルへ: 最大87%削減）
  ↓ 3. Anthropicプロンプトキャッシュ（cache_control: 最大90%削減）
  → LLM API 呼び出し
```

---

### 4. 評価フレームワーク（`llmops_evaluation.py`）

```python
from llmops_evaluation import RAGASEvaluator, EvaluationPipeline, RAGTestCase

pipeline = EvaluationPipeline(ragas, geval, sample_rate=0.05)  # 5%サンプリング

result = pipeline.run_evaluation(RAGTestCase(
    query="Pythonのリスト内包表記とは？",
    retrieved_context=["リスト内包表記は..."],
    generated_answer="リスト内包表記は[式 for 変数 in イテラブル]...",
    ground_truth="正解の回答テキスト",
))
```

**RAGAS 主要メトリクス:**

| メトリクス | 説明 | 目標スコア |
|-----------|------|----------|
| **Faithfulness（忠実性）** | 回答がコンテキストに基づいているか | ≥ 0.8 |
| **Answer Relevancy（関連性）** | 質問に関連した回答か | ≥ 0.7 |
| **Context Precision（精度）** | 取得文書の精度 | ≥ 0.7 |
| **Context Recall（再現率）** | 必要情報を取得できているか | ≥ 0.7 |

> **重要**: LLM-as-Judgeは本番クリティカルパスで同期実行してはいけません。  
> バックグラウンドで非同期に、日次**5%サンプリング**で評価します。

---

## AWS との連携（本番環境）

本サンプルのモック実装を以下に置き換えることで本番環境で動作します:

```python
import boto3

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

# LLM呼び出し（_mock_llm_call の置き換え）
response = bedrock.invoke_model(
    modelId="anthropic.claude-3-haiku-20240307-v1:0",
    body=json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": query}],
    }),
)

# 埋め込み生成（PoC用ダミー埋め込みの置き換え）
embed_response = bedrock.invoke_model(
    modelId="amazon.titan-embed-text-v2:0",
    body=json.dumps({"inputText": query}),
)
embedding = json.loads(embed_response["body"].read())["embedding"]
```

---

## LLMOps成熟度モデル

| ステージ | 特徴 | 本サンプルの対応 |
|---------|------|----------------|
| **1. 実験** | 手動デプロイ・最小限の監視 | - |
| **2. 運用** | 自動デプロイ・初期監視 | `prompt_manager.py` |
| **3. スケール** | 強力な可観測性・コスト監視 | `drift_detector.py`, `cost_optimizer.py` |
| **4. 自律** | 自動再評価・適応型リソース配分 | `llmops_evaluation.py` |


---

📝 [Notionで詳細を見る](https://app.notion.com/p/LLMOps-35347b55202e81a4a531eaa939c5e789)
