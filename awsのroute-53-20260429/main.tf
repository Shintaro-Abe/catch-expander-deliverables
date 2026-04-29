# PoC品質: このコードは概念実証・学習目的のスケルトンです。本番環境での利用前に十分なレビューと調整を行ってください。

terraform {
  required_version = ">= 1.5"
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

# DNSSEC用KMSキーはus-east-1固定
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

# =============================================================
# 1. パブリックホストゾーン
# =============================================================
resource "aws_route53_zone" "public" {
  name    = var.domain_name
  comment = "${var.environment} public hosted zone for ${var.domain_name}"

  tags = {
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# =============================================================
# 2. プライベートホストゾーン（オプション）
#    前提: VPCはenableDnsHostnames=true, enableDnsSupport=true
# =============================================================
resource "aws_route53_zone" "private" {
  count = var.create_private_zone ? 1 : 0

  name    = "internal.${var.domain_name}"
  comment = "${var.environment} private hosted zone"

  vpc {
    vpc_id     = var.private_zone_vpc_id
    vpc_region = var.private_zone_vpc_region
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# =============================================================
# 3. 基本DNSレコード
# =============================================================

# SPFレコード（TXTレコードで設定: SPFタイプは非推奨 RFC 7208）
resource "aws_route53_record" "spf_txt" {
  zone_id = aws_route53_zone.public.zone_id
  name    = var.domain_name
  type    = "TXT"
  ttl     = 3600
  records = [
    "\"v=spf1 include:amazonses.com ~all\"",
    "\"google-site-verification=REPLACE_WITH_VERIFICATION_TOKEN\""
  ]
}

# MXレコード（メール設定例）
resource "aws_route53_record" "mx" {
  zone_id = aws_route53_zone.public.zone_id
  name    = var.domain_name
  type    = "MX"
  ttl     = 3600
  records = [
    "10 inbound-smtp.${var.aws_region}.amazonaws.com."
  ]
}

# CAAレコード（証明書発行認証局の制限）
resource "aws_route53_record" "caa" {
  zone_id = aws_route53_zone.public.zone_id
  name    = var.domain_name
  type    = "CAA"
  ttl     = 3600
  records = [
    "0 issue \"amazon.com\"",
    "0 issue \"amazontrust.com\"",
    "0 issuewild \"amazon.com\""
  ]
}

# =============================================================
# 4. Aliasレコード（ALB統合）
#    ALBのIPは動的に変わるためAlias必須。固定AレコードはNG
# =============================================================
resource "aws_route53_record" "alb_alias" {
  count = var.alb_dns_name != "" ? 1 : 0

  zone_id = aws_route53_zone.public.zone_id
  name    = "app.${var.domain_name}"
  type    = "A"

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = true
  }
}

# =============================================================
# 5. Aliasレコード（CloudFront統合）
#    CloudFrontのホストゾーンIDは固定値: Z2FDTNDATAQYW2
# =============================================================
resource "aws_route53_record" "cloudfront_alias_a" {
  count = var.cloudfront_domain_name != "" ? 1 : 0

  zone_id = aws_route53_zone.public.zone_id
  name    = "www.${var.domain_name}"
  type    = "A"

  alias {
    name                   = var.cloudfront_domain_name
    zone_id                = var.cloudfront_hosted_zone_id
    evaluate_target_health = false
  }
}

# IPv6対応（CloudFrontはデュアルスタック）
resource "aws_route53_record" "cloudfront_alias_aaaa" {
  count = var.cloudfront_domain_name != "" ? 1 : 0

  zone_id = aws_route53_zone.public.zone_id
  name    = "www.${var.domain_name}"
  type    = "AAAA"

  alias {
    name                   = var.cloudfront_domain_name
    zone_id                = var.cloudfront_hosted_zone_id
    evaluate_target_health = false
  }
}

# =============================================================
# 6. Aliasレコード（API Gateway Regional統合）
# =============================================================
resource "aws_route53_record" "api_gw_alias" {
  count = var.api_gw_regional_domain_name != "" ? 1 : 0

  zone_id = aws_route53_zone.public.zone_id
  name    = "api.${var.domain_name}"
  type    = "A"

  alias {
    name                   = var.api_gw_regional_domain_name
    zone_id                = var.api_gw_regional_zone_id
    evaluate_target_health = false
  }
}

# =============================================================
# 7. ヘルスチェック（エンドポイント監視）
# =============================================================

# プライマリエンドポイントのヘルスチェック
resource "aws_route53_health_check" "primary" {
  ip_address        = var.primary_endpoint_ip
  port              = 443
  type              = "HTTPS"
  resource_path     = var.health_check_path
  failure_threshold = var.health_check_failure_threshold
  request_interval  = var.health_check_interval

  # 文字列マッチング（レスポンスボディ最初の5120バイト内を確認）
  # search_string   = "OK"

  tags = {
    Name        = "${var.environment}-primary-hc"
    Environment = var.environment
  }
}

# セカンダリエンドポイントのヘルスチェック
resource "aws_route53_health_check" "secondary" {
  ip_address        = var.secondary_endpoint_ip
  port              = 443
  type              = "HTTPS"
  resource_path     = var.health_check_path
  failure_threshold = var.health_check_failure_threshold
  request_interval  = var.health_check_interval

  tags = {
    Name        = "${var.environment}-secondary-hc"
    Environment = var.environment
  }
}

# 計算型ヘルスチェック（複数エンドポイントの集約監視）
resource "aws_route53_health_check" "calculated" {
  type                   = "CALCULATED"
  child_health_threshold = 1
  child_healthchecks = [
    aws_route53_health_check.primary.id,
    aws_route53_health_check.secondary.id,
  ]

  tags = {
    Name        = "${var.environment}-calculated-hc"
    Environment = var.environment
  }
}

# =============================================================
# 8. SNSトピック & CloudWatchアラーム（ヘルスチェック通知）
#    Route 53ヘルスチェックのアラームはus-east-1に作成が必要
# =============================================================
resource "aws_sns_topic" "health_alert" {
  provider = aws.us_east_1
  name     = "${var.environment}-route53-health-alert"
}

resource "aws_sns_topic_subscription" "health_alert_email" {
  count     = var.alarm_email != "" ? 1 : 0
  provider  = aws.us_east_1
  topic_arn = aws_sns_topic.health_alert.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "primary_health" {
  provider            = aws.us_east_1
  alarm_name          = "${var.environment}-primary-health-check"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HealthCheckStatus"
  namespace           = "AWS/Route53"
  period              = 60
  statistic           = "Minimum"
  threshold           = 1

  dimensions = {
    HealthCheckId = aws_route53_health_check.primary.id
  }

  alarm_actions = [aws_sns_topic.health_alert.arn]
  ok_actions    = [aws_sns_topic.health_alert.arn]
}

# =============================================================
# 9. フェイルオーバールーティング（Active-Passive構成）
#    プライマリが不健全になるとセカンダリへ自動切り替え
# =============================================================
resource "aws_route53_record" "failover_primary" {
  zone_id        = aws_route53_zone.public.zone_id
  name           = "failover.${var.domain_name}"
  type           = "A"
  ttl            = 60
  records        = [var.primary_endpoint_ip]
  set_identifier = "primary"

  failover_routing_policy {
    type = "PRIMARY"
  }

  health_check_id = aws_route53_health_check.primary.id
}

resource "aws_route53_record" "failover_secondary" {
  zone_id        = aws_route53_zone.public.zone_id
  name           = "failover.${var.domain_name}"
  type           = "A"
  ttl            = 60
  records        = [var.secondary_endpoint_ip]
  set_identifier = "secondary"

  failover_routing_policy {
    type = "SECONDARY"
  }

  # セカンダリにもヘルスチェックを推奨
  health_check_id = aws_route53_health_check.secondary.id
}

# =============================================================
# 10. 加重ルーティング（カナリアデプロイ・段階的移行向け）
#     同一名前・タイプのレコードに重みを設定してトラフィック分散
# =============================================================
resource "aws_route53_record" "weighted" {
  for_each = {
    for idx, cfg in var.weighted_record_configs :
    "${cfg.region}" => cfg
  }

  zone_id        = aws_route53_zone.public.zone_id
  name           = "weighted.${var.domain_name}"
  type           = "A"
  ttl            = 60
  records        = [each.value.ip]
  set_identifier = "weighted-${each.key}"

  weighted_routing_policy {
    weight = each.value.weight
  }
}

# =============================================================
# 11. レイテンシールーティング（最低レイテンシーのリージョンへ転送）
# =============================================================
resource "aws_route53_record" "latency_ap" {
  zone_id        = aws_route53_zone.public.zone_id
  name           = "latency.${var.domain_name}"
  type           = "A"
  ttl            = 60
  records        = [var.primary_endpoint_ip]
  set_identifier = "ap-northeast-1"

  latency_routing_policy {
    region = "ap-northeast-1"
  }
}

resource "aws_route53_record" "latency_us" {
  zone_id        = aws_route53_zone.public.zone_id
  name           = "latency.${var.domain_name}"
  type           = "A"
  ttl            = 60
  records        = [var.secondary_endpoint_ip]
  set_identifier = "us-east-1"

  latency_routing_policy {
    region = "us-east-1"
  }
}

# =============================================================
# 12. 複数値回答ルーティング（最大8件の健全なIPをランダム返却）
# =============================================================
resource "aws_route53_record" "multivalue_1" {
  zone_id        = aws_route53_zone.public.zone_id
  name           = "multivalue.${var.domain_name}"
  type           = "A"
  ttl            = 60
  records        = [var.primary_endpoint_ip]
  set_identifier = "mv-1"

  multivalue_answer_routing_policy = true
  health_check_id                  = aws_route53_health_check.primary.id
}

resource "aws_route53_record" "multivalue_2" {
  zone_id        = aws_route53_zone.public.zone_id
  name           = "multivalue.${var.domain_name}"
  type           = "A"
  ttl            = 60
  records        = [var.secondary_endpoint_ip]
  set_identifier = "mv-2"

  multivalue_answer_routing_policy = true
  health_check_id                  = aws_route53_health_check.secondary.id
}

# =============================================================
# 13. DNSSEC設定
#     KMSキーはus-east-1固定要件、キースペック: ECC_NIST_P256
# =============================================================
resource "aws_kms_key" "dnssec" {
  count    = var.enable_dnssec ? 1 : 0
  provider = aws.us_east_1

  description              = "Route 53 DNSSEC KSK for ${var.domain_name}"
  customer_master_key_spec = "ECC_NIST_P256"
  key_usage                = "SIGN_VERIFY"
  deletion_window_in_days  = 7

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${var.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow Route 53 DNSSEC Service"
        Effect = "Allow"
        Principal = {
          Service = "dnssec-route53.amazonaws.com"
        }
        Action   = ["kms:DescribeKey", "kms:GetPublicKey", "kms:Sign"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = var.account_id
          }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:route53:::hostedzone/${aws_route53_zone.public.zone_id}"
          }
        }
      },
      {
        Sid    = "Allow Route 53 DNSSEC to CreateGrant"
        Effect = "Allow"
        Principal = {
          Service = "dnssec-route53.amazonaws.com"
        }
        Action   = ["kms:CreateGrant"]
        Resource = "*"
        Condition = {
          Bool = {
            "kms:GrantIsForAWSResource" = true
          }
        }
      }
    ]
  })

  tags = {
    Environment = var.environment
    Purpose     = "dnssec-ksk"
  }
}

resource "aws_kms_alias" "dnssec" {
  count         = var.enable_dnssec ? 1 : 0
  provider      = aws.us_east_1
  name          = "alias/${var.environment}-route53-dnssec"
  target_key_id = aws_kms_key.dnssec[0].key_id
}

resource "aws_route53_key_signing_key" "main" {
  count = var.enable_dnssec ? 1 : 0

  hosted_zone_id             = aws_route53_zone.public.id
  key_management_service_arn = aws_kms_key.dnssec[0].arn
  name                       = "${var.environment}-ksk"
  status                     = "ACTIVE"
}

resource "aws_route53_hosted_zone_dnssec" "main" {
  count = var.enable_dnssec ? 1 : 0

  depends_on = [aws_route53_key_signing_key.main]

  hosted_zone_id = aws_route53_key_signing_key.main[0].hosted_zone_id
}
