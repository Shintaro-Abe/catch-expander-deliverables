# PoC品質: このコードは概念実証・学習用途のスケルトンです。本番環境への適用前に
# セキュリティレビュー・コンプライアンス要件の確認を必ず実施してください。

# -----------------------------------------------------------------------------
# CloudHSM クラスター情報
# -----------------------------------------------------------------------------
output "cluster_id" {
  description = "CloudHSM クラスター ID"
  value       = aws_cloudhsm_v2_cluster.main.cluster_id
}

output "cluster_state" {
  description = <<-EOT
    クラスターの状態。
    CREATE_IN_PROGRESS → UNINITIALIZED → INITIALIZE_IN_PROGRESS → INITIALIZED → ACTIVE
    初期化手順: CSR 取得 → ルート CA 秘密鍵生成 → CSR 署名 → initialize-cluster 実行
  EOT
  value = aws_cloudhsm_v2_cluster.main.cluster_state
}

output "cluster_certificates" {
  description = <<-EOT
    クラスター証明書情報。
    cluster_csr: HSM の最初の1台が生成する CSR（初期化時に署名が必要）
    aws_hardware_certificate: AWS ハードウェア証明書（HSM 正真性検証用）
    hsm_certificate: HSM 証明書
    manufacturer_hardware_certificate: 製造元ハードウェア証明書
    初期化前のみ HSM の身元・正真性検証が可能（初期化後は不可）
  EOT
  value     = aws_cloudhsm_v2_cluster.main.cluster_certificates
  sensitive = false
}

output "cluster_security_group_id" {
  description = <<-EOT
    クラスター自動生成セキュリティグループ ID (cloudhsm-cluster-<clusterID>-sg)。
    EC2 クライアントインスタンスにこの SG をアタッチすることで HSM と通信可能になる。
  EOT
  value = aws_cloudhsm_v2_cluster.main.security_group_id
}

# -----------------------------------------------------------------------------
# HSM インスタンス情報
# -----------------------------------------------------------------------------
output "hsm_az1_id" {
  description = "AZ-1 の HSM インスタンス ID"
  value       = aws_cloudhsm_v2_hsm.az1.hsm_id
}

output "hsm_az2_id" {
  description = "AZ-2 の HSM インスタンス ID"
  value       = aws_cloudhsm_v2_hsm.az2.hsm_id
}

output "hsm_az1_eni_ip" {
  description = <<-EOT
    AZ-1 HSM の ENI IP アドレス。
    クライアント SDK は ENI 経由で HSM と通信する（HSM 本体へは直接アクセスしない）。
  EOT
  value = aws_cloudhsm_v2_hsm.az1.ip_address
}

output "hsm_az2_eni_ip" {
  description = "AZ-2 HSM の ENI IP アドレス"
  value       = aws_cloudhsm_v2_hsm.az2.ip_address
}

output "hsm_az3_id" {
  description = "AZ-3 の HSM インスタンス ID（enable_third_hsm=true 時のみ）"
  value       = var.enable_third_hsm ? aws_cloudhsm_v2_hsm.az3[0].hsm_id : null
}

# -----------------------------------------------------------------------------
# KMS Custom Key Store 情報（オプション）
# -----------------------------------------------------------------------------
output "kms_custom_key_store_id" {
  description = "KMS Custom Key Store ID（enable_kms_custom_key_store=true 時のみ）"
  value       = var.enable_kms_custom_key_store ? aws_kms_custom_key_store.cloudhsm[0].id : null
}

output "kms_cmk_arn" {
  description = "KMS CMK ARN（enable_kms_custom_key_store=true 時のみ）"
  value       = var.enable_kms_custom_key_store ? aws_kms_key.custom_store_key[0].arn : null
}

# -----------------------------------------------------------------------------
# セキュリティグループ情報
# -----------------------------------------------------------------------------
output "hsm_app_security_group_id" {
  description = "アプリケーション用 SG ID。HSM を利用するアプリコンピューティングにアタッチする。"
  value       = aws_security_group.hsm_app.id
}

output "hsm_client_security_group_id" {
  description = "EC2 クライアント用 SG ID（create_sample_client=true 時のみ）"
  value       = var.create_sample_client ? aws_security_group.hsm_client[0].id : null
}

# -----------------------------------------------------------------------------
# 初期化・設定手順ガイド（terraform output で確認可能）
# -----------------------------------------------------------------------------
output "next_steps" {
  description = "クラスター初期化・アクティベーションの手順概要"
  value       = <<-EOT
    === CloudHSM クラスター初期化手順 ===

    1. CSR 取得:
       aws cloudhsmv2 describe-clusters --filters clusterIds=${aws_cloudhsm_v2_cluster.main.cluster_id} \
         --query 'Clusters[0].Certificates.ClusterCsr' --output text > cluster.csr

    2. ルート CA 秘密鍵生成 (自己署名の例):
       openssl genrsa -aes256 -out ca.key 4096
       openssl req -new -x509 -days 3652 -key ca.key -out ca.crt

    3. CSR への署名:
       openssl x509 -req -days 3652 -in cluster.csr -CA ca.crt -CAkey ca.key \
         -CAcreateserial -out cluster.crt

    4. クラスター初期化:
       aws cloudhsmv2 initialize-cluster \
         --cluster-id ${aws_cloudhsm_v2_cluster.main.cluster_id} \
         --signed-cert file://cluster.crt \
         --trust-anchor file://ca.crt

    5. Client SDK 5 インストール後の設定:
       sudo /opt/cloudhsm/bin/configure-pkcs11 --cluster-id ${aws_cloudhsm_v2_cluster.main.cluster_id}
       # または JCE: configure-jce
       # または OpenSSL: configure-openssl-provider (hsm2m.medium 専用)

    6. 初期管理者 (admin) パスワード設定:
       /opt/cloudhsm/bin/cloudhsm-cli cluster activate
       # admin パスワードを設定し、クラスターをアクティブ状態にする

    HA 構成確認:
    - 各 HSM が異なる AZ に配置されていること
    - キーが少なくとも 2 AZ・2 台の HSM に同期されていること
    - 負荷テストで想定ピーク負荷を測定し、それ + 1 台の HSM を確保すること
  EOT
}
