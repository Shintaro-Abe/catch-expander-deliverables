# PoC品質: 本番環境での使用前に、セキュリティレビューおよびパラメータ見直しを必ず実施してください。

variable "aws_region" {
  description = "AWSリージョン（例: ap-northeast-1 = 東京）"
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "プロジェクト名。リソース名のプレフィックスとして使用されます"
  type        = string
  default     = "data-api-poc"
}

variable "environment" {
  description = "環境名（dev / staging / prod）"
  type        = string
  default     = "dev"
}

# ── Aurora Serverless v2 ──────────────────────────────────────────
variable "db_name" {
  description = "Auroraクラスターに作成するデータベース名"
  type        = string
  default     = "appdb"
}

variable "db_master_username" {
  description = "Auroraマスターユーザー名（シークレットはSecrets Managerで自動管理）"
  type        = string
  default     = "dbadmin"
}

variable "aurora_min_capacity" {
  description = "Aurora Serverless v2の最小ACU（0.5〜128）。小さいほど低コスト"
  type        = number
  default     = 0.5
}

variable "aurora_max_capacity" {
  description = "Aurora Serverless v2の最大ACU（スパイク対応上限）"
  type        = number
  default     = 16
}

# ── DynamoDB ─────────────────────────────────────────────────────
variable "dynamodb_billing_mode" {
  description = "DynamoDB課金モード: PAY_PER_REQUEST（オンデマンド）または PROVISIONED"
  type        = string
  default     = "PAY_PER_REQUEST"

  validation {
    condition     = contains(["PAY_PER_REQUEST", "PROVISIONED"], var.dynamodb_billing_mode)
    error_message = "billing_modeはPAY_PER_REQUESTまたはPROVISIONEDを指定してください。"
  }
}

# ── API Gateway ──────────────────────────────────────────────────
variable "api_gateway_cache_enabled" {
  description = "API Gatewayのレスポンスキャッシュを有効にするか（REST APIのみ、追加コスト発生）"
  type        = bool
  default     = false
}

variable "api_gateway_cache_size" {
  description = "キャッシュサイズ（GB）。0.5, 1.6, 6.1, 13.5, 28.4, 58.2, 118, 237 から選択"
  type        = string
  default     = "0.5"
}

variable "api_throttle_rate_limit" {
  description = "API Gatewayのステディステートスロットリング（RPS）"
  type        = number
  default     = 1000
}

variable "api_throttle_burst_limit" {
  description = "API Gatewayのバーストリミット（トークンバケット容量）"
  type        = number
  default     = 500
}

# ── Lambda ──────────────────────────────────────────────────────
variable "lambda_memory_mb" {
  description = "Lambda関数のメモリ割り当て（MB）。CPUもメモリに比例して増加"
  type        = number
  default     = 256
}

variable "lambda_timeout_sec" {
  description = "Lambda関数のタイムアウト（秒）"
  type        = number
  default     = 29
}

variable "lambda_provisioned_concurrency" {
  description = "Provisioned Concurrencyの数（0=無効、コールドスタート対策）"
  type        = number
  default     = 0
}

# ── AppSync ──────────────────────────────────────────────────────
variable "appsync_auth_type" {
  description = "AppSyncのデフォルト認証タイプ（API_KEY / AMAZON_COGNITO_USER_POOLS / AWS_IAM）"
  type        = string
  default     = "AMAZON_COGNITO_USER_POOLS"
}

# ── WAF ─────────────────────────────────────────────────────────
variable "waf_enabled" {
  description = "WAF（Web Application Firewall）をAPI Gatewayに関連付けるか"
  type        = bool
  default     = true
}

variable "waf_rate_limit" {
  description = "WAFレートベースルール: 5分間のIPごとのリクエスト上限"
  type        = number
  default     = 2000
}
