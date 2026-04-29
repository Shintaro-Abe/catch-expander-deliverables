# PoC品質: このコードは概念実証・学習目的のスケルトンです。本番環境での利用前に十分なレビューと調整を行ってください。

variable "domain_name" {
  description = "管理するドメイン名（例: example.com）"
  type        = string
}

variable "aws_region" {
  description = "AWSリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "environment" {
  description = "環境名（dev / stg / prod）"
  type        = string
  default     = "dev"
}

# =========================================================
# プライベートホストゾーン用
# =========================================================
variable "create_private_zone" {
  description = "プライベートホストゾーンを作成するか"
  type        = bool
  default     = false
}

variable "private_zone_vpc_id" {
  description = "プライベートホストゾーンに関連付けるVPC ID"
  type        = string
  default     = ""
}

variable "private_zone_vpc_region" {
  description = "プライベートホストゾーンに関連付けるVPCのリージョン"
  type        = string
  default     = "ap-northeast-1"
}

# =========================================================
# ヘルスチェック設定
# =========================================================
variable "primary_endpoint_ip" {
  description = "プライマリエンドポイントのIPアドレス（フェイルオーバー構成用）"
  type        = string
  default     = "10.0.0.1"
}

variable "secondary_endpoint_ip" {
  description = "セカンダリエンドポイントのIPアドレス（フェイルオーバー構成用）"
  type        = string
  default     = "10.0.1.1"
}

variable "health_check_path" {
  description = "ヘルスチェックのHTTPパス"
  type        = string
  default     = "/health"
}

variable "health_check_interval" {
  description = "ヘルスチェック間隔（秒）: 10 または 30"
  type        = number
  default     = 30
}

variable "health_check_failure_threshold" {
  description = "不健全と判定するまでの連続失敗回数（1〜10）"
  type        = number
  default     = 3
}

# =========================================================
# ALB / API Gateway / CloudFront（Alias統合）
# =========================================================
variable "alb_dns_name" {
  description = "ALBのDNS名（Aliasレコード用）"
  type        = string
  default     = ""
}

variable "alb_zone_id" {
  description = "ALBのホストゾーンID"
  type        = string
  default     = ""
}

variable "cloudfront_domain_name" {
  description = "CloudFrontディストリビューションのドメイン名"
  type        = string
  default     = ""
}

# CloudFrontのホストゾーンIDは固定値
variable "cloudfront_hosted_zone_id" {
  description = "CloudFrontのホストゾーンID（固定値: Z2FDTNDATAQYW2）"
  type        = string
  default     = "Z2FDTNDATAQYW2"
}

variable "api_gw_regional_domain_name" {
  description = "API GatewayリージョナルカスタムドメインのDNS名"
  type        = string
  default     = ""
}

variable "api_gw_regional_zone_id" {
  description = "API GatewayリージョナルカスタムドメインのホストゾーンID"
  type        = string
  default     = ""
}

# =========================================================
# DNSSEC設定
# =========================================================
variable "enable_dnssec" {
  description = "DNSSECを有効化するか（KMSキーはus-east-1に作成される）"
  type        = bool
  default     = false
}

variable "account_id" {
  description = "AWSアカウントID（DNSSECのKMSポリシー設定に使用）"
  type        = string
  default     = ""
}

# =========================================================
# Route 53 Resolver設定
# =========================================================
variable "create_resolver_endpoints" {
  description = "Route 53 Resolverエンドポイント（Inbound/Outbound）を作成するか"
  type        = bool
  default     = false
}

variable "resolver_vpc_id" {
  description = "ResolverエンドポイントをデプロイするVPC ID"
  type        = string
  default     = ""
}

variable "resolver_subnet_ids" {
  description = "Resolverエンドポイント用サブネットID（高可用性のため最低2つのAZ）"
  type        = list(string)
  default     = []
}

variable "resolver_security_group_ids" {
  description = "ResolverエンドポイントのセキュリティグループID"
  type        = list(string)
  default     = []
}

variable "onprem_dns_ips" {
  description = "オンプレミスDNSサーバーのIPアドレスリスト（Outboundフォワーディング用）"
  type        = list(string)
  default     = []
}

variable "onprem_domain" {
  description = "Outboundフォワーディング対象のオンプレミスドメイン（例: onprem.internal）"
  type        = string
  default     = "onprem.internal"
}

# =========================================================
# DNS Firewall設定
# =========================================================
variable "enable_dns_firewall" {
  description = "Route 53 Resolver DNS Firewallを有効化するか"
  type        = bool
  default     = false
}

variable "dns_firewall_vpc_id" {
  description = "DNS FirewallルールグループをアタッチするVPC ID"
  type        = string
  default     = ""
}

# =========================================================
# 加重ルーティング設定
# =========================================================
variable "weighted_record_configs" {
  description = "加重ルーティングのレコード設定リスト"
  type = list(object({
    ip     = string
    weight = number
    region = string
  }))
  default = [
    { ip = "203.0.113.10", weight = 50, region = "ap-northeast-1" },
    { ip = "203.0.113.20", weight = 50, region = "us-east-1" }
  ]
}

# =========================================================
# アラーム通知設定
# =========================================================
variable "alarm_email" {
  description = "ヘルスチェックアラームのSNS通知先メールアドレス"
  type        = string
  default     = ""
}
