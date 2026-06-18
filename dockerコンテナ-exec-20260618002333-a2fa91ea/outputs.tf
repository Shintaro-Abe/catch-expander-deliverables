# PoC品質: 本番利用前にセキュリティレビューおよびコスト試算を実施すること

output "alb_dns_name" {
  description = "ALB の DNS 名（アプリケーションへのアクセス URL）"
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  description = "ECR リポジトリ URL（docker push 時に使用）"
  value       = aws_ecr_repository.app.repository_url
}

output "ecr_repository_name" {
  description = "ECR リポジトリ名"
  value       = aws_ecr_repository.app.name
}

output "ecs_cluster_name" {
  description = "ECS クラスター名"
  value       = aws_ecs_cluster.main.name
}

output "ecs_cluster_arn" {
  description = "ECS クラスター ARN"
  value       = aws_ecs_cluster.main.arn
}

output "ecs_service_name" {
  description = "ECS サービス名"
  value       = aws_ecs_service.app.name
}

output "ecs_task_definition_arn" {
  description = "ECS タスク定義 ARN（最新リビジョン）"
  value       = aws_ecs_task_definition.app.arn
}

output "ecs_execution_role_arn" {
  description = "ECS タスク実行ロール ARN"
  value       = aws_iam_role.ecs_execution.arn
}

output "ecs_task_role_arn" {
  description = "ECS タスクロール ARN（アプリが使用）"
  value       = aws_iam_role.ecs_task.arn
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "プライベートサブネット ID 一覧（ECS タスク配置先）"
  value       = aws_subnet.private[*].id
}

output "cloudwatch_log_group_name" {
  description = "CloudWatch Logs グループ名（コンテナログ）"
  value       = aws_cloudwatch_log_group.ecs.name
}

output "docker_push_commands" {
  description = "ECR へのイメージプッシュコマンド例"
  value = <<-EOT
    # 1. ECR 認証（トークン有効期限: 12時間）
    aws ecr get-login-password --region ${var.aws_region} | \
      docker login --username AWS --password-stdin ${aws_ecr_repository.app.repository_url}

    # 2. イメージのビルド（ARM64 / Graviton 向け）
    docker buildx build --platform linux/arm64 -t ${aws_ecr_repository.app.repository_url}:v1.0.0 .

    # 3. イメージのプッシュ
    docker push ${aws_ecr_repository.app.repository_url}:v1.0.0
  EOT
}
