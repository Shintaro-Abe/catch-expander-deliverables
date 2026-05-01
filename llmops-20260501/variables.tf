# PoC品質 - 本番環境での使用前に十分なセキュリティレビューが必要です
# このファイルはLLMOps Terraformモジュールの変数定義です

variable "aws_region" {
  description = "デプロイ先AWSリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "プロジェクト名（リソース名のプレフィックスに使用）"
  type        = string
  default     = "llmops"
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

# ── Bedrock ──────────────────────────────────────────────────────────────────
# Amazon Bedrock = AWSが提供する生成AI基盤モデルのマネージドサービス
# モデルIDはAWSコンソールの「Amazon Bedrock > モデルアクセス」で確認・有効化してください
variable "bedrock_model_id" {
  description = "Amazon Bedrockで使用する基盤モデルID"
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
}

# ── Lambda ───────────────────────────────────────────────────────────────────
variable "lambda_memory_size" {
  description = "Lambda関数のメモリサイズ（MB）。値を大きくするとCPU割り当ても増加します"
  type        = number
  default     = 512
}

variable "lambda_timeout" {
  description = "Lambda関数のタイムアウト（秒）。LLM推論は時間がかかるため余裕を持って設定します"
  type        = number
  default     = 30
}

# ── 監視・アラート ────────────────────────────────────────────────────────────
variable "alert_email" {
  description = "アラート通知先メールアドレス（空白の場合は通知なし）"
  type        = string
  default     = ""
  sensitive   = true
}

variable "log_retention_days" {
  description = "CloudWatch Logsの保持期間（日）。コスト管理のため適切に設定してください"
  type        = number
  default     = 30
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365], var.log_retention_days)
    error_message = "CloudWatch Logsの保持期間はAWS許容値（1/3/5/7/14/30/60/90/120/150/180/365）から選択してください。"
  }
}

# ── 品質評価 ──────────────────────────────────────────────────────────────────
# 品質スコア = LLM-as-Judgeが0〜1のスコアでレスポンス品質を評価した値
# 閾値を下回るとアラートが発生し、プロンプトドリフトの早期検知に活用します
variable "quality_score_threshold" {
  description = "低品質判定の閾値（0.0〜1.0）。この値を下回るとアラートを発報します"
  type        = number
  default     = 0.7
  validation {
    condition     = var.quality_score_threshold >= 0 && var.quality_score_threshold <= 1
    error_message = "quality_score_threshold は 0.0〜1.0 の範囲で指定してください。"
  }
}

variable "low_quality_alert_count" {
  description = "5分間の低品質レスポンス件数がこの値を超えたらアラートを発報します"
  type        = number
  default     = 10
}

# ── 共通タグ ──────────────────────────────────────────────────────────────────
variable "tags" {
  description = "全リソースに付与する追加タグ（コスト配賦・管理用途）"
  type        = map(string)
  default     = {}
}
