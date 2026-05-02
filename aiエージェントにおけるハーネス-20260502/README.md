## プログラムコード（Python またはユーザープロファイルの技術スタック）

# AIエージェントハーネス実装サンプル（PoC）

> **PoC品質**: このコードは概念実証（Proof of Concept）用です。本番環境での利用前に十分なテストと改修を行ってください。

---

## 概要

AIエージェントの「**ハーネス（Harness）**」を Python で実装したサンプルコードです。

### ハーネスとは？

| 比喩 | 説明 |
|------|------|
| LLM（モデル）= 優秀な専門家 | 知識と推論力はあるが、道具も記憶もシステムアクセスも持たない |
| ハーネス = オフィス環境 | 電話・PC・書類棚・スケジュール管理など、専門家を支える全インフラ |

> エンタープライズAI障害の **65% はハーネス欠陥**（コンテキストドリフト・スキーマ不整合・状態劣化）に起因します。

---

## ファイル構成

```
.
├── agent_harness.py    # メインハーネス（ReAct ループ・状態管理・統合制御）
├── tool_registry.py    # ツールレジストリ（登録・発見・スキーマ生成・実行）
├── memory_manager.py   # メモリ管理（短期・長期・エピソード記憶）
├── error_handler.py    # エラーハンドリング（リトライ・フォールバック・サーキットブレーカー）
└── README.md           # このファイル
```

---

## アーキテクチャ図

```
ユーザー入力
     │
     ▼
┌────────────────────────────────────────────────────┐
│               AgentHarness（agent_harness.py）      │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │              ReAct ループ                    │   │
│  │                                             │   │
│  │  [1] LLM呼び出し ──→ stop_reason?           │   │
│  │                         │                  │   │
│  │            end_turn ────┘         tool_use │   │
│  │               │                       │   │   │
│  │          最終応答返却            [2] ツール実行│   │
│  │                                       │   │   │
│  │                    ツール結果をメモリに追加   │   │
│  │                           │               │   │
│  │                    [1]に戻る ──────────────┘   │
│  └─────────────────────────────────────────────┘   │
│          ↑              ↑              ↑            │
│  ToolRegistry    MemoryManager   AgentErrorHandler  │
│  (tool_registry) (memory_manager) (error_handler)   │
└────────────────────────────────────────────────────┘
```

---

## モジュール詳細

### 1. `tool_registry.py` — ツールレジストリ

**役割**: エージェントが利用可能なツールを一元管理する「内線電話帳」

| 機能 | メソッド | 説明 |
|------|----------|------|
| 登録 | `@registry.register(...)` | デコレータで関数をツールとして登録 |
| 発見 | `registry.discover(keyword, tags)` | キーワード・タグで検索 |
| スキーマ生成 | `registry.get_schema(allowed_tools)` | LLM向けJSON スキーマを生成 |
| 実行 | `registry.call(name, **kwargs)` | ツールを名前で呼び出し |
| ヘルスチェック | `registry.health_check()` | 全ツールの稼働確認 |

**Just-in-Time インジェクション**: `allowed_tools` で必要なツールのみをプロンプトに注入し、LLMのアテンション効率を向上させます。

```python
# ツール定義の例
@registry.register(name="calculator", description="四則演算を実行", tags=["math"])
def calculator(expression: str) -> str:
    ...
```

---

### 2. `memory_manager.py` — メモリ管理

**役割**: エージェントの記憶を階層的に管理する

| 記憶の種類 | クラス | 比喩 |
|-----------|--------|------|
| ワーキングメモリ | `WorkingMemory` | 作業台（今やっている仕事の書類） |
| エピソード記憶 | `EpisodicMemory` | 日記（過去の経験の記録） |
| セマンティック記憶 | `SemanticMemory` | ファイリングキャビネット（整理された知識） |

**コンテキスト圧縮**: コンテキストウィンドウの上限に達すると古いメッセージを自動的にサマリーに変換します（要約ドリフト対策として原文はエピソード記憶に保存）。

```python
manager = MemoryManager()
manager.add_user_message("AWSとは？")
manager.add_assistant_message("AWSはAmazonのクラウドサービスです。")
manager.remember("user_goal", "クラウドを学習する", tags=["goal"])

# RAG的アプローチでのキーワード検索（全記憶層横断）
results = manager.recall_by_keyword("AWS")
```

---

### 3. `error_handler.py` — エラーハンドリング

**役割**: 3層のエラー保護メカニズムを提供

```
リクエスト
  └─ [1] リトライ（指数バックオフ + ジッター）    ← 一時エラー（429, 500等）
       └─ [2] フォールバック（代替プロバイダー）   ← リトライ枯渇時
            └─ [3] サーキットブレーカー            ← 持続的障害
                 └─ [4] エスカレーション / 最終応答
```

| 機能 | クラス/関数 | 役割 |
|------|------------|------|
| リトライ | `with_retry()` | 指数バックオフ + ジッターで再試行 |
| フォールバック | `with_fallback()` | プロバイダーチェーン（Claude Opus → Sonnet → Haiku） |
| サーキットブレーカー | `CircuitBreaker` | 持続的障害時にリクエストをブロック |
| エラー分類 | `classify_error()` | HTTP ステータスからリトライ可否を判定 |

```python
handler = AgentErrorHandler()
result = handler.execute_with_protection(
    primary_func=lambda: call_claude_opus(prompt),
    fallback_funcs=[
        ("claude-sonnet", lambda: call_claude_sonnet(prompt)),
        ("claude-haiku",  lambda: call_claude_haiku(prompt)),
    ],
)
```

---

### 4. `agent_harness.py` — メインハーネス

**役割**: 上記3モジュールを統合してエージェントループを制御する

**主要コンポーネント**:

| コンポーネント | クラス | 役割 |
|--------------|--------|------|
| 状態管理 | `AgentState` | IDLE→PLANNING→EXECUTING→VERIFYING→COMPLETED |
| バジェット管理 | `BudgetConfig` | ターン数・ツール呼び出し数・タイムアウトの上限設定 |
| ループ検出 | `LoopDetector` | SHA256ハッシュで同一状態の繰り返しを検知 |
| 設定 | `HarnessConfig` | モデルID・システムプロンプト・各種設定を一元管理 |

---

## クイックスタート

### 動作確認（boto3 不要）

```bash
# Python 3.10+ 推奨
python tool_registry.py    # ツールレジストリの動作確認
python memory_manager.py   # メモリ管理の動作確認
python error_handler.py    # エラーハンドリングの動作確認
python agent_harness.py    # ハーネス全体の動作確認（スタブLLM使用）
```

### AWS Bedrock との接続

```python
# agent_harness.py の先頭で以下を変更
BEDROCK_CLIENT_AVAILABLE = True  # False → True

# 必要なライブラリのインストール
# pip install boto3

# AWS 認証設定（環境変数または IAM ロール）
# export AWS_DEFAULT_REGION=us-west-2
# export AWS_ACCESS_KEY_ID=...      ← 本番では Secrets Manager を使用
# export AWS_SECRET_ACCESS_KEY=...  ← 本番では Secrets Manager を使用
```

---

## 本番化に向けたチェックリスト

- [ ] `BEDROCK_CLIENT_AVAILABLE = True` に変更して boto3 を接続
- [ ] `BudgetConfig` の上限値をユースケースに合わせて調整
- [ ] `RetryConfig.max_attempts` を調整（推奨: 3〜5回）
- [ ] メモリバックエンドをインメモリ → 永続ストア（DynamoDB / PostgreSQL）に変更
- [ ] CloudWatch EMF でメトリクスを送信（トークン使用量・ツール呼び出し数・レイテンシ）
- [ ] IAM ロールを最小権限に絞り込む（エージェント専用ロールを作成）
- [ ] Amazon Bedrock Guardrails でプロンプトインジェクション対策を実装
- [ ] シークレット（API キー等）は AWS Secrets Manager Agent で管理

---

## 関連 AWS サービス

| 目的 | 推奨サービス |
|------|-------------|
| LLM 呼び出し | Amazon Bedrock Converse API |
| フルマネージドハーネス | Amazon Bedrock AgentCore（2026年プレビュー） |
| 分散トレーシング | AWS Distro for OpenTelemetry (ADOT) |
| ログ・メトリクス | CloudWatch Logs + Embedded Metric Format |
| シークレット管理 | AWS Secrets Manager Agent |
| 入出力検証 | Amazon Bedrock Guardrails |
| インフラ定義 | AWS CDK (`aws-cdk-lib.aws_bedrock_alpha`) |


---

📝 [Notionで詳細を見る](https://www.notion.so/AI-35447b55202e81428b44e1221c6955ba)
