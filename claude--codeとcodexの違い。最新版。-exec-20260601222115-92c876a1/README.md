## プログラムコード（Python またはユーザープロファイルの技術スタック）

# Claude Code vs OpenAI Codex 比較デモ（2026年6月最新版）

> **PoC品質**: このリポジトリは概念実証（PoC）用のスケルトン実装です。本番利用前に認証・エラーハンドリング・セキュリティ設定を追加してください。

## 概要

AIエンジニア向けに、Claude CodeとOpenAI Codex CLIの主要な違いを**実装レベル**で体験できるサンプル集です。

---

## ファイル構成

| ファイル | 内容 |
|---|---|
| `claude_code_client.py` | Claude API（Anthropic SDK）を使ったコード生成・レビュークライアント。プロンプトキャッシュ対応 |
| `openai_codex_client.py` | OpenAI API（o4-mini/GPT-5.x）を使ったCodexスタイルクライアント |
| `benchmark_report.py` | 両ツールのベンチマークデータを表形式でターミナル出力するレポートツール |
| `cost_calculator.py` | トークン消費量からリアルタイムでコストを試算するCLIツール |

---

## 主要比較サマリー

### エージェント性能（SWE-bench Verified）

| モデル | スコア | 備考 |
|---|---|---|
| Claude Opus 4.7 | 87.6% | Claude Code フラッグシップ |
| Claude Sonnet 4.6 | 79.6% | Claude Code 推奨モデル |
| GPT-5.3-Codex | ~80.0% | Codex CLI デフォルト |
| GPT-5.2-Codex | 80.0% | — |

### コスト比較（API従量課金）

| モデル | 入力 $/MTok | 出力 $/MTok |
|---|---|---|
| Claude Sonnet 4.6 | $3.00 | $15.00 |
| Claude Opus 4.7 | $5.00 | $25.00 |
| o4-mini（Codex） | $1.10 | $4.40 |
| GPT-5.3-Codex | $1.75 | $14.00 |

> **ポイント**: サブスクリプション個人プランは両者とも $20/$100/$200 と同一価格帯。API単価はo4-miniがSonnet 4.6より約65%安いが、トークン消費量はClaude Codeが多い傾向あり。

### 強み比較

| 観点 | Claude Code | Codex CLI |
|---|---|---|
| SWE-bench総合 | ✅ やや優位 | ✅ 同等水準 |
| ターミナルデバッグ | — | ✅ 優位（Terminal-Bench 77% vs 65%） |
| UIナビゲーション | ✅ 優位（OSWorld 72.5%） | — |
| コンテキスト長 | ✅ 1Mトークン | 400K（GPT-5.4で1M） |
| トークン効率 | — | ✅ 約4倍効率的（Figmaタスク実測） |
| MCP対応 | ✅ 27フック、豊富なエコシステム | ✅ 対応 |
| IDE連携 | VS Code / JetBrains（Beta） | VS Code / JetBrains |
| GitHub Actions | ✅ `claude-code-action@v1` | ✅ `openai/codex-action@v1` |
| 日本語精度 | ✅ MMLU 96.8%（公式） | 未公開 |

---

## セットアップ

```bash
pip install anthropic openai rich typer
export ANTHROPIC_API_KEY="your-key-here"
export OPENAI_API_KEY="your-key-here"
```

## 実行例

```bash
# ベンチマークレポート表示
python benchmark_report.py

# コスト試算（Sonnet 4.6、入力5Mトークン・出力1Mトークン）
python cost_calculator.py --model sonnet-4-6 --input-tokens 5000000 --output-tokens 1000000

# Claude Codeスタイルでコード生成
python claude_code_client.py --task "FizzBuzzをPythonで実装してください"

# Codexスタイルでコード生成
python openai_codex_client.py --task "FizzBuzzをPythonで実装してください"
```


---

📝 [Notionで詳細を見る](https://www.notion.so/Claude-Code-Codex-37247b55202e8144882eda907ebe22b8)
