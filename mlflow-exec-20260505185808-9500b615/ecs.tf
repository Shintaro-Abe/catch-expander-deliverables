# PoC品質: 本番環境での利用前に、セキュリティ・可用性・コスト設計の見直しを行ってください。
#
# このファイルで定義するリソース:
#   - Application Load Balancer (ALB) + ターゲットグループ
#   - ECS クラスター + タスク定義 + Fargate サービス
#   - CloudWatch Logs グループ
#   - セキュリティグループ (ALB 用 / ECS タスク用)

# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch Logs
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "mlflow" {
  name              = "/ecs/${var.project_name}"
  retention_in_days = 30
}

# ─────────────────────────────────────────────────────────────────────────────
# セキュリティグループ
# ─────────────────────────────────────────────────────────────────────────────

# ALB: 外部から HTTP (80) を受け付ける
# 本番では HTTPS (443) のみに絞り、ACM 証明書を設定すること
resource "aws_security_group" "alb" {
  name        = "${var.project_name}-alb-sg"
  description = "MLflow ALB: 外部 HTTP アクセスを受け付ける"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from allowed CIDRs"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-alb-sg" }
}

# ECS タスク: ALB からのトラフィックのみ受け付ける
resource "aws_security_group" "ecs_tasks" {
  name        = "${var.project_name}-ecs-tasks-sg"
  description = "MLflow ECS Tasks: ALB からのインバウンドのみ許可"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "MLflow port from ALB"
    from_port       = var.mlflow_port
    to_port         = var.mlflow_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-ecs-tasks-sg" }
}

# ─────────────────────────────────────────────────────────────────────────────
# Application Load Balancer
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lb" "mlflow" {
  name               = "${var.project_name}-alb"
  internal           = false # 本番では internal = true + PrivateLink 経由の公開を検討
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = false # PoC用: 本番では true に変更

  tags = { Name = "${var.project_name}-alb" }
}

resource "aws_lb_target_group" "mlflow" {
  name        = "${var.project_name}-tg"
  port        = var.mlflow_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip" # Fargate は "ip" を指定

  health_check {
    enabled             = true
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = { Name = "${var.project_name}-tg" }
}

resource "aws_lb_listener" "mlflow_http" {
  load_balancer_arn = aws_lb.mlflow.arn
  port              = 80
  protocol          = "HTTP"

  # 本番では protocol = "HTTPS" + certificate_arn を設定し、
  # HTTP → HTTPS リダイレクトリスナーを追加すること
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.mlflow.arn
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# ECS クラスター
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "mlflow" {
  name = "${var.project_name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled" # CloudWatch Container Insights を有効化
  }

  tags = { Name = "${var.project_name}-cluster" }
}

resource "aws_ecs_cluster_capacity_providers" "mlflow" {
  cluster_name       = aws_ecs_cluster.mlflow.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    base              = 1
    weight            = 100
    capacity_provider = "FARGATE"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# ECS タスク定義
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "mlflow" {
  family                   = "${var.project_name}-task"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "mlflow"
      image     = var.mlflow_image
      essential = true

      portMappings = [
        {
          containerPort = var.mlflow_port
          hostPort      = var.mlflow_port
          protocol      = "tcp"
        }
      ]

      # MLflow サーバー起動コマンド
      # --serve-artifacts: サーバーがアーティファクトのプロキシとして機能
      #   → クライアントに直接 AWS 認証情報が不要になる
      command = [
        "mlflow", "server",
        "--host", "0.0.0.0",
        "--port", tostring(var.mlflow_port),
        "--backend-store-uri",
        "postgresql://${var.db_username}:$(DB_PASSWORD)@${aws_db_instance.mlflow.address}:5432/${var.db_name}",
        "--artifacts-destination",
        "s3://${aws_s3_bucket.mlflow_artifacts.id}/artifacts",
        "--serve-artifacts"
      ]

      environment = [
        {
          name  = "AWS_DEFAULT_REGION"
          value = var.aws_region
        },
        # DB パスワードは Secrets Manager から注入（下記 secrets セクション参照）
      ]

      # DB パスワードを Secrets Manager から環境変数として安全に注入
      secrets = [
        {
          name      = "DB_PASSWORD"
          valueFrom = "${aws_secretsmanager_secret.db_password.arn}:password::"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.mlflow.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "mlflow"
        }
      }

      # ヘルスチェック: MLflow /health エンドポイントを確認
      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:${var.mlflow_port}/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60 # DB マイグレーション完了まで待機
      }
    }
  ])

  tags = { Name = "${var.project_name}-task-def" }
}

# ─────────────────────────────────────────────────────────────────────────────
# ECS サービス (Fargate)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecs_service" "mlflow" {
  name            = "${var.project_name}-service"
  cluster         = aws_ecs_cluster.mlflow.id
  task_definition = aws_ecs_task_definition.mlflow.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # デプロイ設定: ローリングアップデートでダウンタイムを最小化
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false # プライベートサブネット内で動作
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.mlflow.arn
    container_name   = "mlflow"
    container_port   = var.mlflow_port
  }

  # タスク定義の変更時に自動更新しない（CI/CD パイプライン経由でデプロイする場合）
  lifecycle {
    ignore_changes = [task_definition]
  }

  depends_on = [
    aws_lb_listener.mlflow_http,
    aws_iam_role_policy_attachment.ecs_execution_managed,
    aws_db_instance.mlflow
  ]

  tags = { Name = "${var.project_name}-service" }
}

# ─────────────────────────────────────────────────────────────────────────────
# Auto Scaling (オプション: 大規模チーム向け)
# ─────────────────────────────────────────────────────────────────────────────
# 以下はコメントアウト。チーム規模に応じて有効化してください。
#
# resource "aws_appautoscaling_target" "mlflow" {
#   service_namespace  = "ecs"
#   scalable_dimension = "ecs:service:DesiredCount"
#   resource_id        = "service/${aws_ecs_cluster.mlflow.name}/${aws_ecs_service.mlflow.name}"
#   min_capacity       = 1
#   max_capacity       = 4
# }
#
# resource "aws_appautoscaling_policy" "mlflow_cpu" {
#   name               = "${var.project_name}-cpu-scaling"
#   service_namespace  = aws_appautoscaling_target.mlflow.service_namespace
#   scalable_dimension = aws_appautoscaling_target.mlflow.scalable_dimension
#   resource_id        = aws_appautoscaling_target.mlflow.resource_id
#   policy_type        = "TargetTrackingScaling"
#   target_tracking_scaling_policy_configuration {
#     predefined_metric_specification {
#       predefined_metric_type = "ECSServiceAverageCPUUtilization"
#     }
#     target_value = 70.0
#   }
# }
