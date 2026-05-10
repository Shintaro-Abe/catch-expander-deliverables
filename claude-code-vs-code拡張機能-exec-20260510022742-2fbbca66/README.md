## プログラムコード（Python またはユーザープロファイルの技術スタック）

# Claude Code VS Code拡張機能 — Pythonサンプルコード集

> **⚠️ PoC品質**: このコードは概念実証(Proof of Concept)として提供されています。本番環境での使用には追加の実装が必要です。

## 概要

Claude Code VS Code拡張機能が提供する主要機能を、PythonからAnthropic APIを通じて実現するサンプルコード集です。

| ファイル | 内容 |
|---|---|
| `claude_client.py` | Anthropic APIクライアントラッパー（基本機能） |
| `vscode_settings_generator.py` | settings.json 設定ファイルジェネレーター |
| `claude_code_workflow.py` | 開発ワークフロー自動化（CLIツール） |
| `example_settings.json` | settings.json 設定テンプレート |

---

## セットアップ

### 1. 必要なパッケージのインストール

```bash
pip install anthropic
```

### 2. APIキーの設定

```bash
# 環境変数で設定（推奨）
export ANTHROPIC_API_KEY="sk-ant-..."

# または .env ファイルを作成（Gitにコミットしないこと）
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

> **セキュリティ注意**: APIキーは絶対にコードにハードコードしないでください。
> `.gitignore` に `.env` と `.claude/settings.local.json` を追加することを推奨します。

---

## 使い方

### claude_client.py — 基本チャット・コードレビュー

```python
from claude_client import ClaudeCodeClient

client = ClaudeCodeClient()

# 基本チャット
response = client.chat("Pythonのリスト内包表記を説明してください。")
print(response)

# ストリーミング（リアルタイム表示）
for chunk in client.stream_chat("Dockerとは何ですか？"):
    print(chunk, end="", flush=True)

# コードレビュー
code = """
def divide(a, b):
    return a / b
"""
review = client.review_code(code, language="python", focus="バグ・エラー処理")
print(review)

# 拡張思考（複雑な問題向け）
result = client.think_deeply(
    "マイクロサービスとモノリシックアーキテクチャの最適な選択基準は？",
    effort="high",  # "low" / "medium" / "high"
)
print(result["thinking"])   # 思考プロセス
print(result["response"])   # 最終回答
```

### vscode_settings_generator.py — 設定ファイル生成

```python
from vscode_settings_generator import SettingsGenerator

# メソッドチェーンで設定を組み立てる
generator = (
    SettingsGenerator()
    .set_model("claude-sonnet-4-6")
    .set_permission_mode("default")
    .apply_preset("python")
    .apply_preset("git")
    .apply_preset("secure")
    .add_hook("PostToolUse", "Edit", "ruff check $FILE --fix")
    .set_env("DISABLE_TELEMETRY", "1")
)

# 内容を確認
generator.show_summary()

# ファイルに書き出し
generator.save(".claude/settings.json")
```

### claude_code_workflow.py — CLIワークフロー

```bash
# ステージ済み変更をレビュー
python claude_code_workflow.py review --focus セキュリティ

# コミットメッセージを自動生成
git add .
python claude_code_workflow.py commit --convention conventional

# ファイルのテストを生成
python claude_code_workflow.py test --file src/utils.py --output tests/test_utils.py

# リファクタリング提案
python claude_code_workflow.py refactor --file src/legacy.py --goal "モダンPython化"

# ツール比較分析
python claude_code_workflow.py compare --topic "プライバシー・セキュリティ"
```

---

## VS Code拡張機能との対応

| このコードの機能 | VS Code拡張での操作 |
|---|---|
| `client.chat()` | Claudeパネルにメッセージを入力して送信 |
| `client.stream_chat()` | 同上（ストリーミング表示） |
| `client.review_code()` | `@ファイル` をメンションして「レビューして」 |
| `client.think_deeply()` | `/think` コマンドまたは拡張思考トグル |
| `workflow.review_staged_changes()` | コミット前に「この変更をレビューして」 |
| `workflow.generate_commit_message()` | 「コミットメッセージを書いて」 |
| `workflow.generate_tests_for_file()` | `@ファイル` をメンションして「テストを追加して」 |
| `SettingsGenerator.save()` | VS Code設定UIでのClaude Code設定 |

---

## 主要AIコーディングツール比較

| 観点 | Claude Code | GitHub Copilot | Cursor | Windsurf |
|---|---|---|---|---|
| **コンテキスト** | 最大1Mトークン | ワークスペースインデックス | プロジェクト全体 | Cascade技術 |
| **無料枠** | なし（Pro以上必要） | あり（月2,000補完） | あり（機能制限） | あり |
| **最低料金** | $20/月（Proプラン） | $10/月 | $20/月 | $20/月 |
| **オフライン** | 非対応 | 非対応 | 非対応 | Enterprise限定 |
| **IDE対応** | VS Code・JetBrains等 | 8+エディタ | Cursorのみ | 40+エディタ |
| **マルチモデル** | Anthropicのみ | Claude・GPT等複数 | Claude・GPT等 | 複数対応 |
| **エンタープライズ** | ZDR・HIPAA対応 | SOC 2準拠 | SOC 2準拠 | FedRAMP認定 |

### ツール選択ガイド

- **個人開発・コスト重視**: GitHub Copilot（無料枠あり）または Windsurf
- **大規模コードベース・自律エージェント**: Claude Code（1Mトークン）
- **マルチモデル・IDE体験**: Cursor
- **エンタープライズ・プライバシー最優先**: Tabnine（エアギャップ対応）

---

## セキュリティ上の注意点

1. **APIキー**: 環境変数または `.claude/settings.local.json` にのみ保存
2. **セッションログ**: `~/.claude/projects/` にプレーンテキストで保存される
3. **パーミッション**: `bypassPermissions` モードは本番環境で絶対に使用しない
4. **MCPサーバー**: 信頼できるプロバイダーのサーバーのみ使用すること
5. **`/feedback` コマンド**: 実行するとソースコードを含む会話履歴がAnthropicに送信される

---

## データプライバシー設定

```bash
# テレメトリを無効化
export DISABLE_TELEMETRY=1

# エラーレポートを無効化
export DISABLE_ERROR_REPORTING=1

# 全非必須通信を無効化
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
```

> **プランによるデータ取り扱いの違い**:
> - **Pro/Maxプラン**: デフォルトでモデル学習に使用（オプトアウト可）
> - **Team/Enterpriseプラン**: デフォルトでモデル学習に使用しない


---

📝 [Notionで詳細を見る]()
