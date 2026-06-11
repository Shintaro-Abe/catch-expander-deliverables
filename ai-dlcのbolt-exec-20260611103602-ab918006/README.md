## プログラムコード（Python またはユーザープロファイルの技術スタック）

# Bolt × AI-DLC Python 実装サンプル集

> **PoC品質** — 本番利用前に認証・エラーハンドリング・セキュリティレビューを実施すること

---

## 概要

[Bolt.new](https://bolt.new)（StackBlitz製）は、ブラウザ内でフルスタックWebアプリを
自然言語プロンプトだけで生成・実行・デプロイできる AI 駆動開発プラットフォームです。
このリポジトリは Bolt の核心技術パターンを **Python で再実装**したサンプル集であり、
AI開発ライフサイクル（AI-DLC）における各フェーズへの統合方法を示しています。

---

## AI-DLC における Bolt の位置づけ

```
[ AI-DLC フェーズ ]

プロトタイピング ──► 開発 ──► テスト ──► デプロイ ──► 運用監視
      ↑                 ↑           ↑           ↑
  bolt_client.py   bolt_action_  bolt_llmops_  bolt_cicd_
  (LLM生成)        parser.py     pipeline.py   bridge.py
                  (コード解析)   (メトリクス)   (CI/CD連携)

★ Bolt は「プロトタイピング」に最適化されたツール
  本番 LLMOps は LangSmith / MLflow / W&B と役割分担する
```

---

## ファイル構成

| ファイル | 役割 | AI-DLC フェーズ |
|----------|------|-----------------|
| `bolt_client.py` | Claude API を使った Bolt スタイルコード生成クライアント | プロトタイピング |
| `bolt_action_parser.py` | `<boltAction>` タグパーサー + ローカル実行エンジン | プロトタイピング |
| `bolt_llmops_pipeline.py` | マルチステージ生成パイプライン + メトリクス収集 | 開発・テスト |
| `bolt_cicd_bridge.py` | GitHub プッシュ + GitHub Actions 自動設定 | デプロイ |

---

## セットアップ

```bash
# 依存パッケージインストール
pip install anthropic

# 環境変数設定
export ANTHROPIC_API_KEY="sk-ant-..."     # 必須
export GITHUB_TOKEN="ghp_..."             # bolt_cicd_bridge.py 使用時
export GITHUB_OWNER="your-org"            # bolt_cicd_bridge.py 使用時
export GITHUB_REPO="your-repo"            # bolt_cicd_bridge.py 使用時
```

---

## 各モジュールの使い方

### 1. `bolt_client.py` — シンプルなコード生成

```python
from bolt_client import BoltClient

client = BoltClient()

# 単発生成
code = client.generate("React + Tailwind でTODOアプリを作成してください")

# マルチターン（フォローアップ修正）
client.chat("TODOアプリを作成してください")
client.chat("優先度（高/中/低）の選択肢を追加してください")  # 差分更新
```

**Bolt のキャッシュ戦略**: システムプロンプトに `cache_control: ephemeral` を付与することで、
同一プロジェクト内の反復呼び出しでトークンコストを最大 **90% 削減**できます。

---

### 2. `bolt_action_parser.py` — タグ解析 + ローカル実行

```python
from bolt_action_parser import BoltActionParser, BoltActionExecutor

parser   = BoltActionParser()
executor = BoltActionExecutor(output_dir="./output")

# LLM 出力から boltAction タグを解析
artifacts = parser.parse(llm_output_text)

# ローカルファイルシステムに書き出す
for artifact in artifacts:
    result = executor.execute(artifact, dry_run=True)  # dry_run=True でシェル実行をスキップ
    print(result["files_written"])
```

**Bolt の「暗黙的ツールコーリング」**: LLM が `<boltAction>` タグを生成し、パーサーが実行する
方式により、JSON スキーマ定義なしでツール呼び出しを実現しています。

---

### 3. `bolt_llmops_pipeline.py` — LLMOps 統合

```python
from bolt_llmops_pipeline import BoltLLMOpsPipeline, PipelineStage

pipeline = BoltLLMOpsPipeline()

stages = [
    PipelineStage(name="requirements", prompt_template="要件定義: {app_description}"),
    PipelineStage(name="scaffold",     prompt_template="実装: {requirements_output}"),
    PipelineStage(name="tests",        prompt_template="テスト: {scaffold_output}"),
]

results = pipeline.run_pipeline(stages, {"app_description": "タスク管理SaaS"})
pipeline.export_metrics("metrics.json")  # LangSmith / MLflow にインポート可能
```

**メトリクス例**:
```json
{
  "total_runs": 3,
  "success_rate": "100.0%",
  "cache_savings_ratio": "42.3%",
  "total_estimated_cost_usd": 0.0234
}
```

---

### 4. `bolt_cicd_bridge.py` — CI/CD 自動設定

```python
from bolt_cicd_bridge import BoltCICDBridge, RepoFile

bridge = BoltCICDBridge()

files = [
    RepoFile(path="package.json", content='{"name": "app", ...}'),
    RepoFile(path="src/App.tsx",  content="export default function App() { ... }"),
]

# GitHub にプッシュ + .github/workflows/ + .bolt/ignore を自動生成
result = bridge.scaffold_repo(files, branch="develop")
# → GitHub Actions が自動起動: test → staging deploy
```

---

## Bolt の主要制約と対策

| 制約 | 影響 | 対策 |
|------|------|------|
| コンテキスト上限 | 15〜20コンポーネント超で「Project size exceeded」エラー | `.bolt/ignore` で不要ファイルを除外 |
| トークンコスト | 複雑なアプリで月間割当の10%+消費 | プロンプトキャッシュ + 段階的生成 |
| ローカル開発不可 | デバッグが困難 | bolt_action_parser.py でローカル再現 |
| GitHub Actions 非ネイティブ | CI/CD 手動設定が必要 | bolt_cicd_bridge.py で自動化 |
| 組織アカウント未対応 | 個人アカウントのみ | 個人リポジトリ → 組織 fork のフロー |

---

## 競合ツールとの使い分け

```
UI生成特化     → v0 (Vercel) — Figma対応、Tailwind品質最高
フルスタック生成 → Bolt.new / Lovable — プロトタイプ〜MVP
IDE統合        → Cursor / Claude Code — 既存コードベースの編集
コード補完     → GitHub Copilot — 既存フローへの軽量追加
```

---

## 参考

- [Bolt.new 公式](https://bolt.new)
- [bolt.diy (オープンソース版)](https://github.com/stackblitz-labs/bolt.diy)
- [WebContainers ドキュメント](https://webcontainers.io)
- [Anthropic プロンプトキャッシュ](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)


---

📝 [Notionで詳細を見る]()
