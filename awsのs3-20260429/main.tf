# PoC品質: 本コードは概念実証用スケルトンです。本番利用前に十分なレビューを行ってください。

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# CRR用サブプロバイダー
provider "aws" {
  alias  = "replica"
  region = var.replication_region
}

locals {
  name_prefix = "${var.project_name}-${var.environment}"
  common_tags = merge(var.tags, {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  })
}

# ─────────────────────────────────────────────
# KMS キー（SSE-KMS 暗号化用）
# ─────────────────────────────────────────────
resource "aws_kms_key" "s3" {
  description             = "S3 SSE-KMS key for ${local.name_prefix}"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = local.common_tags
}

resource "aws_kms_alias" "s3" {
  name          = "alias/${local.name_prefix}-s3"
  target_key_id = aws_kms_key.s3.key_id
}

# ─────────────────────────────────────────────
# メインバケット
# ─────────────────────────────────────────────
resource "aws_s3_bucket" "main" {
  bucket        = "${local.name_prefix}-main"
  # Object Lock 有効化はバケット作成時のみ設定可能
  object_lock_enabled = var.enable_object_lock
  tags          = local.common_tags
}

# バージョニング（Object Lock 利用時は必須）
resource "aws_s3_bucket_versioning" "main" {
  bucket = aws_s3_bucket.main.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-KMS 暗号化（S3 Bucket Keys でKMSコストを削減）
resource "aws_s3_bucket_server_side_encryption_configuration" "main" {
  bucket = aws_s3_bucket.main.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    }
    bucket_key_enabled = true
  }
}

# Block Public Access（全4設定を有効化 — Security Hub S3.8準拠）
resource "aws_s3_bucket_public_access_block" "main" {
  bucket                  = aws_s3_bucket.main.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

# Object Ownership（ACL廃止・BucketOwnerEnforced）
resource "aws_s3_bucket_ownership_controls" "main" {
  bucket = aws_s3_bucket.main.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# EventBridge への全イベント送信を有効化
resource "aws_s3_bucket_notification" "main" {
  bucket      = aws_s3_bucket.main.id
  eventbridge = true
  # Lambda 直接通知は serverless.tf 側で addEventNotification 相当の設定を行う
}

# ─────────────────────────────────────────────
# ライフサイクルポリシー
# ─────────────────────────────────────────────
resource "aws_s3_bucket_lifecycle_configuration" "main" {
  bucket = aws_s3_bucket.main.id
  # バージョニングが有効になってから設定
  depends_on = [aws_s3_bucket_versioning.main]

  # ログデータの多段階移行 + 有効期限
  rule {
    id     = "logs-tiering"
    status = "Enabled"
    filter {
      and {
        prefix = "logs/"
        # 128KB未満は移行コスト > 削減効果のため対象外
        object_size_greater_than = 131072
      }
    }
    transition {
      days          = var.lifecycle_log_transition_ia_days
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = var.lifecycle_log_transition_glacier_days
      storage_class = "GLACIER"
    }
    expiration {
      days = var.lifecycle_log_expiration_days
    }
    # 非現行バージョン管理（最大10件保持、30日後に削除）
    noncurrent_version_expiration {
      newer_noncurrent_versions = 10
      noncurrent_days           = 30
    }
    # 期限切れ削除マーカーの自動クリーンアップ
    expiration {
      expired_object_delete_marker = true
    }
  }

  # アーカイブデータ（Glacier Deep Archive）
  rule {
    id     = "archive-deep"
    status = "Enabled"
    filter {
      prefix = "archive/"
    }
    transition {
      days          = 1
      storage_class = "DEEP_ARCHIVE"
    }
    noncurrent_version_expiration {
      noncurrent_days = 180
    }
  }

  # 不完全マルチパートアップロードの自動中断（コスト漏れ防止）
  rule {
    id     = "abort-incomplete-mpu"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Intelligent-Tiering アーカイブ層（オプション）
  dynamic "rule" {
    for_each = var.enable_intelligent_tiering ? [1] : []
    content {
      id     = "intelligent-tiering-uploads"
      status = "Enabled"
      filter {
        prefix = "uploads/"
      }
      transition {
        days          = 0
        storage_class = "INTELLIGENT_TIERING"
      }
    }
  }
}

# Intelligent-Tiering アーカイブ層設定（オプション）
resource "aws_s3_bucket_intelligent_tiering_configuration" "main" {
  count  = var.enable_intelligent_tiering ? 1 : 0
  bucket = aws_s3_bucket.main.id
  name   = "archive-tiers"
  tiering {
    access_tier = "ARCHIVE_ACCESS"
    days        = 90
  }
  tiering {
    access_tier = "DEEP_ARCHIVE_ACCESS"
    days        = 180
  }
}

# Object Lock デフォルト保持設定（enable_object_lock=true の場合）
resource "aws_s3_bucket_object_lock_configuration" "main" {
  count  = var.enable_object_lock ? 1 : 0
  bucket = aws_s3_bucket.main.id
  rule {
    default_retention {
      mode = var.object_lock_mode
      days = var.object_lock_retention_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.main]
}

# HTTPS強制バケットポリシー（+ 署名鮮度制限の例）
resource "aws_s3_bucket_policy" "main" {
  bucket = aws_s3_bucket.main.id
  # Block Public Access が有効になってから設定
  depends_on = [aws_s3_bucket_public_access_block.main]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # HTTP通信を拒否
      {
        Sid       = "DenyHTTP"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.main.arn,
          "${aws_s3_bucket.main.arn}/*"
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
      # 10分以上前の署名を拒否（プレサインドURL鮮度制限）
      {
        Sid       = "DenyStaleSignedUrls"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.main.arn}/*"
        Condition = {
          NumericGreaterThan = { "s3:signatureAge" = "600000" }
        }
      },
      # Lambda実行ロールからの読み書きを許可
      {
        Sid    = "AllowLambdaAccess"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.lambda_s3.arn
        }
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = "${aws_s3_bucket.main.arn}/*"
      }
    ]
  })
}

# ─────────────────────────────────────────────
# CRR: クロスリージョンレプリケーション（オプション）
# ─────────────────────────────────────────────
resource "aws_s3_bucket" "replica" {
  count    = var.enable_replication ? 1 : 0
  provider = aws.replica
  bucket   = "${local.name_prefix}-replica"
  tags     = local.common_tags
}

resource "aws_s3_bucket_versioning" "replica" {
  count    = var.enable_replication ? 1 : 0
  provider = aws.replica
  bucket   = aws_s3_bucket.replica[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_iam_role" "replication" {
  count = var.enable_replication ? 1 : 0
  name  = "${local.name_prefix}-s3-replication"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "replication" {
  count = var.enable_replication ? 1 : 0
  name  = "s3-replication-policy"
  role  = aws_iam_role.replication[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetReplicationConfiguration",
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.main.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObjectVersionForReplication",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging"
        ]
        Resource = "${aws_s3_bucket.main.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ReplicateObject",
          "s3:ReplicateDelete",
          "s3:ReplicateTags"
        ]
        Resource = "${aws_s3_bucket.replica[0].arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = aws_kms_key.s3.arn
      }
    ]
  })
}

resource "aws_s3_bucket_replication_configuration" "main" {
  count  = var.enable_replication ? 1 : 0
  bucket = aws_s3_bucket.main.id
  role   = aws_iam_role.replication[0].arn
  depends_on = [
    aws_s3_bucket_versioning.main,
    aws_s3_bucket_versioning.replica
  ]
  rule {
    id     = "replicate-all"
    status = "Enabled"
    filter {}
    delete_marker_replication {
      status = "Enabled"
    }
    destination {
      bucket        = aws_s3_bucket.replica[0].arn
      storage_class = "STANDARD_IA"
    }
  }
}

# ─────────────────────────────────────────────
# IAM ロール（Lambda共通）
# ─────────────────────────────────────────────
resource "aws_iam_role" "lambda_s3" {
  name = "${local.name_prefix}-lambda-s3"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_s3.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_s3_access" {
  name = "s3-access"
  role = aws_iam_role.lambda_s3.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GeneratePresignedUrl"
        ]
        Resource = "${aws_s3_bucket.main.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.s3.arn
      }
    ]
  })
}
