## プログラムコード（Python またはユーザープロファイルの技術スタック）

# AIエージェントハーネス実装サンプル（PoC）

> **PoC 品質**: このコードは学習・プロトタイピング目的のスケルトン実装です。  
> 本番利用前に認証強化・エラーハンドリング・業務ロジックの追加が必要です。

---

## ハーネスとは何か？

```
Agent = Model + Harness
```

| 役割 | 担当 |
|------|------|
| 知性（考える）| AIモデル（Claude 等） |
| 体と環境（動く）| **ハーネス**（このリポジトリ） |

ハーネスとは「AIモデル以外のすべてのコード・設定・実行ロジック」の総体です。  
モデルがどれほど優秀でも、ハーネスがなければ現実のタスクを完遂できません。

---

## アーキテクチャ概要

```
┌─────────────────────────────────────────────────┐
│                ユーザー入力                       │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│           プロンプト管理                          │
│  システムプロンプト ＋ ツール定義 ＋ キャッシュ   │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│        エージェントループ制御（ReAct）            │
│   考える → ツール呼び出し → 観察 → 繰り返す      │
│                    │                             │
│  ┌─────────────────▼──────────────────────┐     │
│  │           ツール管理                    │     │
│  │  read_file / calculate / web_search    │     │
│  └────────────────────────────────────────┘     │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│              状態管理                            │
│   会話履歴・トークン使用量・セッションID          │
└─────────────────────────────────────────────────┘
                横断的関心事
┌──────────────────────────────────────────────────┐
│  セキュリティ              可観測性               │
│  InjectionGuard            HarnessTracer         │
│  IdempotencyStore          RetryHandler          │
└──────────────────────────────────────────────────┘
```

---

## ファイル構成

| ファイル | 役割 |
|---------|------|
| `agent_harness.py` | ハーネスのコア。ReAct ループ・プロンプト管理・状態管理を統合 |
| `tools.py` | ツール登録（ToolRegistry）とサンプルツール実装 |
| `loop_patterns.py` | 3 種類のループパターン（ReAct / Plan-and-Execute / Reflection） |
| `security_and_observability.py` | リトライ・冪等性・インジェクション検出・トレーシング |

---

## セットアップ

```bash
# 依存ライブラリのインストール
pip install anthropic

# API キーの設定（シークレットはコードに書かないこと！）
export ANTHROPIC_API_KEY="your-api-key-here"
```

---

## クイックスタート

### 基本的な使い方（ReAct ループ）

```python
from agent_harness import AgentHarness, HarnessConfig

harness = AgentHarness(
    config=HarnessConfig(
        max_turns=10,
        system_prompt="あなたは計算が得意なアシスタントです。",
    )
)

session = harness.run("144 の平方根と 2 の 10 乗を計算してください")
# コストサマリーも自動表示されます
```

### Plan-and-Execute（複雑なタスク）

```python
from loop_patterns import PlanAndExecute
from tools import default_registry

planner = PlanAndExecute(
    tools=default_registry.to_anthropic_schema(),
    tool_executor=lambda name, inp: default_registry.execute(name, inp),
)

result = planner.run("Pythonの基礎を調べて、初心者向けのサマリーを作成してください")
print(result["final"])
```

### Reflection（高品質出力）

```python
from loop_patterns import ReflectionLoop

reflector = ReflectionLoop(quality_threshold=8, max_iterations=3)
result = reflector.run("Pythonで素数を判定する関数を書いてください")
print(f"最終スコア: {result['final_score']}/10")
print(result["output"])
```

---

## ループパターンの選び方

```
タスクの特性
    │
    ├─ リアルタイム対話・シンプル ──────── ReAct（agent_harness.py）
    │
    ├─ 複雑な多段階・コスト重視 ────────── Plan-and-Execute
    │
    └─ コード生成・文書・品質最優先 ────── Reflection
```

---

## セキュリティ対策（OWASP LLM01 対応）

```python
from security_and_observability import InjectionGuard

guard = InjectionGuard(block_on_detection=True)

# 外部データを使う前に必ずチェック
is_safe, patterns = guard.check(external_data, source="web_fetch")
if not is_safe:
    raise ValueError(f"インジェクション試行を検出: {patterns}")

# 外部データをモデルに渡すときは分離してラベル付け
safe_content = guard.sanitize(external_data, source="document")
```

---

## プロンプトキャッシュによるコスト削減

このサンプルでは Anthropic のプロンプトキャッシュを有効化しています。

| トークン種別 | コスト比率 |
|------------|-----------|
| 通常の入力トークン | 100% |
| キャッシュ書き込み（初回のみ） | 125% |
| **キャッシュ読み出し（2回目以降）** | **10%** |

システムプロンプトとツール定義（変化しない部分）をキャッシュすることで、  
長いセッションでは**入力コストを最大 90% 削減**できます。

---

## 参考資料

- [Anthropic Claude API ドキュメント](https://docs.anthropic.com)
- [OWASP Top 10 for LLM Applications 2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- Yao et al. (2022) "ReAct: Synergizing Reasoning and Acting in Language Models"
- Andrew Ng "Agentic Design Patterns" シリーズ


## IaCコード（Terraform または CloudFormation）

# AIエージェントハーネス IaC (Terraform)

> **PoC品質** — 本番環境での利用前に、セキュリティレビュー・詳細設定・業務ロジックの実装が必要です。

---

## 概要

このTerraformコードは、**AIエージェントハーネス**の基盤インフラを AWS 上に構築します。

### ハーネスとは？

`エージェント = モデル（頭脳）+ ハーネス（体と環境）`

AIモデルがどれほど優秀でも、「手足・目・耳・記憶・安全装置」がなければ実世界でタスクを完遂できません。**ハーネス**はその体全体を構成するインフラ基盤です。

---

## アーキテクチャ図

```
                            ┌─────────────────────────────────────────────┐
                            │           AIエージェントハーネス              │
                            │                                             │
  ユーザー / 外部システム     │  ┌──────────────┐                          │
       │                   │  │  API Gateway │  (同期リクエスト)           │
       ├──[POST /invoke]──▶│  │  HTTP API v2 │                          │
       │                   │  └──────┬───────┘                          │
       │                   │         │                                   │
  EventBridge──[イベント]──▶│  ┌──────▼───────────┐   ┌──────────────┐  │
                            │  │  Lambda          │   │  Bedrock     │  │
                            │  │  agent_entry     ├──▶│  Agent       │  │
                            │  │  (エントリポイント) │◀──│  (エージェント│  │
                            │  └──────────────────┘   │   ループ制御) │  │
                            │                         └──────┬───────┘  │
                            │  ┌──────────────────┐         │           │
                            │  │  Lambda          │◀────────┘           │
                            │  │  tool_executor   │  (Action Group呼出) │
                            │  │  (ツール実行)     │                     │
                            │  └──────┬───────────┘                     │
                            │         │                                   │
                            │  ┌──────▼───────────┐                     │
                            │  │  DynamoDB        │                     │
                            │  │  session_state   │  (状態管理)          │
                            │  └──────────────────┘                     │
                            │                                             │
                            │  SQS DLQ / CloudWatch Logs / Alarms        │
                            └─────────────────────────────────────────────┘
```

---

## 構成コンポーネント

| コンポーネント | AWSサービス | 役割 |
|---|---|---|
| **エントリポイント** | API Gateway (HTTP API v2) | ユーザーからの同期リクエスト受け口 |
| **非同期トリガー** | EventBridge | 外部イベントによるエージェント非同期起動 |
| **エージェントループ制御** | Amazon Bedrock Agent | ReActパターンのループを管理・LLM推論実行 |
| **ツール管理** | Lambda (tool_executor) | Action Groupとしてツール関数を実行 |
| **エントリ転送** | Lambda (agent_entry) | リクエストをBedrockへブリッジ |
| **状態管理** | DynamoDB | セッション状態・中間成果物の永続化 |
| **エラーハンドリング** | SQS (DLQ) | 配信失敗イベントの退避・リプレイ |
| **可観測性** | CloudWatch Logs / Alarms | ログ集中管理・エラー監視・アラート |
| **セキュリティ** | IAM ロール/ポリシー | 最小権限の原則による権限分離 |

---

## ファイル構成

```
.
├── main.tf       # プロバイダー・変数・DynamoDB・EventBridge・CloudWatch・出力値
├── bedrock.tf    # Bedrock Agent・エイリアス・Action Group (ツール定義)
├── lambda.tf     # Lambda関数・APIGateway・Pythonソースコード（インライン）
├── iam.tf        # IAMロール・ポリシー (最小権限設計)
└── README.md     # このファイル
```

---

## エージェントループの動作フロー

```
1. ユーザーが POST /invoke にリクエスト送信
   {"input": "売上データを分析して要約してください", "session_id": "abc-123"}

2. agent_entry Lambda がリクエストを受け取り Bedrock Agent を呼び出す

3. Bedrock Agent がエージェントループを開始 (ReActパターン)
   ┌─ [考える] LLMが「何をすべきか」を推論
   ├─ [行動する] search_knowledge ツールを呼び出す決定
   ├─ [観察する] tool_executor から検索結果を受け取る
   ├─ [考える] 結果を踏まえて次のアクションを推論
   ├─ [行動する] save_session_state で中間結果を保存
   └─ [完了] タスク完了と判断 → 最終回答を生成

4. agent_entry Lambda がレスポンスをユーザーに返却
   {"session_id": "abc-123", "response": "分析結果: ..."}
```

---

## 前提条件

- Terraform >= 1.5.0
- AWS CLI 設定済み (`aws configure` または IAMロール)
- Amazon Bedrockで利用したいモデルへのアクセス許可が付与済み
  - AWSコンソール → Amazon Bedrock → モデルアクセス → 有効化

---

## デプロイ手順

```bash
# 1. Terraformの初期化 (プロバイダーのダウンロード)
terraform init

# 2. 実行計画の確認 (変更内容のプレビュー)
terraform plan -var="project_name=myagent-harness"

# 3. インフラのデプロイ
terraform apply -var="project_name=myagent-harness"

# デプロイ完了後、エンドポイントURLが出力されます
# Outputs:
#   api_endpoint = "https://xxxx.execute-api.us-east-1.amazonaws.com/invoke"
```

---

## 動作確認

```bash
# エンドポイントURLを変数に設定
ENDPOINT=$(terraform output -raw api_endpoint)

# エージェントにリクエストを送信
curl -X POST "$ENDPOINT" \
  -H "Content-Type: application/json" \
  -d '{"input": "こんにちは。あなたは何ができますか？"}'

# 期待するレスポンス例:
# {"session_id": "xxx", "response": "こんにちは！私は..."}
```

---

## 主要変数一覧

| 変数名 | デフォルト値 | 説明 |
|---|---|---|
| `aws_region` | `us-east-1` | デプロイ先リージョン |
| `project_name` | `ai-agent-harness` | リソース名プレフィックス |
| `foundation_model_id` | `anthropic.claude-3-5-sonnet-20241022-v2:0` | 使用するLLMモデルID |
| `agent_instruction` | (システムプロンプト) | エージェントへの行動指針 |
| `session_ttl_seconds` | `1800` | セッションタイムアウト (秒) |
| `log_retention_days` | `30` | ログ保存期間 (日) |
| `enable_pitr` | `false` | DynamoDB ポイントインタイムリカバリ |
| `error_alarm_threshold` | `5` | エラーアラーム閾値 (件/分) |
| `alarm_sns_topic_arn` | `""` | アラーム通知先SNSトピックARN |

---

## カスタマイズポイント

### ツールの追加

`bedrock.tf` の `function_schema` に新しい `functions` ブロックを追加し、
`lambda.tf` の `_dispatch` 関数に対応する処理を実装してください。

```hcl
# bedrock.tf に追加する例
functions {
  name        = "call_external_api"
  description = "外部APIを呼び出してデータを取得する"
  parameters {
    map_block_key = "endpoint"
    type          = "string"
    description   = "APIエンドポイントURL"
    required      = true
  }
}
```

### 知識ベースの統合

`tool_executor.py` の `_search_knowledge` 関数に、
以下のいずれかの検索ロジックを実装してください:

- **Bedrock Knowledge Base**: `bedrock-agent-runtime.retrieve()`
- **Amazon OpenSearch**: `opensearchpy` クライアント経由でのベクター検索
- **外部API**: `requests` ライブラリ経由でのHTTPリクエスト

---

## セキュリティ注意事項

1. **最小権限**: IAMポリシーはすでに特定ARNに制限していますが、本番環境ではさらに絞り込みを推奨
2. **プロンプトインジェクション対策**: `tool_executor` に渡されるパラメータは必ず検証・サニタイズする (OWASP LLM01)
3. **シークレット管理**: APIキー等は環境変数に直接設定せず、AWS Secrets Manager を使用する
4. **ネットワーク分離**: 本番環境ではLambdaをVPC内プライベートサブネットに配置することを推奨

---

## リソース削除

```bash
# 作成したリソースをすべて削除 (課金停止)
terraform destroy -var="project_name=myagent-harness"
```


---

📝 [Notionで詳細を見る]()
