# PoC品質: このコードは概念実証・学習用途のスケルトンです。本番環境への適用前に
# セキュリティレビュー・コンプライアンス要件の確認を必ず実施してください。

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# -----------------------------------------------------------------------------
# データソース: 既存 VPC / サブネット取得
# -----------------------------------------------------------------------------
data "aws_vpc" "target" {
  id = var.vpc_id
}

data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [var.vpc_id]
  }
  filter {
    name   = "tag:Tier"
    values = ["private"]
  }
}

# サブネットの詳細（AZ確認用）
data "aws_subnet" "private" {
  for_each = toset(var.hsm_subnet_ids)
  id       = each.value
}

# -----------------------------------------------------------------------------
# CloudHSM クラスター
# hsm2m.medium を指定（FIPS 140-3 Level 3 認証済み、推奨タイプ）
# クラスター作成後にサブネットを追加できないため、全対象AZを初期指定すること
# -----------------------------------------------------------------------------
resource "aws_cloudhsm_v2_cluster" "main" {
  hsm_type   = var.hsm_type          # "hsm2m.medium" (推奨) or "hsm1.medium" (非推奨)
  subnet_ids = var.hsm_subnet_ids    # 複数AZのプライベートサブネット

  # バックアップ保持設定: 7〜379日。デフォルト 90日
  tags = merge(var.common_tags, {
    Name = "${var.name_prefix}-cloudhsm-cluster"
  })
}

# -----------------------------------------------------------------------------
# HSM インスタンス
# HA構成: 異なるAZに最低2台配置。ミッションクリティカルは3台以上推奨。
# 各 HSM 作成時にユーザー指定サブネットへ ENI が自動配置される。
# -----------------------------------------------------------------------------
resource "aws_cloudhsm_v2_hsm" "az1" {
  cluster_id        = aws_cloudhsm_v2_cluster.main.cluster_id
  subnet_id         = var.hsm_subnet_ids[0]   # AZ-1 プライベートサブネット
  availability_zone = data.aws_subnet.private[var.hsm_subnet_ids[0]].availability_zone
}

resource "aws_cloudhsm_v2_hsm" "az2" {
  cluster_id        = aws_cloudhsm_v2_cluster.main.cluster_id
  subnet_id         = var.hsm_subnet_ids[1]   # AZ-2 プライベートサブネット
  availability_zone = data.aws_subnet.private[var.hsm_subnet_ids[1]].availability_zone

  # HSM 同士はクラスター内でキー・ポリシーを自動同期（サーバーサイド同期）
  depends_on = [aws_cloudhsm_v2_hsm.az1]
}

# ミッションクリティカル構成: 3台目 HSM (オプション)
resource "aws_cloudhsm_v2_hsm" "az3" {
  count = var.enable_third_hsm ? 1 : 0

  cluster_id        = aws_cloudhsm_v2_cluster.main.cluster_id
  subnet_id         = var.hsm_subnet_ids[2]   # AZ-3 プライベートサブネット（3AZ構成時）
  availability_zone = data.aws_subnet.private[var.hsm_subnet_ids[2]].availability_zone

  depends_on = [aws_cloudhsm_v2_hsm.az2]
}

# -----------------------------------------------------------------------------
# KMS Custom Key Store（オプション）
# KMS の利便性と CloudHSM のシングルテナント制御を組み合わせるハイブリッド構成。
# 制約: 対称暗号化キーのみ対応、自動ローテーション不可、インポートキー不可
# -----------------------------------------------------------------------------
resource "aws_kms_custom_key_store" "cloudhsm" {
  count = var.enable_kms_custom_key_store ? 1 : 0

  custom_key_store_name = "${var.name_prefix}-cloudhsm-keystore"
  cloud_hsm_cluster_id  = aws_cloudhsm_v2_cluster.main.cluster_id

  # クラスター初期化後に設定: CloudHSM の kmsuser CU パスワード
  # シークレットは Secrets Manager または環境変数で管理し、ここにはハードコードしない
  key_store_password = var.kms_key_store_password

  # クラスター側から取得した信頼済み証明書
  trust_anchor_certificate = var.trust_anchor_certificate
}

# KMS CMK（Custom Key Store 使用時）
resource "aws_kms_key" "custom_store_key" {
  count = var.enable_kms_custom_key_store ? 1 : 0

  description              = "${var.name_prefix} - CloudHSM backed CMK"
  custom_key_store_id      = aws_kms_custom_key_store.cloudhsm[0].id
  deletion_window_in_days  = 30

  tags = merge(var.common_tags, {
    Name = "${var.name_prefix}-cloudhsm-cmk"
  })
}

resource "aws_kms_alias" "custom_store_key" {
  count         = var.enable_kms_custom_key_store ? 1 : 0
  name          = "alias/${var.name_prefix}-cloudhsm-cmk"
  target_key_id = aws_kms_key.custom_store_key[0].key_id
}

# -----------------------------------------------------------------------------
# EC2 クライアントインスタンス（HSM 接続用踏み台 / アプリサーバー例）
# 実際のアプリサーバーには AMI・ユーザーデータ等を適切に設定すること
# HSM クライアントは Client SDK 5 をインストールし、
# configure-pkcs11 / configure-jce / configure-openssl-provider で設定する
# -----------------------------------------------------------------------------
resource "aws_instance" "hsm_client" {
  count = var.create_sample_client ? 1 : 0

  ami                    = var.client_ami_id
  instance_type          = var.client_instance_type  # t.nano/t.micro は不可（リソース不足）
  subnet_id              = var.hsm_subnet_ids[0]
  vpc_security_group_ids = [
    aws_security_group.hsm_client[0].id,
    aws_cloudhsm_v2_cluster.main.security_group_id,  # クラスター自動生成 SG をアタッチ
  ]
  iam_instance_profile = aws_iam_instance_profile.hsm_client[0].name

  # IMDSv2 強制（セキュリティベストプラクティス）
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    encrypted = true
  }

  tags = merge(var.common_tags, {
    Name = "${var.name_prefix}-hsm-client"
  })
}

# -----------------------------------------------------------------------------
# IAM ロール（EC2 クライアント用）
# CloudHSM API の読み取り権限。実運用では最小権限原則に従い精査すること。
# -----------------------------------------------------------------------------
resource "aws_iam_role" "hsm_client" {
  count = var.create_sample_client ? 1 : 0

  name = "${var.name_prefix}-hsm-client-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })

  tags = var.common_tags
}

resource "aws_iam_role_policy_attachment" "hsm_client_ssm" {
  count      = var.create_sample_client ? 1 : 0
  role       = aws_iam_role.hsm_client[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "hsm_client_describe" {
  count = var.create_sample_client ? 1 : 0
  name  = "cloudhsm-describe"
  role  = aws_iam_role.hsm_client[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["cloudhsm:DescribeClusters", "cloudhsm:DescribeHsm"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_instance_profile" "hsm_client" {
  count = var.create_sample_client ? 1 : 0
  name  = "${var.name_prefix}-hsm-client-profile"
  role  = aws_iam_role.hsm_client[0].name
}
