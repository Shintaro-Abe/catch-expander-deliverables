## プログラムコード（Python またはユーザープロファイルの技術スタック）

# Claude Code VS Code拡張機能 - Python実装デモ

> **PoC品質**: このコードは学習・動作確認用のスケルトンです。本番環境での使用には追加の設計・検証が必要です。

## 概要

このリポジトリは、**Claude Code VS Code拡張機能**が提供する主要機能をPythonスクリプトで再現したデモです。

Claude Code VS Code拡張（バージョン v2.1.138、2026年5月時点）は1,300万以上のインストールを記録し、SWE-bench Verified **80.8%**という業界最高水準のベンチマーク性能を持つAIコーディングアシスタントです。

---

## ファイル構成

| ファイル | 説明 |
|----------|------|
| `claude_code_assistant.py` | コードレビュー・テスト生成・リファクタリング支援の基本機能 |
| `mcp_server_demo.py` | MCP（Model Context Protocol）サーバーとエージェントループの実装 |
| `plan_mode_demo.py` | Plan Mode（変更前に計画を確認する安全なワークフロー）の実装 |
| `requirements.txt` | 依存パッケージ一覧 |

---

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. APIキーの設定

**Claude Pro/Max サブスクリプションをお持ちの場合:**

VS Code拡張では `/login` でサインインするだけで利用可能ですが、
このPythonスクリプトではAPIキーが必要です。
[Anthropic Console](https://console.anthropic.com) でキーを作成してください。

```bash
# macOS / Linux
export ANTHROPIC_API_KEY='sk-ant-...'

# Windows (PowerShell)
$env:ANTHROPIC_API_KEY='sk-ant-...'
```

> ⚠️ **注意**: VS Code拡張でClaude Proを使う場合、`ANTHROPIC_API_KEY`を**設定しない**のが推奨です。
> 環境変数が設定されていると、サブスクリプションより優先されて従量課金が発生します。
> `/status`コマンドで現在の認証方式を確認できます。

---

## 使い方

### コードレビュー・テスト生成・リファクタリング

```bash
python claude_code_assistant.py
```

**主な機能:**

```python
from claude_code_assistant import create_client, review_code, generate_tests

client = create_client()

# コードレビュー（セキュリティ・バグ・改善点を指摘）
result = review_code(client, your_code, language="python")

# テスト自動生成（pytest / unittest対応）
tests = generate_tests(client, your_code, framework="pytest")

# リファクタリング提案（変更計画 + 修正後コード）
refactored = suggest_refactoring(client, your_code)

# ストリーミング解説（リアルタイム出力）
stream_code_explanation(client, your_code, "このコードの問題点は？")
```

### MCPサーバー & エージェントループ

```bash
python mcp_server_demo.py
```

Claude Code VS Code拡張が使用する **MCP（Model Context Protocol）** の仕組みを学べます。

| MCPツール | 説明 |
|-----------|------|
| `mcp__ide__getDiagnostics` | VS Code Problemsパネルのエラー・警告を取得 |
| `mcp__ide__executeCode` | Jupyterカーネルでコードを実行 |
| `read_file` | ファイル内容をClaudeに渡す（@メンション相当） |

### Plan Mode（安全な変更確認ワークフロー）

```bash
python plan_mode_demo.py
```

Claude Code VS Code拡張の **Plan Mode**（`Shift+Tab`で切り替え）を体験できます。

```
実行フロー:
  1. タスクを入力
  2. Claudeが変更計画を作成（ファイルは変更しない）
  3. 計画をMarkdown形式で表示
  4. ユーザーが承認/拒否
  5. 承認後にファイルへの変更を実行
```

---

## プロンプトキャッシュについて

> **専門用語補足**: プロンプトキャッシュとは、同じシステムプロンプトを繰り返し使う場合に、
> 2回目以降はキャッシュから読み込むことでAPIコストを削減する機能です。

本デモでは `cache_control: {"type": "ephemeral"}` を使用しています：

```python
system=[{
    "type": "text",
    "text": SYSTEM_PROMPT,
    "cache_control": {"type": "ephemeral"},  # 5分間キャッシュ
}]
```

- **キャッシュヒット時**: 通常価格の **10%** のコスト
- **キャッシュ有効期限**: 5分間（`ephemeral`）または1時間（`persistent`）

---

## メリット・デメリット

### Claude Code VS Code拡張を使うメリット

| 観点 | 内容 |
|------|------|
| **性能** | SWE-bench 80.8%（業界最高水準） |
| **コンテキスト** | 最大1Mトークン（競合比最大4倍） |
| **エージェント機能** | 複数ファイルの自律的な変更・テスト実行が可能 |
| **安全性** | Plan Modeで変更前に計画を確認できる |
| **拡張性** | MCPサーバーでカスタムツールを統合できる |

### デメリット・注意点

| 観点 | 内容 |
|------|------|
| **コスト** | Pro $20/月〜、高負荷利用時はMax $100〜$200/月が必要 |
| **オートコンプリートなし** | GitHub Copilot・Cursorのようなインライン補完は非対応 |
| **データプライバシー** | 消費者プランはコードがモデル訓練に使われる可能性あり |
| **AI生成コードの品質** | 62%に設計上の欠陥や脆弱性が含まれるとの研究結果あり |
| **ハルシネーション** | 12〜18%のサイレントロジックエラーが発生する可能性あり |

---

## セキュリティに関する注意事項

- **シークレットの漏洩防止**: `.env`ファイルやAPIキーをコードに含めないこと
- **SQLインジェクション対策**: AI生成コードのSQLクエリは必ずパラメータ化クエリを使用
- **コードレビューの実施**: AI生成コードは人間によるセキュリティレビューを必ず実施
- **フィードバック機能の無効化**: `DISABLE_FEEDBACK_COMMAND=1`でコード送信を防止可能

---

## 料金目安（2026年5月時点）

| モデル | 入力 | 出力 |
|--------|------|------|
| Claude Haiku 4.5 | $1/MTok | $5/MTok |
| Claude Sonnet 4.6 | $3/MTok | $15/MTok |
| Claude Opus 4.7 | $5/MTok | $25/MTok |

> **MTok** = 100万トークン。日本語1文字 ≈ 1〜2トークン。

**コスト削減のヒント:**
- プロンプトキャッシュでキャッシュヒット時は通常価格の10%
- Batch API（非同期処理）で50%オフ
- 単純なタスクにはHaiku、複雑なタスクにはSonnet/Opusと使い分ける


---

📝 [Notionで詳細を見る]()
