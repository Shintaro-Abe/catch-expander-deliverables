# PoC品質: このコードは概念実証用です。本番環境では追加のセキュリティ設定・テストが必要です。

# =============================================================================
# メインリソース定義
# Claude Code + GitHub Actions + Playwright テスト基盤
#
# 構成概要:
#   - S3バケット          : Playwrightテストレポート（HTML / blob）の保存先
#   - CloudFront          : S3レポートをHTTPSで安全に公開
#   - ライフサイクルポリシー: 古いレポートを自動削除してコスト最適化
# =============================================================================

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(var.tags, {
      Project     = var.project_name
      Environment = var.environment
    })
  }
}

# CloudFrontはus-east-1リージョンのACM証明書が必要なため別プロバイダーを定義
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

# =============================================================================
# S3バケット: Playwrightテストレポート保存
# =============================================================================

resource "aws_s3_bucket" "playwright_reports" {
  # バケット名はグローバルで一意である必要があるため、ランダムサフィックスを付与
  bucket = "${local.name_prefix}-reports-${random_id.bucket_suffix.hex}"
}

# ランダムID（バケット名の衝突回避用）
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# バケットへのパブリックアクセスをブロック（CloudFront OAC経由のみ許可）
resource "aws_s3_bucket_public_access_block" "playwright_reports" {
  bucket = aws_s3_bucket.playwright_reports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# バージョニング設定（レポートの上書き時に以前のバージョンを保持したい場合に有効化）
resource "aws_s3_bucket_versioning" "playwright_reports" {
  bucket = aws_s3_bucket.playwright_reports.id

  versioning_configuration {
    status = "Disabled" # PoC環境ではコスト削減のため無効化
  }
}

# サーバーサイド暗号化（保存データの暗号化）
resource "aws_s3_bucket_server_side_encryption_configuration" "playwright_reports" {
  bucket = aws_s3_bucket.playwright_reports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# ライフサイクルポリシー（古いレポートを自動削除）
resource "aws_s3_bucket_lifecycle_configuration" "playwright_reports" {
  bucket = aws_s3_bucket.playwright_reports.id

  rule {
    id     = "delete-old-reports"
    status = "Enabled"

    filter {
      prefix = "reports/"
    }

    expiration {
      days = var.report_retention_days
    }
  }

  rule {
    id     = "delete-old-blob-reports"
    status = "Enabled"

    filter {
      prefix = "blob-reports/"
    }

    # blobレポートは統合後不要になるため短期間で削除
    expiration {
      days = 3
    }
  }
}

# =============================================================================
# CloudFront: S3レポートのHTTPS公開
# =============================================================================

# Origin Access Control（OAC）: CloudFrontからS3へのアクセスを制御
resource "aws_cloudfront_origin_access_control" "playwright_reports" {
  count = var.enable_cloudfront ? 1 : 0

  name                              = "${local.name_prefix}-oac"
  description                       = "OAC for Playwright test reports"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# CloudFrontディストリビューション
resource "aws_cloudfront_distribution" "playwright_reports" {
  count = var.enable_cloudfront ? 1 : 0

  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${local.name_prefix} Playwright Test Reports"
  default_root_object = "index.html"

  origin {
    domain_name              = aws_s3_bucket.playwright_reports.bucket_regional_domain_name
    origin_id                = "S3-${aws_s3_bucket.playwright_reports.id}"
    origin_access_control_id = aws_cloudfront_origin_access_control.playwright_reports[0].id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "S3-${aws_s3_bucket.playwright_reports.id}"
    viewer_protocol_policy = "redirect-to-https"

    # HTMLレポートはビルドごとに更新されるためキャッシュTTLを短めに設定
    min_ttl     = 0
    default_ttl = 300  # 5分
    max_ttl     = 3600 # 1時間

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    Name = "${local.name_prefix}-distribution"
  }
}

# S3バケットポリシー: CloudFront OACからの読み取りを許可
resource "aws_s3_bucket_policy" "playwright_reports" {
  bucket = aws_s3_bucket.playwright_reports.id
  policy = data.aws_iam_policy_document.s3_cloudfront_policy.json

  depends_on = [aws_s3_bucket_public_access_block.playwright_reports]
}

data "aws_iam_policy_document" "s3_cloudfront_policy" {
  # CloudFront OAC経由の読み取り許可
  dynamic "statement" {
    for_each = var.enable_cloudfront ? [1] : []
    content {
      sid    = "AllowCloudFrontOACRead"
      effect = "Allow"

      principals {
        type        = "Service"
        identifiers = ["cloudfront.amazonaws.com"]
      }

      actions   = ["s3:GetObject"]
      resources = ["${aws_s3_bucket.playwright_reports.arn}/*"]

      condition {
        test     = "StringEquals"
        variable = "AWS:SourceArn"
        values   = [aws_cloudfront_distribution.playwright_reports[0].arn]
      }
    }
  }

  # GitHub Actions（OIDC IAMロール）からのアップロード許可
  statement {
    sid    = "AllowGitHubActionsUpload"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.github_actions.arn]
    }

    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:ListBucket",
      "s3:DeleteObject",
    ]

    resources = [
      aws_s3_bucket.playwright_reports.arn,
      "${aws_s3_bucket.playwright_reports.arn}/*",
    ]
  }
}
