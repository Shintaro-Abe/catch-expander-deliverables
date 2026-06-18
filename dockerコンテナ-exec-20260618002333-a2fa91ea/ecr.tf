# PoC品質: 本番利用前にセキュリティレビューおよびコスト試算を実施すること

# ============================================================
# ECR リポジトリ
# ============================================================

resource "aws_ecr_repository" "app" {
  name                 = "${local.name_prefix}/app"
  image_tag_mutability = var.ecr_image_tag_mutability # IMMUTABLE 推奨（タグ上書き防止）

  image_scanning_configuration {
    scan_on_push = var.ecr_scan_on_push # プッシュ時に自動脆弱性スキャン
  }

  # KMS による暗号化（デフォルトは AES-256）
  encryption_configuration {
    encryption_type = "AES256"
    # 本番環境では KMS を指定してキー管理ポリシーを細かく制御することを推奨
    # encryption_type = "KMS"
    # kms_key         = aws_kms_key.ecr.arn
  }

  tags = { Name = "${local.name_prefix}-ecr-repo" }
}

# ============================================================
# ECR ライフサイクルポリシー（古いイメージを自動削除してコスト削減）
# ============================================================

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        # tagged イメージは最新 10 世代のみ保持
        rulePriority = 1
        description  = "最新10件の tagged イメージを保持"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v"]
          countType     = "imageCountMoreThan"
          countNumber   = 10
        }
        action = { type = "expire" }
      },
      {
        # untagged イメージは 7 日で削除
        rulePriority = 2
        description  = "untagged イメージを7日で削除"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      }
    ]
  })
}

# ============================================================
# ECR リポジトリポリシー（クロスアカウント Pull を制限）
# ============================================================

resource "aws_ecr_repository_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowECSPull"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.ecs_execution.arn
        }
        Action = [
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability"
        ]
      }
    ]
  })
}

# ============================================================
# ECR Enhanced Scanning（Amazon Inspector 連携）
# ============================================================
# ECR Enhanced Scanning は Inspector を有効化することで自動的に動作する。
# 以下は Inspector の有効化（アカウントレベル）。
# 注意: Inspector は追加コストが発生します。
# ============================================================

# resource "aws_inspector2_enabler" "ecr" {
#   account_ids    = [local.account_id]
#   resource_types = ["ECR"]
# }
