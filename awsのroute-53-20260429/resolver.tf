# PoC品質: このコードは概念実証・学習目的のスケルトンです。本番環境での利用前に十分なレビューと調整を行ってください。

# =============================================================
# Route 53 Resolver Inbound/Outboundエンドポイント
# オンプレミス ↔ AWS間のハイブリッドDNS解決に使用
# Direct Connect または Site-to-Site VPN が前提
# =============================================================

# ----- Inboundエンドポイント -----
# オンプレミスDNSサーバーからのクエリをVPC内Route 53 Resolverへ転送
# フロー: オンプレミスDNSサーバー → Forwardルール → Inbound Endpoint → Route 53 → プライベートHZ

resource "aws_route53_resolver_endpoint" "inbound" {
  count = var.create_resolver_endpoints ? 1 : 0

  name      = "${var.environment}-inbound-resolver"
  direction = "INBOUND"

  security_group_ids = var.resolver_security_group_ids

  # 高可用性のため最低2つのAZにENIを配置（ENIあたり10,000 QPS）
  dynamic "ip_address" {
    for_each = var.resolver_subnet_ids
    content {
      subnet_id = ip_address.value
    }
  }

  tags = {
    Name        = "${var.environment}-inbound-resolver"
    Environment = var.environment
  }
}

# ----- Outboundエンドポイント -----
# VPC内EC2からオンプレミスDNSサーバーへのクエリを転送
# フロー: EC2 → Route 53 Resolver(.2アドレス) → Resolverルール照合 → Outbound Endpoint → オンプレミスDNS

resource "aws_route53_resolver_endpoint" "outbound" {
  count = var.create_resolver_endpoints ? 1 : 0

  name      = "${var.environment}-outbound-resolver"
  direction = "OUTBOUND"

  security_group_ids = var.resolver_security_group_ids

  dynamic "ip_address" {
    for_each = var.resolver_subnet_ids
    content {
      subnet_id = ip_address.value
    }
  }

  tags = {
    Name        = "${var.environment}-outbound-resolver"
    Environment = var.environment
  }
}

# ----- Outbound Forwardingルール -----
# 指定ドメイン（オンプレミス）へのクエリをオンプレミスDNSに転送
# 最長一致（most specific match）が優先されるため、サブドメインルールが上位のルールより優先される

resource "aws_route53_resolver_rule" "forward_onprem" {
  count = var.create_resolver_endpoints && length(var.onprem_dns_ips) > 0 ? 1 : 0

  domain_name          = var.onprem_domain
  name                 = "${var.environment}-forward-to-onprem"
  rule_type            = "FORWARD"
  resolver_endpoint_id = aws_route53_resolver_endpoint.outbound[0].id

  dynamic "target_ip" {
    for_each = var.onprem_dns_ips
    content {
      ip   = target_ip.value
      port = 53
    }
  }

  tags = {
    Name        = "${var.environment}-forward-onprem"
    Environment = var.environment
  }
}

# ForwardingルールをVPCに関連付け
resource "aws_route53_resolver_rule_association" "forward_onprem" {
  count = var.create_resolver_endpoints && length(var.onprem_dns_ips) > 0 ? 1 : 0

  resolver_rule_id = aws_route53_resolver_rule.forward_onprem[0].id
  vpc_id           = var.resolver_vpc_id
}

# =============================================================
# Route 53 Resolver DNS Firewall
# VPC内のDNSクエリに対してALLOW/BLOCK/ALERTアクションを適用
# =============================================================

# ----- カスタムドメインリスト（ブロック対象）-----
resource "aws_route53_resolver_firewall_domain_list" "blocklist" {
  count = var.enable_dns_firewall ? 1 : 0

  name = "${var.environment}-custom-blocklist"

  # 運用ではS3などから動的にドメインリストを取得することを推奨
  domains = [
    "malicious-example.com.",
    "phishing-example.net."
  ]

  tags = {
    Environment = var.environment
  }
}

# ----- カスタムドメインリスト（明示許可）-----
resource "aws_route53_resolver_firewall_domain_list" "allowlist" {
  count = var.enable_dns_firewall ? 1 : 0

  name = "${var.environment}-custom-allowlist"

  domains = [
    "${var.domain_name}.",
    "amazonaws.com."
  ]

  tags = {
    Environment = var.environment
  }
}

# ----- ルールグループ -----
resource "aws_route53_resolver_firewall_rule_group" "main" {
  count = var.enable_dns_firewall ? 1 : 0

  name = "${var.environment}-dns-firewall-rg"

  tags = {
    Environment = var.environment
  }
}

# ルール1: AWSマネージド脅威リスト（アグリゲート）をBLOCK
# 本番前はACTION=ALERTでCloudWatchメトリクスを確認してからBLOCKに切り替えること
resource "aws_route53_resolver_firewall_rule" "block_malware" {
  count = var.enable_dns_firewall ? 1 : 0

  name                    = "block-aws-aggregate-threat-list"
  action                  = "BLOCK"
  block_response          = "NXDOMAIN"
  firewall_domain_list_id = data.aws_route53_resolver_firewall_domain_list.aggregate_threat[0].id
  firewall_rule_group_id  = aws_route53_resolver_firewall_rule_group.main[0].id
  priority                = 100
}

# ルール2: カスタムブロックリストをBLOCK
resource "aws_route53_resolver_firewall_rule" "block_custom" {
  count = var.enable_dns_firewall ? 1 : 0

  name                    = "block-custom-list"
  action                  = "BLOCK"
  block_response          = "NXDOMAIN"
  firewall_domain_list_id = aws_route53_resolver_firewall_domain_list.blocklist[0].id
  firewall_rule_group_id  = aws_route53_resolver_firewall_rule_group.main[0].id
  priority                = 200
}

# ルール3: 許可リストのドメインはALLOW（BLOCKルールより低い優先度番号で先に評価）
# 優先度は数値が小さいほど先に評価される
resource "aws_route53_resolver_firewall_rule" "allow_custom" {
  count = var.enable_dns_firewall ? 1 : 0

  name                    = "allow-trusted-domains"
  action                  = "ALLOW"
  firewall_domain_list_id = aws_route53_resolver_firewall_domain_list.allowlist[0].id
  firewall_rule_group_id  = aws_route53_resolver_firewall_rule_group.main[0].id
  priority                = 10
}

# AWSマネージドアグリゲート脅威リストの参照
data "aws_route53_resolver_firewall_domain_list" "aggregate_threat" {
  count = var.enable_dns_firewall ? 1 : 0

  # AWSManagedDomainsAggregateThreatList を名前で検索
  # 利用可能なリスト: AWSManagedDomainsMalwareDomainList,
  #   AWSManagedDomainsBotnetCommandandControl,
  #   AWSManagedDomainsAggregateThreatList,
  #   AWSManagedDomainsAmazonGuardDutyThreatList
  filter {
    name   = "Name"
    values = ["AWSManagedDomainsAggregateThreatList"]
  }
}

# DNS FirewallルールグループをVPCに関連付け
resource "aws_route53_resolver_firewall_rule_group_association" "main" {
  count = var.enable_dns_firewall && var.dns_firewall_vpc_id != "" ? 1 : 0

  name                   = "${var.environment}-dns-firewall-assoc"
  firewall_rule_group_id = aws_route53_resolver_firewall_rule_group.main[0].id
  vpc_id                 = var.dns_firewall_vpc_id
  priority               = 100

  # mutation_protection: 誤操作によるルールグループの削除を防止
  mutation_protection = "ENABLED"

  tags = {
    Environment = var.environment
  }
}

# =============================================================
# CloudWatchメトリクス監視（DNS Firewall）
# ブロックされたクエリを検知してSNS通知
# =============================================================
resource "aws_cloudwatch_metric_alarm" "dns_firewall_blocks" {
  count    = var.enable_dns_firewall ? 1 : 0
  provider = aws.us_east_1

  alarm_name          = "${var.environment}-dns-firewall-blocks"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FirewallQueryVolumeForRuleGroup"
  namespace           = "AWS/Route53Resolver"
  period              = 300
  statistic           = "Sum"
  threshold           = 100

  alarm_description = "DNS Firewallでブロックされたクエリ数が閾値を超過"
  alarm_actions     = [aws_sns_topic.health_alert.arn]

  dimensions = {
    FirewallRuleGroupId = aws_route53_resolver_firewall_rule_group.main[0].id
    VpcId               = var.dns_firewall_vpc_id
    FirewallRuleAction  = "BLOCK"
  }
}
