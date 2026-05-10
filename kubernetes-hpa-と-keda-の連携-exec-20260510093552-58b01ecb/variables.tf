# PoC品質: このコードは学習・検証目的のスケルトン実装です。本番利用前に十分なレビューと調整が必要です。

variable "aws_region" {
  description = "AWSリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "eks_cluster_name" {
  description = "対象EKSクラスター名"
  type        = string
}

variable "app_name" {
  description = "アプリケーション識別名（リソース名のプレフィックスに使用）"
  type        = string
  default     = "keda-demo"
}

variable "app_namespace" {
  description = "ワークロードをデプロイするKubernetes Namespace"
  type        = string
  default     = "demo"
}

variable "keda_namespace" {
  description = "KEDAをインストールするNamespace"
  type        = string
  default     = "keda"
}

variable "keda_chart_version" {
  description = "KEDA Helmチャートバージョン（https://github.com/kedacore/charts/releases で確認）"
  type        = string
  default     = "2.17.0"
}

variable "worker_image" {
  description = "ワーカーコンテナイメージ（例: 123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/my-worker:latest）"
  type        = string
  default     = "public.ecr.aws/amazonlinux/amazonlinux:2023"
}

# -------------------------------------------------------
# KEDA ScaledObject のスケーリング設定
# -------------------------------------------------------

variable "min_replica_count" {
  description = "最小レプリカ数。0 に設定するとゼロスケール（アイドル時 Pod ゼロ）が有効になる。レイテンシ要件が厳しいサービスは 1 以上を推奨"
  type        = number
  default     = 0
}

variable "max_replica_count" {
  description = "最大レプリカ数。HPAに渡される上限値"
  type        = number
  default     = 10
}

variable "sqs_queue_length_target" {
  description = "Pod 1台あたりの目標SQSメッセージ数。この値を超えるとスケールアウトが発生する"
  type        = number
  default     = 5
}

variable "polling_interval" {
  description = "KEDAがスケーラー（SQS等）をポーリングする間隔（秒）。短くするとKubernetes APIへの負荷が増加する"
  type        = number
  default     = 30
}

variable "cooldown_period" {
  description = "最後のトリガーが非アクティブになった後、0にスケールダウンするまでの待機時間（秒）。短すぎるとスケールスラッシングが発生する"
  type        = number
  default     = 300
}

# -------------------------------------------------------
# 共通タグ
# -------------------------------------------------------

variable "common_tags" {
  description = "全リソースに付与する共通タグ"
  type        = map(string)
  default = {
    Project     = "keda-hpa-demo"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}
