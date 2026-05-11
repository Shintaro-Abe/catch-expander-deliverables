# PoC品質: このコードは概念実証用です。本番環境では追加のセキュリティ設定・テストが必要です。

# =============================================================================
# 変数定義ファイル
# Claude Code + GitHub Actions + Playwright テスト基盤
# =============================================================================

# --- プロジェクト共通設定 ---

variable "project_name" {
  description = "プロジェクト名（リソース名のプレフィックスに使用）"
  type        = string
  default     = "claude-playwright"
}

variable "environment" {
  description = "環境名（dev / staging / prod）"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment は dev / staging / prod のいずれかを指定してください。"
  }
}

variable "aws_region" {
  description = "AWSリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "tags" {
  description = "全リソースに付与する共通タグ"
  type        = map(string)
  default = {
    ManagedBy = "Terraform"
    Purpose   = "claude-playwright-ci"
  }
}

# --- GitHub OIDC設定 ---

variable "github_org" {
  description = "GitHubの組織名またはユーザー名（例: my-org）"
  type        = string
}

variable "github_repo" {
  description = "GitHubリポジトリ名（例: my-app）"
  type        = string
}

variable "github_allowed_branches" {
  description = "AWSへのアクセスを許可するブランチパターン（例: [\"main\", \"develop\"]）"
  type        = list(string)
  default     = ["main"]
}

# --- S3レポートバケット設定 ---

variable "report_retention_days" {
  description = "Playwrightテストレポートの保存期間（日）"
  type        = number
  default     = 30

  validation {
    condition     = var.report_retention_days >= 1 && var.report_retention_days <= 365
    error_message = "保存期間は 1〜365 日の範囲で指定してください。"
  }
}

variable "enable_cloudfront" {
  description = "CloudFront経由でレポートを公開するか（true: 公開 / false: S3直接アクセスのみ）"
  type        = bool
  default     = true
}

# --- Playwrightシャーディング設定（参考値） ---

variable "playwright_shard_count" {
  description = "並列実行するシャード数（GitHub Actionsのmatrix設定値として参照用）"
  type        = number
  default     = 4
}
