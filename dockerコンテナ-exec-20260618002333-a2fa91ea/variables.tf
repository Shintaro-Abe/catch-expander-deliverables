# PoC品質: 本番利用前にセキュリティレビューおよびコスト試算を実施すること

variable "aws_region" {
  description = "AWSリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "プロジェクト名（リソース名のプレフィックスに使用）"
  type        = string
  default     = "llmops-demo"
}

variable "environment" {
  description = "環境名（dev / stg / prod）"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "stg", "prod"], var.environment)
    error_message = "environment は dev / stg / prod のいずれかを指定してください。"
  }
}

# --- ネットワーク ---
variable "vpc_cidr" {
  description = "VPC の CIDR ブロック"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "パブリックサブネット CIDR（ALB 配置用）"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "プライベートサブネット CIDR（ECS タスク配置用）"
  type        = list(string)
  default     = ["10.0.11.0/24", "10.0.12.0/24"]
}

# --- ECR ---
variable "ecr_image_tag_mutability" {
  description = "ECR イメージタグのミュータビリティ（IMMUTABLE 推奨）"
  type        = string
  default     = "IMMUTABLE"
}

variable "ecr_scan_on_push" {
  description = "プッシュ時にイメージスキャンを実行するか"
  type        = bool
  default     = true
}

# --- ECS タスク定義 ---
variable "container_image_tag" {
  description = "デプロイする ECR イメージタグ（CI/CD から渡す）"
  type        = string
  default     = "latest"
}

variable "task_cpu" {
  description = "Fargate タスク CPU ユニット（256 / 512 / 1024 / 2048 / 4096）"
  type        = number
  default     = 512
}

variable "task_memory_mib" {
  description = "Fargate タスクメモリ MiB"
  type        = number
  default     = 1024
}

variable "container_port" {
  description = "コンテナが LISTEN するポート番号"
  type        = number
  default     = 8080
}

variable "container_user" {
  description = "コンテナ実行ユーザー（非 root を強制）"
  type        = string
  default     = "5000"
}

# --- ECS サービス ---
variable "desired_count" {
  description = "ECS サービスの希望タスク数"
  type        = number
  default     = 2
}

variable "min_capacity" {
  description = "オートスケーリング最小タスク数"
  type        = number
  default     = 1
}

variable "max_capacity" {
  description = "オートスケーリング最大タスク数"
  type        = number
  default     = 10
}

variable "cpu_scaling_target" {
  description = "CPU ベーススケーリングの目標使用率（%）"
  type        = number
  default     = 70
}

variable "enable_fargate_spot" {
  description = "Fargate Spot を混在させてコスト削減するか（中断許容ワークロード向け）"
  type        = bool
  default     = false
}
