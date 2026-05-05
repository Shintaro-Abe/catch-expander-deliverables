# PoC品質: 本番環境での利用前に、セキュリティ・可用性・コスト設計の見直しを行ってください。

variable "aws_region" {
  description = "AWSリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "プロジェクト名（リソース命名に使用）"
  type        = string
  default     = "mlflow-poc"
}

variable "environment" {
  description = "環境名（dev / staging / prod）"
  type        = string
  default     = "dev"
}

# ── ネットワーク ──────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "VPC の CIDR ブロック"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "パブリックサブネットの CIDR リスト（AZ 数に合わせて調整）"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "プライベートサブネットの CIDR リスト"
  type        = list(string)
  default     = ["10.0.11.0/24", "10.0.12.0/24"]
}

# ── ECS / コンテナ ────────────────────────────────────────────────────────────

variable "mlflow_image" {
  description = "MLflow コンテナイメージ URI（例: ghcr.io/mlflow/mlflow:v2.16.0 または ECR URI）"
  type        = string
  default     = "ghcr.io/mlflow/mlflow:v2.16.0"
}

variable "mlflow_port" {
  description = "MLflow サーバーがリッスンするコンテナポート"
  type        = number
  default     = 5000
}

variable "task_cpu" {
  description = "Fargate タスクに割り当てる CPU ユニット（256 = 0.25 vCPU）"
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate タスクに割り当てるメモリ（MiB）"
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "ECS サービスの希望タスク数"
  type        = number
  default     = 1
}

# ── RDS (PostgreSQL) ──────────────────────────────────────────────────────────

variable "db_name" {
  description = "MLflow バックエンドストア用データベース名"
  type        = string
  default     = "mlflowdb"
}

variable "db_username" {
  description = "RDS マスターユーザー名"
  type        = string
  default     = "mlflowuser"
}

variable "db_instance_class" {
  description = "RDS インスタンスクラス"
  type        = string
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  description = "RDS 割り当てストレージ容量（GiB）"
  type        = number
  default     = 20
}

variable "db_engine_version" {
  description = "PostgreSQL エンジンバージョン"
  type        = string
  default     = "15.6"
}

# ── S3 アーティファクトストア ──────────────────────────────────────────────────

variable "artifact_bucket_force_destroy" {
  description = "terraform destroy 時にバケット内オブジェクトを強制削除するか（PoC用: true, 本番: false）"
  type        = bool
  default     = true
}

# ── ALB アクセス制限 ──────────────────────────────────────────────────────────

variable "allowed_cidr_blocks" {
  description = "MLflow UI / API へのアクセスを許可する CIDR リスト（本番では VPN/社内 CIDR に絞ること）"
  type        = list(string)
  default     = ["0.0.0.0/0"] # PoC用: 全公開。本番では必ず制限すること
}
