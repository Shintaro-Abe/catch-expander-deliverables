# PoC品質 — 本番環境への適用前に、値を適切にカスタマイズしてください
# 標準AWS2.0: 共通変数定義

# ====================================================
# 基本設定
# ====================================================
variable "aws_region" {
  description = "デプロイ対象のAWSリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "プロジェクト名（全リソース名のプレフィックスに使用）"
  type        = string
  default     = "aws-std2"
}

variable "environment" {
  description = "環境種別（dev / staging / prod）"
  type        = string
  default     = "dev"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment は dev / staging / prod のいずれかを指定してください。"
  }
}

variable "cost_center" {
  description = "コスト配分タグ用のコストセンターID（FinOps管理に使用）"
  type        = string
  default     = "engineering"
}

# ====================================================
# ネットワーク
# ====================================================
variable "vpc_cidr" {
  description = "VPC CIDRブロック"
  type        = string
  default     = "10.0.0.0/16"
  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "有効なCIDRブロックを指定してください（例: 10.0.0.0/16）。"
  }
}

variable "availability_zones" {
  description = "使用するAZ（ap-northeast-1 は a/c/d の3AZ推奨）"
  type        = list(string)
  default     = ["ap-northeast-1a", "ap-northeast-1c", "ap-northeast-1d"]
}

variable "public_subnet_cidrs" {
  description = "パブリックサブネットCIDR（ALB・NAT GW配置）"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
}

variable "private_subnet_cidrs" {
  description = "プライベートサブネットCIDR（アプリケーション・Lambda配置）"
  type        = list(string)
  default     = ["10.0.11.0/24", "10.0.12.0/24", "10.0.13.0/24"]
}

variable "intra_subnet_cidrs" {
  description = "イントラサブネットCIDR（DB・VPCエンドポイント専用、インターネット非接続）"
  type        = list(string)
  default     = ["10.0.21.0/24", "10.0.22.0/24", "10.0.23.0/24"]
}

# ====================================================
# Organizations / ガバナンス
# ====================================================
variable "root_ou_id" {
  description = "Organizations ルートOU ID（r-xxxx 形式）。未指定の場合はSCP/RCPアタッチをスキップ"
  type        = string
  default     = ""
  validation {
    condition     = var.root_ou_id == "" || can(regex("^r-[a-z0-9]{4,32}$", var.root_ou_id))
    error_message = "root_ou_id は r-xxxx 形式で指定するか、空文字列を指定してください。"
  }
}

variable "allowed_regions" {
  description = "SCP で使用を許可するAWSリージョン（us-east-1 はIAM等グローバルサービスに必要）"
  type        = list(string)
  default     = ["ap-northeast-1", "us-east-1"]
}

# ====================================================
# Bedrock RAG
# ====================================================
variable "bedrock_embedding_model_arn" {
  description = "Bedrock 埋め込みモデルARN（東京リージョン対応モデルを使用）"
  type        = string
  default     = "arn:aws:bedrock:ap-northeast-1::foundation-model/amazon.titan-embed-text-v2:0"
}

variable "bedrock_kb_name" {
  description = "Bedrock Knowledge Base の名前"
  type        = string
  default     = "aws-std2-knowledge-base"
}

variable "opensearch_collection_name" {
  description = "OpenSearch Serverless コレクション名（3〜32文字、英小文字・数字・ハイフン）"
  type        = string
  default     = "aws-std2-rag"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.opensearch_collection_name))
    error_message = "コレクション名は3〜32文字の英小文字・数字・ハイフンで指定し、英小文字で始めてください。"
  }
}
