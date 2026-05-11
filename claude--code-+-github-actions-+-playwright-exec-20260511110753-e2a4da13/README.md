## IaCコード（Terraform または CloudFormation）

# Claude Code + GitHub Actions + Playwright CI/CD 基盤

> **注意:** このコードは **PoC（概念実証）品質** です。本番環境へ適用する前に、セキュリティレビュー・テストを実施してください。

## 概要

このTerraformコードは、**Claude Code** による AI支援コードレビュー・テスト生成と、**Playwright** によるE2Eテストを **GitHub Actions** 上で運用するためのAWSインフラを構築します。

```
GitHub PR作成
    ↓
GitHub Actions起動
    ├─ Claude Code Action → AIによるPRレビューコメント投稿
    └─ Playwright テスト（並列シャーディング）
           ↓
      S3バケット（レポート保存）
           ↓
      CloudFront（HTMLレポートのHTTPS公開）
```

---

## 構成リソース一覧

| リソース | 用途 | 補足 |
|---|---|---|
| `aws_s3_bucket` | テストレポートの保存 | AES256暗号化・ライフサイクル自動削除 |
| `aws_cloudfront_distribution` | レポートのHTTPS公開 | OAC経由でS3に安全アクセス |
| `aws_iam_openid_connect_provider` | GitHub OIDC認証 | 長期キー不要のセキュアな認証 |
| `aws_iam_role` | GitHub Actions用IAMロール | 最小権限・ブランチ制限付き |

---

## 前提条件

- Terraform >= 1.6.0
- AWS CLIの設定済み（`aws configure` または環境変数）
- GitHubリポジトリの管理者権限

---

## セットアップ手順

### 1. 変数ファイルの作成

```hcl
# terraform.tfvars（Gitにコミットしない）
project_name = "my-app"
environment  = "dev"
github_org   = "my-organization"    # GitHubの組織名
github_repo  = "my-repository"     # リポジトリ名
github_allowed_branches = ["main", "develop"]
```

### 2. Terraformの実行

```bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

### 3. GitHub Actionsワークフローの設定

`terraform output github_actions_workflow_snippet` の出力を参考に、以下の **GitHub Secrets** を設定します。

| Secret名 | 値 | 取得方法 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude APIキー | Anthropic Console |
| *(OIDC使用のため不要)* | AWSアクセスキー | OIDCで代替 |

**ワークフロー変数（Variables）**:

| Variable名 | 値 |
|---|---|
| `AWS_ROLE_ARN` | `terraform output github_actions_role_arn` |
| `S3_BUCKET` | `terraform output report_bucket_name` |
| `CLOUDFRONT_DISTRIBUTION_ID` | `terraform output cloudfront_distribution_id` |

### 4. GitHub Actionsワークフロー例

```yaml
# .github/workflows/playwright-ci.yml
name: Playwright E2E Tests + Claude Review

on:
  pull_request:
    types: [opened, synchronize]
    paths:
      - 'src/**'
      - 'tests/**'

concurrency:
  group: ${{ github.workflow }}-${{ github.head_ref }}
  cancel-in-progress: true

permissions:
  contents: read
  pull-requests: write
  id-token: write  # OIDCトークン取得に必要

jobs:
  # ── ① AIコードレビュー（Claude Code Action）──────────────────────────────
  claude-review:
    runs-on: ubuntu-latest
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: |
            このPRをコード品質・正確性・セキュリティの観点でレビューし、
            具体的な改善提案をPRコメントとして投稿してください。
          claude_args: "--max-turns 5 --model claude-sonnet-4-6"

  # ── ② Playwright E2Eテスト（シャーディング並列実行）──────────────────────
  playwright-tests:
    timeout-minutes: 60
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        shardIndex: [1, 2, 3, 4]  # variables.tfのplaywright_shard_countと合わせる
        shardTotal: [4]
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: lts/*
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright browsers
        run: npx playwright install --with-deps

      - name: Run Playwright tests
        run: npx playwright test --shard=${{ matrix.shardIndex }}/${{ matrix.shardTotal }}

      - name: Upload blob report
        if: ${{ !cancelled() }}
        uses: actions/upload-artifact@v4
        with:
          name: blob-report-${{ matrix.shardIndex }}
          path: blob-report
          retention-days: 1

  # ── ③ レポート統合 → S3アップロード ─────────────────────────────────────
  merge-and-upload-reports:
    if: ${{ !cancelled() }}
    needs: [playwright-tests]
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: lts/*

      - name: Install dependencies
        run: npm ci

      - name: Download blob reports
        uses: actions/download-artifact@v4
        with:
          path: all-blob-reports
          pattern: blob-report-*
          merge-multiple: true

      - name: Merge into HTML Report
        run: npx playwright merge-reports --reporter html ./all-blob-reports

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.AWS_ROLE_ARN }}
          aws-region: ap-northeast-1

      - name: Upload report to S3
        run: |
          aws s3 sync playwright-report/ \
            s3://${{ vars.S3_BUCKET }}/reports/${{ github.run_id }}/

      - name: Invalidate CloudFront cache
        run: |
          aws cloudfront create-invalidation \
            --distribution-id ${{ vars.CLOUDFRONT_DISTRIBUTION_ID }} \
            --paths "/reports/${{ github.run_id }}/*"

      - name: Post report URL to PR
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          REPORT_URL="https://$(aws cloudfront list-distributions \
            --query "DistributionList.Items[?Id=='${{ vars.CLOUDFRONT_DISTRIBUTION_ID }}'].DomainName" \
            --output text)/reports/${{ github.run_id }}/index.html"
          gh pr comment ${{ github.event.pull_request.number }} \
            --body "## Playwright テストレポート
          [HTMLレポートを見る]($REPORT_URL)"
```

---

## メリット・デメリット

### メリット

| 観点 | 内容 |
|---|---|
| **セキュリティ** | OIDC認証により、長期的なAWSアクセスキーをGitHubに保存しない |
| **コスト最適化** | `concurrency` + `paths` フィルターで不要なCI実行を削減 |
| **並列性能** | 4シャード並列でテスト実行時間を最大75%短縮 |
| **可視性** | CloudFront経由でHTMLレポートをURLで共有可能 |
| **自動化** | Claude Codeによるレビューで初期コードレビューを自動化 |
| **コスト管理** | ライフサイクルポリシーで古いレポートを自動削除 |

### デメリット・注意事項

| 観点 | 内容 |
|---|---|
| **Claude API費用** | PRごとにAPI呼び出しが発生（`claude-sonnet-4-6`: $3/MTok入力）|
| **CloudFront遅延** | キャッシュ無効化に最大15分かかる場合がある |
| **複雑性** | シャーディング構成はシンプルな構成より設定・デバッグが複雑 |
| **ブランチ制限** | OIDC信頼ポリシーで許可するブランチを明示的に管理する必要がある |
| **ブラウザキャッシュ** | Playwright公式はブラウザバイナリのキャッシュを非推奨（復元コスト≒ダウンロードコスト）|
| **AI精度** | Claude Codeのレビューは補助ツール。人間レビューの代替にはならない |

---

## コスト目安

```
月額コスト概算（中規模プロジェクト・1日10PR想定）:

AWSインフラ:
  S3ストレージ       :  ~$1〜5/月
  CloudFrontリクエスト:  ~$1〜3/月
  合計               :  ~$2〜8/月

Claude API（claude-sonnet-4-6）:
  PRレビュー         :  ~$0.05〜0.30/PR
  月間（10PR×22日）  :  ~$11〜66/月

GitHub Actions:
  並列4ジョブ×10分  :  ~16 Actions分/PR
  月間              :  GitHub Freeプランの上限に注意
```

---

## セキュリティベストプラクティス

1. **APIキーはGitHub Secretsで管理** — コードにハードコードしない
2. **OIDC信頼条件を厳格に設定** — `github_allowed_branches` で許可ブランチを最小化
3. **最小権限IAMポリシー** — S3バケット・CloudFrontのみにスコープを限定
4. **`pull_request_target` は使用しない** — フォークPRからシークレットが漏洩するリスク
5. **ワークフロー権限を明示的に設定** — `permissions:` ブロックで不要な権限を排除

---

## ファイル構成

```
.
├── main.tf       # S3・CloudFrontリソース定義
├── iam.tf        # GitHub Actions OIDC・IAMロール定義
├── variables.tf  # 入力変数定義
├── outputs.tf    # 出力値定義（ワークフロー設定値など）
└── README.md     # このファイル
```


## プログラムコード（Python またはユーザープロファイルの技術スタック）

# Claude Code + GitHub Actions + Playwright 統合サンプル

> **PoC品質**: このサンプルコードは概念実証（Proof of Concept）です。本番環境での利用前にセキュリティレビューと十分な動作検証を行ってください。

## 概要

このリポジトリは、**Claude Code（AI エージェント）** と **GitHub Actions** と **Playwright（E2E テストフレームワーク）** を組み合わせた CI/CD パイプラインのサンプル実装です。

### 実現できること

| 機能 | 説明 |
|------|------|
| **並列テスト実行** | Playwright のシャーディングで複数ジョブに分割し高速化 |
| **AI 自動分析** | テスト失敗時に Claude が根本原因を分析し PR にコメント |
| **インタラクティブ対話** | `@claude` メンションでコードレビューや質問に応答 |
| **コスト最適化** | キャッシュ・paths フィルター・concurrency で無駄を削減 |

---

## ファイル構成

```
.
├── .github/workflows/
│   └── claude-playwright-ci.yml   # GitHub Actions ワークフロー（メイン）
├── playwright.config.ts           # Playwright 設定（CI 最適化済み）
├── tests/
│   └── example.spec.ts            # Playwright テストサンプル（Page Object Model）
├── scripts/
│   └── claude_test_analyzer.py    # Claude API でテスト失敗を分析する Python スクリプト
└── README.md
```

---

## セットアップ手順

### 1. 必要なシークレットの登録

GitHub リポジトリの **Settings → Secrets and variables → Actions** で登録：

| シークレット名 | 説明 |
|--------------|------|
| `ANTHROPIC_API_KEY` | Anthropic の API キー（[console.anthropic.com](https://console.anthropic.com) で取得） |

> **重要**: API キーを直接コードに書かないでください。Git 履歴に残り、公開後の削除は困難です。

### 2. GitHub App のインストール（推奨）

Claude Code ターミナルで以下を実行すると、GitHub App の設定が自動完了します：

```bash
/install-github-app
```

または手動で `https://github.com/apps/claude` からインストールしてください。

### 3. 依存パッケージのインストール

```bash
# Node.js 依存パッケージ（Playwright 等）
npm install

# Playwright ブラウザバイナリのインストール
npx playwright install --with-deps

# Python 依存パッケージ（分析スクリプト用）
pip install anthropic
```

---

## 使い方

### Playwright テストの実行

```bash
# ローカルで全テストを実行
npx playwright test

# 特定ブラウザのみ
npx playwright test --project=chromium

# シャーディング（4分割の1番目）をシミュレート
npx playwright test --shard=1/4

# 失敗したテストのレポートを確認
npx playwright show-report
```

### テスト失敗の手動分析

```bash
# テスト失敗後に分析スクリプトを実行
python scripts/claude_test_analyzer.py \
  --results-dir ./test-results \
  --pr-number 42 \
  --base-ref main
```

### @claude メンションで AI に質問

PR やコメントで `@claude` をメンションすると AI が応答します：

```
@claude このコードのセキュリティ上の問題点を教えてください
@claude テストが失敗している原因を調べてください
@claude このロジックをリファクタリングしてください
```

---

## アーキテクチャ図

```
PR 作成・更新
    │
    ▼
┌─────────────────────────────────────────────┐
│ GitHub Actions ワークフロー                    │
│                                             │
│  [Job 1] Playwright テスト（4シャード並列）      │
│    Shard 1/4 ──┐                            │
│    Shard 2/4 ──┤ blob レポートを artifact 保存 │
│    Shard 3/4 ──┤                            │
│    Shard 4/4 ──┘                            │
│         │                                   │
│         ▼                                   │
│  [Job 2] レポート統合（HTML 生成）             │
│                                             │
│  [Job 3] 失敗時のみ Claude 分析              │
│    - スクリーンショット収集                    │
│    - Claude API で根本原因分析               │
│    - PR にコメント投稿                        │
└─────────────────────────────────────────────┘
```

---

## コスト・パフォーマンスの考慮事項

### メリット

- **高速化**: 4シャード並列実行で理論上 4x の速度向上
- **早期発見**: PR 段階で自動的に問題を検出
- **コスト削減**: `concurrency` + `paths` フィルターで不要な CI 実行を抑制
- **プロンプトキャッシュ**: システムプロンプトをキャッシュして Claude API コストを最大 90% 削減

### デメリット・注意点

- **GitHub Actions 費用**: 並列ジョブはランナー時間も並列消費（4シャード = 4倍のコンピューティング時間）
- **Claude API 費用**: テスト失敗ごとにコストが発生（Sonnet 4.6: 入力 $3/MTok・出力 $15/MTok）
- **スキャン時間**: ブラウザバイナリのインストール・キャッシュ復元にも時間がかかる
- **セキュリティリスク**: `pull_request_target` でシークレットを使用しない（フォーク PR からのシークレット漏洩リスク）
- **誤自動修正**: AI による自動修正は権限バグを隠蔽する可能性があるため、人間によるレビューを必須とする運用を推奨

### スケール判断基準

| 規模 | 推奨設定 |
|------|---------|
| テスト数 50 未満 | シャーディングなし・Chromium のみ |
| テスト数 50〜200 | 2〜4 シャード・Chromium + Firefox |
| テスト数 200 超 | 4 シャード×3 ブラウザ = 12 並列ジョブ |

> **目安**: 単一ランナーで 10〜15 分以内に完了できない場合にシャーディングを導入

---

## 参考資料

- [Playwright 公式ドキュメント - CI 設定](https://playwright.dev/docs/ci)
- [claude-code-action GitHub リポジトリ](https://github.com/anthropics/claude-code-action)
- [GitHub Actions - Workflow syntax](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions)
- [Anthropic API ドキュメント - プロンプトキャッシュ](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)


---

📝 [Notionで詳細を見る]()
