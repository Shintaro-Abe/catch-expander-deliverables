# PoC品質: 本番環境での利用前に、セキュリティ・可用性・コスト設計の見直しを行ってください。

# ─────────────────────────────────────────────────────────────────────────────
# MLflow アクセス情報
# ─────────────────────────────────────────────────────────────────────────────

output "mlflow_tracking_uri" {
  description = "MLflow トラッキングサーバーの URI（クライアント側で MLFLOW_TRACKING_URI に設定）"
  value       = "http://${aws_lb.mlflow.dns_name}"
}

output "mlflow_ui_url" {
  description = "MLflow Web UI の URL"
  value       = "http://${aws_lb.mlflow.dns_name}"
}

# ─────────────────────────────────────────────────────────────────────────────
# ネットワーク
# ─────────────────────────────────────────────────────────────────────────────

output "vpc_id" {
  description = "作成された VPC の ID"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "パブリックサブネットの ID リスト"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "プライベートサブネットの ID リスト"
  value       = aws_subnet.private[*].id
}

# ─────────────────────────────────────────────────────────────────────────────
# S3 アーティファクトストア
# ─────────────────────────────────────────────────────────────────────────────

output "artifact_bucket_name" {
  description = "MLflow アーティファクト用 S3 バケット名"
  value       = aws_s3_bucket.mlflow_artifacts.id
}

output "artifact_bucket_arn" {
  description = "MLflow アーティファクト用 S3 バケットの ARN"
  value       = aws_s3_bucket.mlflow_artifacts.arn
}

# ─────────────────────────────────────────────────────────────────────────────
# RDS
# ─────────────────────────────────────────────────────────────────────────────

output "rds_endpoint" {
  description = "RDS PostgreSQL エンドポイント（VPC 内からのみアクセス可能）"
  value       = aws_db_instance.mlflow.address
  sensitive   = true
}

output "db_secret_arn" {
  description = "DB パスワードが格納された Secrets Manager シークレットの ARN"
  value       = aws_secretsmanager_secret.db_password.arn
}

# ─────────────────────────────────────────────────────────────────────────────
# ECS
# ─────────────────────────────────────────────────────────────────────────────

output "ecs_cluster_name" {
  description = "ECS クラスター名"
  value       = aws_ecs_cluster.mlflow.name
}

output "ecs_service_name" {
  description = "ECS サービス名（CodePipeline 等での参照用）"
  value       = aws_ecs_service.mlflow.name
}

output "ecs_task_role_arn" {
  description = "ECS タスクロールの ARN（SageMaker 等から MLflow へアクセスする際の信頼ポリシーに追加）"
  value       = aws_iam_role.ecs_task.arn
}

# ─────────────────────────────────────────────────────────────────────────────
# クライアント側セットアップガイド
# ─────────────────────────────────────────────────────────────────────────────

output "client_setup_guide" {
  description = "Python クライアントからのアクセス設定例"
  value       = <<-EOT
    # ── 環境変数の設定 ──────────────────────────
    export MLFLOW_TRACKING_URI=http://${aws_lb.mlflow.dns_name}

    # ── Python コード例 ─────────────────────────
    import mlflow
    mlflow.set_tracking_uri("http://${aws_lb.mlflow.dns_name}")
    mlflow.set_experiment("my-experiment")

    with mlflow.start_run():
        mlflow.log_param("learning_rate", 0.01)
        mlflow.log_metric("accuracy", 0.95)
  EOT
}
