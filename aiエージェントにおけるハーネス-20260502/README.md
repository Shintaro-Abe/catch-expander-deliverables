## プログラムコード（Python またはユーザープロファイルの技術スタック）

# AIエージェントハーネス - Pythonスケルトン実装

> **PoC品質**: 本番利用前にセキュリティレビュー・認証基盤・永続化バックエンドの接続を行うこと。

---

## 概要

**「Agent = Model + Harness」** という設計思想に基づいた、AIエージェントハーネスのPythonスケルトン実装です。

LLM（大規模言語モデル）本体は推論・生成に専念し、それ以外の **ツール管理・メモリ・コンテキスト・ライフサイクルフック・信頼性制御・可観測性** をハーネスが担います。

```
┌─────────────────────────────────────────────────────┐
│                   AgentHarness                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │  Tools   │  │  Memory  │  │  Observability   │  │
│  │ Registry │  │ Manager  │  │   Collector      │  │
│  └──────────┘  └──────────┘  └──────────────────┘  │
│  ┌──────────────────────────────────────────────┐   │
│  │        Circuit Breaker + Retry Logic        │   │
│  └──────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────┐   │
│  │          Lifecycle Hook System              │   │
│  └──────────────────────────────────────────────┘  │
└─────────────────┬───────────────────────────────────┘
                  │ API呼び出し
         ┌────────▼────────┐
         │   Anthropic     │
         │   Claude API    │
         └─────────────────┘
```

---

## ファイル構成

| ファイル | 役割 |
|--------|------|
| `agent_harness.py` | ハーネス本体。エージェントループ・フック・リトライ・コンテキスト管理 |
| `tools.py` | ツールレジストリと組み込みツール（ReadFile・WriteFile・Bash等）|
| `memory.py` | 3層メモリ管理（短期・ワーキング・長期記憶）|
| `observability.py` | 分散トレース・構造化ログ・メトリクス・評価シグナル |

---

## セットアップ

```bash
# 依存パッケージのインストール
pip install anthropic

# Anthropic APIキーを環境変数に設定（シークレットをコードに書かないこと）
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

## クイックスタート

```python
from agent_harness import AgentHarness, AgentConfig, HookEvent, example_block_hook
from tools import ToolRegistry, ReadFileTool, BashTool

# 1. ツールレジストリのセットアップ
registry = ToolRegistry()
registry.register(ReadFileTool())
registry.register(BashTool(timeout=30, allowed_commands=["ls", "cat", "grep"]))

# 2. ハーネスの設定と初期化
config = AgentConfig(
    model="claude-opus-4-7",
    max_turns=10,
    enable_prompt_cache=True,  # プロンプトキャッシュでコスト削減
)
harness = AgentHarness(config=config, tools=registry)

# 3. フックの登録（危険コマンドのブロック）
harness.add_hook(HookEvent.PRE_TOOL_USE, example_block_hook)

# 4. エージェント実行
result = harness.run("現在のディレクトリのPythonファイル一覧を表示してください。")
print(result)
```

---

## 主要コンポーネント

### ライフサイクルフック

エージェントの判断レイヤーの外側で、モデルの自己規制に依存せず外部から制約を強制する仕組みです。

```python
def my_audit_hook(ctx: HookContext) -> None:
    if ctx.event == HookEvent.PRE_TOOL_USE:
        print(f"ツール呼び出し: {ctx.tool_name} | 入力: {ctx.tool_input}")
        # ctx.block = True にするとツール実行を拒否できる

harness.add_hook(HookEvent.PRE_TOOL_USE, my_audit_hook)
```

**利用可能なフックイベント:**

| イベント | 発火タイミング | 主な用途 |
|---------|--------------|---------|
| `SESSION_START` | セッション開始時 | 初期化・認証チェック |
| `PRE_TOOL_USE` | ツール実行直前 | 監査ログ・危険操作のブロック |
| `POST_TOOL_USE` | ツール実行直後 | 結果の検証・変換 |
| `PRE_COMPACT` | コンテキスト圧縮前 | 重要情報の退避 |
| `POST_COMPACT` | コンテキスト圧縮後 | 圧縮後の整合性確認 |
| `STOP` | 停止試行時 | 未完了タスクの検出 |

### サーキットブレーカー

LLM APIの障害からシステム全体を保護する3状態ステートマシン。

```
正常時       障害検知時        回復確認中
[CLOSED] → [OPEN] → [HALF-OPEN] → [CLOSED]
全通過     即時拒否    一部通過       全通過
```

### 3層メモリ構造

```
Layer 1: セッション内（コンテキストウィンドウ内）      ← 現在の会話
Layer 2: ワーキングメモリ（セッション中永続）          ← 作業メモ
Layer 3: 長期メモリ（セッション間永続 / 外部DB）       ← 学習・知識
```

---

## 本番化に向けたチェックリスト

- [ ] `MemoryBackend` を DynamoDB / Redis / pgvector に差し替える
- [ ] `ObservabilityCollector` を AWS X-Ray / CloudWatch Logs に接続する
- [ ] `BashTool` の `allowed_commands` を最小限に絞る
- [ ] `SearchKnowledgeTool` を Amazon Bedrock Knowledge Base に接続する
- [ ] プロンプトインジェクション対策フックを `PRE_TOOL_USE` に追加する
- [ ] APIキーを AWS Secrets Manager で管理する
- [ ] `AgentConfig.model` を本番モデルIDに更新する

---

## 参考アーキテクチャ（AWS構成）

長時間セッション（8時間超）が必要な場合は **Amazon Bedrock AgentCore** へのデプロイを推奨。
短命なリクエスト処理には Lambda + API Gateway のサーバーレス構成が適切。

```
API Gateway → Lambda → AgentHarness → Anthropic API
                  ↓
            DynamoDB（メモリ永続化）
                  ↓
            CloudWatch Logs（可観測性）
```


---

📝 [Notionで詳細を見る]()
