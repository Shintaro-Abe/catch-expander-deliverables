# PoC品質: このコードは概念実証・学習用途のスケルトンです。本番環境への適用前に
# セキュリティレビュー・コンプライアンス要件の確認を必ず実施してください。

# =============================================================================
# CloudHSM セキュリティグループ設計
#
# ポイント:
# - クラスター作成時に "cloudhsm-cluster-<clusterID>-sg" という名前で SG が自動生成される
# - EC2 クライアントにこの自動生成 SG をアタッチすることで HSM との通信が可能になる
# - HSM クライアント通信には TCP ポート 2223〜2225 が必要
# - EC2 の SG 上限 (5個) を超える場合は、デフォルト SG にルールを追加する代替案を参照
# =============================================================================

# -----------------------------------------------------------------------------
# EC2 クライアント用セキュリティグループ（アプリケーション側）
# main.tf の aws_instance.hsm_client にアタッチ。
# 管理アクセス (SSH) は特定 IP のみ許可。0.0.0.0/0 は絶対に禁止。
# -----------------------------------------------------------------------------
resource "aws_security_group" "hsm_client" {
  count = var.create_sample_client ? 1 : 0

  name        = "${var.name_prefix}-hsm-client-sg"
  description = "CloudHSM クライアント EC2 用セキュリティグループ"
  vpc_id      = var.vpc_id

  tags = merge(var.common_tags, {
    Name = "${var.name_prefix}-hsm-client-sg"
  })
}

# SSH 管理アクセス（特定 IP のみ）
resource "aws_vpc_security_group_ingress_rule" "hsm_client_ssh" {
  for_each          = var.create_sample_client ? toset(var.admin_cidr_blocks) : toset([])
  security_group_id = aws_security_group.hsm_client[0].id
  description       = "SSH 管理アクセス（特定 IP のみ）"
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
  cidr_ipv4         = each.value
}

# アウトバウンド: CloudHSM クラスター SG への TCP 2223-2225
# クライアント → HSM ENI の通信に必要。
resource "aws_vpc_security_group_egress_rule" "hsm_client_to_hsm" {
  count = var.create_sample_client ? 1 : 0

  security_group_id            = aws_security_group.hsm_client[0].id
  description                  = "CloudHSM クラスター ENI への通信 (TCP 2223-2225)"
  from_port                    = 2223
  to_port                      = 2225
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_cloudhsm_v2_cluster.main.security_group_id
}

# アウトバウンド: HTTPS（AWS API 呼び出し、SSM エージェント用）
resource "aws_vpc_security_group_egress_rule" "hsm_client_https" {
  count = var.create_sample_client ? 1 : 0

  security_group_id = aws_security_group.hsm_client[0].id
  description       = "AWS API / SSM エージェント用 HTTPS"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

# -----------------------------------------------------------------------------
# CloudHSM クラスター自動生成 SG へのルール追加
# クラスター作成時に自動生成される SG (aws_cloudhsm_v2_cluster.main.security_group_id) に
# 追加のインバウンドルールを設定する（クライアント SG からの通信を明示的に許可）。
#
# ※ EC2 の SG 上限 (5個) 超過時の代替構成:
#   - デフォルト SG に TCP 2223-2225 インバウンド（ソース: CloudHSM クラスター SG）を追加
#   - CloudHSM クラスター SG に TCP 2223-2225 インバウンド（ソース: デフォルト SG）を追加
# -----------------------------------------------------------------------------

# CloudHSM クラスター SG: クライアント SG からの TCP 2223-2225 を許可
resource "aws_vpc_security_group_ingress_rule" "cloudhsm_from_client" {
  count = var.create_sample_client ? 1 : 0

  security_group_id            = aws_cloudhsm_v2_cluster.main.security_group_id
  description                  = "クライアント SG からの HSM 通信 (TCP 2223-2225)"
  from_port                    = 2223
  to_port                      = 2225
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.hsm_client[0].id
}

# -----------------------------------------------------------------------------
# アプリケーション用セキュリティグループ（EC2 外の ECS タスク等から HSM を使う場合）
# EC2 以外のコンピューティング（Fargate タスク等）が HSM に接続するケースで使用。
# ENI に到達できれば VPC 外からも技術的には接続可能だが、プライベートサブネット推奨。
# -----------------------------------------------------------------------------
resource "aws_security_group" "hsm_app" {
  name        = "${var.name_prefix}-hsm-app-sg"
  description = "HSM を利用するアプリケーション向けセキュリティグループ"
  vpc_id      = var.vpc_id

  tags = merge(var.common_tags, {
    Name = "${var.name_prefix}-hsm-app-sg"
  })
}

# アウトバウンド: CloudHSM クラスター SG への TCP 2223-2225
resource "aws_vpc_security_group_egress_rule" "hsm_app_to_hsm" {
  security_group_id            = aws_security_group.hsm_app.id
  description                  = "CloudHSM ENI への通信 (TCP 2223-2225)"
  from_port                    = 2223
  to_port                      = 2225
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_cloudhsm_v2_cluster.main.security_group_id
}

# CloudHSM クラスター SG: アプリ SG からの TCP 2223-2225 を許可
resource "aws_vpc_security_group_ingress_rule" "cloudhsm_from_app" {
  security_group_id            = aws_cloudhsm_v2_cluster.main.security_group_id
  description                  = "アプリ SG からの HSM 通信 (TCP 2223-2225)"
  from_port                    = 2223
  to_port                      = 2225
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.hsm_app.id
}
