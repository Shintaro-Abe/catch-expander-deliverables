# PoC品質: このコードは概念実証・学習目的のスケルトンです。本番環境での利用前に十分なレビューと調整を行ってください。

# =============================================================
# パブリックホストゾーン
# =============================================================
output "public_zone_id" {
  description = "パブリックホストゾーンID"
  value       = aws_route53_zone.public.zone_id
}

output "public_zone_name_servers" {
  description = "パブリックホストゾーンのNSレコード（ドメインレジストラへ登録する値）"
  value       = aws_route53_zone.public.name_servers
}

# =============================================================
# プライベートホストゾーン
# =============================================================
output "private_zone_id" {
  description = "プライベートホストゾーンID（作成した場合）"
  value       = var.create_private_zone ? aws_route53_zone.private[0].zone_id : null
}

# =============================================================
# ヘルスチェック
# =============================================================
output "primary_health_check_id" {
  description = "プライマリエンドポイントのヘルスチェックID"
  value       = aws_route53_health_check.primary.id
}

output "secondary_health_check_id" {
  description = "セカンダリエンドポイントのヘルスチェックID"
  value       = aws_route53_health_check.secondary.id
}

output "calculated_health_check_id" {
  description = "計算型ヘルスチェックID（プライマリ・セカンダリの集約）"
  value       = aws_route53_health_check.calculated.id
}

# =============================================================
# フェイルオーバーレコード
# =============================================================
output "failover_fqdn" {
  description = "フェイルオーバーレコードのFQDN"
  value       = aws_route53_record.failover_primary.fqdn
}

# =============================================================
# SNS通知
# =============================================================
output "health_alert_sns_arn" {
  description = "ヘルスチェックアラートSNSトピックARN"
  value       = aws_sns_topic.health_alert.arn
}

# =============================================================
# DNSSEC
# =============================================================
output "dnssec_kms_key_arn" {
  description = "DNSSEC KSK用KMSキーARN（DNSSECが有効な場合）"
  value       = var.enable_dnssec ? aws_kms_key.dnssec[0].arn : null
}

output "dnssec_ksk_name" {
  description = "DNSSEC KSK名（DNSSECが有効な場合）"
  value       = var.enable_dnssec ? aws_route53_key_signing_key.main[0].name : null
}

output "dnssec_ds_record" {
  description = "DNSSEC DSレコード値（レジストラまたは親ゾーンへ登録する値）"
  value       = var.enable_dnssec ? aws_route53_key_signing_key.main[0].ds_record : null
  sensitive   = false
}

# =============================================================
# Route 53 Resolver
# =============================================================
output "resolver_inbound_endpoint_id" {
  description = "ResolverインバウンドエンドポイントのエンドポイントID（作成した場合）"
  value       = var.create_resolver_endpoints ? aws_route53_resolver_endpoint.inbound[0].id : null
}

output "resolver_inbound_ip_addresses" {
  description = "Resolverインバウンドエンドポイントに割り当てられたIPアドレス（オンプレミスのForwardルールに設定する値）"
  value       = var.create_resolver_endpoints ? aws_route53_resolver_endpoint.inbound[0].ip_address : null
}

output "resolver_outbound_endpoint_id" {
  description = "ResolverアウトバウンドエンドポイントのエンドポイントID（作成した場合）"
  value       = var.create_resolver_endpoints ? aws_route53_resolver_endpoint.outbound[0].id : null
}

output "dns_firewall_rule_group_id" {
  description = "DNS FirewallルールグループID（作成した場合）"
  value       = var.enable_dns_firewall ? aws_route53_resolver_firewall_rule_group.main[0].id : null
}

# =============================================================
# 加重ルーティング
# =============================================================
output "weighted_record_fqdns" {
  description = "加重ルーティングレコードのFQDN一覧"
  value       = { for k, v in aws_route53_record.weighted : k => v.fqdn }
}

# =============================================================
# 動作確認用コマンド例
# =============================================================
output "dig_commands" {
  description = "設定確認用のdigコマンド例"
  value = {
    public_soa      = "dig SOA ${var.domain_name} @8.8.8.8"
    failover        = "dig A failover.${var.domain_name} @8.8.8.8"
    latency_ap      = "dig A latency.${var.domain_name} @8.8.8.8"
    multivalue      = "dig A multivalue.${var.domain_name} @8.8.8.8"
    dnssec_verify   = "dig A ${var.domain_name} +dnssec @8.8.8.8"
  }
}
