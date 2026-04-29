# PoC品質: 本コードは概念実証用スケルトンです。本番利用前に十分なレビューを行ってください。

variable "aws_region" {
  description = "プライマリリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "replication_region" {
  description = "CRRレプリケーション先リージョン"
  type        = string
  default     = "ap-northeast-3"
}

variable "project_name" {
  description = "プロジェクト名（リソース名プレフィックスに使用）"
  type        = string
  default     = "myproject"
}

variable "environment" {
  description = "環境名（dev / staging / prod）"
  type        = string
  default     = "dev"
}

variable "enable_object_lock" {
  description = "S3 Object Lock（WORM）を有効化するか（有効化後は無効化不可）"
  type        = bool
  default     = false
}

variable "object_lock_mode" {
  description = "Object Lockモード: GOVERNANCE または COMPLIANCE"
  type        = string
  default     = "GOVERNANCE"
  validation {
    condition     = contains(["GOVERNANCE", "COMPLIANCE"], var.object_lock_mode)
    error_message = "object_lock_mode は GOVERNANCE または COMPLIANCE を指定してください。"
  }
}

variable "object_lock_retention_days" {
  description = "Object Lock デフォルト保持日数"
  type        = number
  default     = 30
}

variable "enable_replication" {
  description = "クロスリージョンレプリケーション（CRR）を有効化するか"
  type        = bool
  default     = false
}

variable "enable_intelligent_tiering" {
  description = "S3 Intelligent-Tieringアーカイブ層を有効化するか"
  type        = bool
  default     = false
}

variable "lifecycle_log_transition_ia_days" {
  description = "logs/ プレフィックスを Standard-IA へ移行するまでの日数"
  type        = number
  default     = 30
}

variable "lifecycle_log_transition_glacier_days" {
  description = "logs/ プレフィックスを Glacier Flexible Retrieval へ移行するまでの日数"
  type        = number
  default     = 90
}

variable "lifecycle_log_expiration_days" {
  description = "logs/ プレフィックスのオブジェクト有効期限（日数）"
  type        = number
  default     = 365
}

variable "presigned_url_expiration_seconds" {
  description = "プレサインドURL有効期限（秒）"
  type        = number
  default     = 3600
}

variable "lambda_runtime" {
  description = "Lambda ランタイム"
  type        = string
  default     = "python3.12"
}

variable "tags" {
  description = "全リソースに付与する共通タグ"
  type        = map(string)
  default     = {}
}
