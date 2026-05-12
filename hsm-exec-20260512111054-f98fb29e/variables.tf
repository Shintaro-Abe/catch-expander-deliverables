# PoC品質: このコードは概念実証・学習用途のスケルトンです。本番環境への適用前に
# セキュリティレビュー・コンプライアンス要件の確認を必ず実施してください。

# -----------------------------------------------------------------------------
# 基本設定
# -----------------------------------------------------------------------------
variable "aws_region" {
  description = "デプロイ先 AWS リージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "name_prefix" {
  description = "全リソース名に付与するプレフィックス"
  type        = string
  default     = "poc-cloudhsm"
}

variable "common_tags" {
  description = "全リソースに付与する共通タグ"
  type        = map(string)
  default = {
    Environment = "poc"
    ManagedBy   = "terraform"
    Project     = "cloudhsm-demo"
  }
}

# -----------------------------------------------------------------------------
# ネットワーク設定
# -----------------------------------------------------------------------------
variable "vpc_id" {
  description = "CloudHSM を配置する VPC の ID"
  type        = string
  # 例: "vpc-0abc1234567890def"
}

variable "hsm_subnet_ids" {
  description = <<-EOT
    HSM ENI を配置するプライベートサブネットのリスト（各要素が異なる AZ であること）。
    HA 構成: 最低2つの AZ のサブネットを指定。
    ミッションクリティカル: 3つ以上を推奨。
    重要: クラスター作成後にサブネットを追加することはできない。
  EOT
  type        = list(string)
  validation {
    condition     = length(var.hsm_subnet_ids) >= 2
    error_message = "HA 構成のため、最低2つのサブネット（異なる AZ）を指定してください。"
  }
}

# -----------------------------------------------------------------------------
# CloudHSM 設定
# -----------------------------------------------------------------------------
variable "hsm_type" {
  description = <<-EOT
    HSM インスタンスタイプ。
    hsm2m.medium: FIPS 140-3 Level 3 認証済み（推奨）
    hsm1.medium:  FIPS 140-2 Level 3 認証済み（非推奨・新規作成不可）
  EOT
  type    = string
  default = "hsm2m.medium"
  validation {
    condition     = contains(["hsm2m.medium", "hsm1.medium"], var.hsm_type)
    error_message = "hsm_type は 'hsm2m.medium' または 'hsm1.medium' を指定してください。"
  }
}

variable "enable_third_hsm" {
  description = "3台目の HSM を AZ-3 に配置するか（ミッションクリティカル構成用）。true の場合は hsm_subnet_ids に3つ以上必要。"
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "CloudHSM バックアップ保持日数（7〜379日）。デフォルト: 90日。"
  type        = number
  default     = 90
  validation {
    condition     = var.backup_retention_days >= 7 && var.backup_retention_days <= 379
    error_message = "backup_retention_days は 7〜379 の範囲で指定してください。"
  }
}

# -----------------------------------------------------------------------------
# KMS Custom Key Store（オプション）
# KMS API 互換 + CloudHSM シングルテナント制御のハイブリッド構成
# 制約: 対称鍵のみ / 自動ローテーション不可 / インポートキー不可
# -----------------------------------------------------------------------------
variable "enable_kms_custom_key_store" {
  description = "KMS Custom Key Store (CloudHSM バックエンド) を作成するか"
  type        = bool
  default     = false
}

variable "kms_key_store_password" {
  description = <<-EOT
    KMS が CloudHSM クラスター内に作成する kmsuser CU のパスワード。
    クラスター初期化・アクティベーション後に設定する。
    シークレットは Secrets Manager 等で管理し、tfvars にハードコードしないこと。
  EOT
  type      = string
  default   = ""
  sensitive = true
}

variable "trust_anchor_certificate" {
  description = <<-EOT
    CloudHSM クラスター初期化時に使用したルート CA 証明書 (PEM 形式)。
    aws cloudhsmv2 describe-clusters で取得した HSM 証明書の発行者証明書。
  EOT
  type    = string
  default = ""
}

# -----------------------------------------------------------------------------
# EC2 クライアント設定（オプション）
# -----------------------------------------------------------------------------
variable "create_sample_client" {
  description = "HSM 接続検証用のサンプル EC2 クライアントインスタンスを作成するか"
  type        = bool
  default     = false
}

variable "client_ami_id" {
  description = "クライアント EC2 の AMI ID (Amazon Linux 2023 等を推奨)"
  type        = string
  default     = ""
}

variable "client_instance_type" {
  description = <<-EOT
    クライアント EC2 インスタンスタイプ。
    t3.small 以上を推奨。t3.nano / t3.micro は CloudHSM クライアント用途に不向き（リソース不足）。
  EOT
  type    = string
  default = "t3.small"
}

variable "admin_cidr_blocks" {
  description = "管理用 SSH アクセスを許可する CIDR ブロックのリスト（0.0.0.0/0 は禁止）"
  type        = list(string)
  default     = []
  validation {
    condition = alltrue([
      for cidr in var.admin_cidr_blocks : cidr != "0.0.0.0/0" && cidr != "::/0"
    ])
    error_message = "admin_cidr_blocks に 0.0.0.0/0 または ::/0 を設定しないでください。特定 IP のみ許可してください。"
  }
}
