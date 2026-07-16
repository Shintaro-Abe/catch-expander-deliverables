# PoC品質 — 本番環境への適用前に、必ずセキュリティレビューと適切な設定変更を行ってください
# 標準AWS2.0: コアインフラストラクチャ（VPC・KMS・CloudTrail・VPCエンドポイント）

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # 本番環境ではS3リモートバックエンドを使用（以下のコメントを解除）
  # backend "s3" {
  #   bucket         = "YOUR-TFSTATE-BUCKET-ACCOUNTID"
  #   key            = "aws-standard-2/management/terraform.tfstate"
  #   region         = "ap-northeast-1"
  #   encrypt        = true
  #   dynamodb_table = "terraform-state-lock"
  # }
}

provider "aws" {
  region = var.aws_region

  # 全リソースに共通タグを自動付与（FinOps: コスト配分・リソース管理の基盤）
  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
      CostCenter  = var.cost_center
    }
  }
}

# ====================================================
# データソース: 現在のアカウント・環境情報
# ====================================================
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# AWS Organizations 情報（管理アカウントでのみ参照可能）
data "aws_organizations_organization" "current" {}

# ====================================================
# KMS カスタマーマネージドキー（CMK）
#
# AWSマネージドキーではなくCMKを使用する理由:
#   - キーポリシーで細粒度のアクセス制御が可能
#   - CloudTrailでキー利用状況を完全追跡できる
#   - Organizationsを超えたクロスアカウントアクセスを制御できる
# ====================================================
resource "aws_kms_key" "main" {
  description             = "${var.project_name}-${var.environment} master CMK"
  deletion_window_in_days = 30
  enable_key_rotation     = true  # 年次自動ローテーション（CIS Benchmark / NIST 要件）
  multi_region            = false

  policy = data.aws_iam_policy_document.kms_policy.json
}

resource "aws_kms_alias" "main" {
  name          = "alias/${var.project_name}-${var.environment}-main"
  target_key_id = aws_kms_key.main.key_id
}

data "aws_iam_policy_document" "kms_policy" {
  # ルートアカウントへの全権限（キー孤立を防ぐための最低限の設定）
  statement {
    sid       = "EnableRootAccess"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  # CloudWatch Logs サービスへの暗号化権限（フローログ・CloudTrailログ暗号化に必要）
  statement {
    sid    = "AllowCloudWatchLogs"
    effect = "Allow"
    actions = [
      "kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*",
      "kms:GenerateDataKey*", "kms:DescribeKey"
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

# ====================================================
# VPC: 3層サブネット分離によるゼロトラスト基盤
#
# パブリック層  : ALB・NAT GW のみ配置（最小限の露出）
# プライベート層: アプリケーション・Lambda（インターネット非直接接続）
# イントラ層   : DB・VPCエンドポイント（完全インターネット非接続）
#
# 本番環境では AZ ごとに NAT GW を配置して高可用性を確保
# ====================================================
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.16"

  name = "${var.project_name}-${var.environment}"
  cidr = var.vpc_cidr

  azs             = var.availability_zones
  public_subnets  = var.public_subnet_cidrs
  private_subnets = var.private_subnet_cidrs
  intra_subnets   = var.intra_subnet_cidrs

  # 本番: AZごとに NAT GW を配置（コスト増だが SPoF を排除）
  enable_nat_gateway     = true
  single_nat_gateway     = var.environment != "prod"
  one_nat_gateway_per_az = var.environment == "prod"

  # VPCエンドポイントのプライベートDNS解決に必要
  enable_dns_hostnames = true
  enable_dns_support   = true

  # VPCフローログ: 全トラフィックを記録してセキュリティ分析基盤を構成
  enable_flow_log                          = true
  create_flow_log_cloudwatch_log_group     = true
  create_flow_log_cloudwatch_iam_role      = true
  flow_log_max_aggregation_interval        = 60
  flow_log_cloudwatch_log_group_kms_key_id = aws_kms_key.main.arn
}

# ====================================================
# セキュリティグループ: VPC Interface Endpoints 共通
# ====================================================
resource "aws_security_group" "vpc_endpoints" {
  name_prefix = "${var.project_name}-vpc-ep-"
  description = "VPC Interface Endpoints 用（VPC内からのHTTPS通信のみ許可）"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "HTTPS from VPC CIDR"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  lifecycle { create_before_destroy = true }
}

# ====================================================
# VPCエンドポイント: AWSサービスへのプライベート通信
#
# メリット:
#   - データがインターネットを経由しない（セキュリティ）
#   - NAT GW経由のデータ転送コストを削減（FinOps）
# ====================================================

# Gateway型（無料）: S3へのプライベート通信
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids = concat(
    module.vpc.private_route_table_ids,
    module.vpc.intra_route_table_ids
  )
}

# Interface型: Bedrock RuntimeへのプライベートAPI呼び出し
resource "aws_vpc_endpoint" "bedrock_runtime" {
  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.intra_subnets
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
}

# Interface型: Bedrock Agentsへのプライベート通信（Knowledge Base呼び出しに使用）
resource "aws_vpc_endpoint" "bedrock_agent_runtime" {
  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.bedrock-agent-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.intra_subnets
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
}

# Interface型: Secrets Manager（認証情報のプライベート取得）
resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.intra_subnets
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
}

# ====================================================
# CloudTrail: 全API操作の証跡記録
#
# セキュリティ監査・インシデント対応・コンプライアンスに必須
# マルチリージョンかつグローバルサービスイベントを記録
# ====================================================
resource "aws_s3_bucket" "cloudtrail" {
  bucket        = "${var.project_name}-cloudtrail-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.environment != "prod"
}

resource "aws_s3_bucket_versioning" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    # Bucket Key: KMS API呼び出し数を削減しコストを最大99%削減
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket                  = aws_s3_bucket.cloudtrail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket     = aws_s3_bucket.cloudtrail.id
  policy     = data.aws_iam_policy_document.cloudtrail_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.cloudtrail]
}

data "aws_iam_policy_document" "cloudtrail_bucket" {
  statement {
    sid       = "CloudTrailAclCheck"
    effect    = "Allow"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.cloudtrail.arn]
    principals { type = "Service"; identifiers = ["cloudtrail.amazonaws.com"] }
  }

  statement {
    sid       = "CloudTrailWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.cloudtrail.arn}/AWSLogs/*"]
    principals { type = "Service"; identifiers = ["cloudtrail.amazonaws.com"] }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }

  # 非TLS通信を全拒否（転送中のデータ保護）
  statement {
    sid       = "DenyNonTLS"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.cloudtrail.arn, "${aws_s3_bucket.cloudtrail.arn}/*"]
    principals { type = "AWS"; identifiers = ["*"] }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_cloudtrail" "main" {
  name                          = "${var.project_name}-${var.environment}-trail"
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  include_global_service_events = true  # IAM・Route53・CloudFrontなどのグローバルAPI記録
  is_multi_region_trail         = true  # 全リージョンのAPIコールを単一トレイルで記録
  enable_log_file_validation    = true  # ログ改ざん検知（署名検証）
  kms_key_id                    = aws_kms_key.main.arn

  event_selector {
    read_write_type           = "All"
    include_management_events = true

    # S3データイベント（オブジェクトの読み取り・書き込み）を全バケット対象で記録
    data_resource {
      type   = "AWS::S3::Object"
      values = ["arn:aws:s3:::"]
    }
  }

  # 異常なAPIパターンを自動検知（大量エラー・突発的なAPI呼び出し増加）
  insight_selector { insight_type = "ApiCallRateInsight" }
  insight_selector { insight_type = "ApiErrorRateInsight" }

  depends_on = [aws_s3_bucket_policy.cloudtrail]
}

# ====================================================
# 出力: 他モジュールやスクリプトから参照する値
# ====================================================
output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "private_subnet_ids" {
  description = "プライベートサブネットID一覧"
  value       = module.vpc.private_subnets
}

output "intra_subnet_ids" {
  description = "イントラサブネットID一覧（DB・VPCエンドポイント配置）"
  value       = module.vpc.intra_subnets
}

output "kms_key_arn" {
  description = "メインKMS CMK ARN"
  value       = aws_kms_key.main.arn
}

output "kms_key_id" {
  description = "メインKMS CMK ID"
  value       = aws_kms_key.main.key_id
}

output "cloudtrail_bucket_name" {
  description = "CloudTrail ログ格納S3バケット名"
  value       = aws_s3_bucket.cloudtrail.id
}
