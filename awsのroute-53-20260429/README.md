## IaCコード（Terraform または CloudFormation）

# AWS Route 53 Terraform IaC（PoC品質）

> **注意**: このコードは概念実証・学習目的のスケルトンです。本番環境での利用前に十分なレビューと調整を行ってください。

## 構成ファイル

| ファイル | 内容 |
|---------|------|
| `main.tf` | ホストゾーン、DNSレコード、ヘルスチェック、フェイルオーバー、DNSSEC |
| `resolver.tf` | Route 53 Resolver（Inbound/Outbound）、DNS Firewall |
| `variables.tf` | 全変数定義 |
| `outputs.tf` | 出力値（NSレコード、ヘルスチェックID、DSレコード等） |

## カバーしている機能

### ホストゾーン
- **パブリックホストゾーン**: TXTレコード（SPF）、MXレコード、CAAレコード
- **プライベートホストゾーン**: VPC内専用（`create_private_zone = true`で有効化）

### AWSサービス統合（Aliasレコード）
- **ALB**: IPが動的に変わるためAlias必須（固定AレコードはNG）
- **CloudFront**: A/AAAAの両レコードを作成（IPv6デュアルスタック対応）、ホストゾーンID固定値: `Z2FDTNDATAQYW2`
- **API Gateway Regional**: Regional用証明書はAPIと同一リージョンで発行が必要

### ルーティングポリシー
| ポリシー | リソース名 | 用途 |
|---------|-----------|------|
| フェイルオーバー | `failover.{domain}` | Active-Passive構成 |
| 加重 | `weighted.{domain}` | カナリアデプロイ・段階移行 |
| レイテンシー | `latency.{domain}` | マルチリージョン最低レイテンシー転送 |
| 複数値回答 | `multivalue.{domain}` | 最大8件の健全なIPをランダム返却 |

### ヘルスチェック
- エンドポイント監視（HTTPS、失敗閾値・間隔設定可能）
- 計算型ヘルスチェック（複数エンドポイントの集約）
- CloudWatch Alarm + SNS通知（アラームはus-east-1に作成）

**フェイルオーバー所要時間の目安:**
```
最短: 評価間隔(10秒) × 閾値(1) + TTL
標準: 評価間隔(30秒) × 閾値(3) + TTL ≈ 90秒 + TTL
```

### DNSSEC（`enable_dnssec = true`で有効化）
- KMSキーはus-east-1固定、キースペック: `ECC_NIST_P256`
- KSK（Key Signing Key）をユーザー管理KMS CMKで作成
- ZSK（Zone Signing Key）はRoute 53が自動管理
- Confused Deputy攻撃対策のKMSポリシー（`aws:SourceAccount` / `aws:SourceArn`条件）を実装
- `outputs.tf`の`dnssec_ds_record`出力をレジストラへ登録してChain of Trustを確立

### Route 53 Resolver（`create_resolver_endpoints = true`で有効化）
- Inboundエンドポイント: オンプレミスDNS → AWS Route 53へのクエリ受け付け
- Outboundエンドポイント + Forwardingルール: AWS → オンプレミスDNSへのクエリ転送
- 高可用性のため最低2つのAZにサブネットを指定（`resolver_subnet_ids`）
- Direct Connect または Site-to-Site VPN が接続前提

### DNS Firewall（`enable_dns_firewall = true`で有効化）
- AWSマネージドアグリゲート脅威リスト（BLOCK）
- カスタムブロックリスト（BLOCK）
- 信頼済みドメイン許可リスト（ALLOW）- 優先度を低く設定して先に評価
- CloudWatchアラーム連携でブロックイベントを通知
- **運用Tips**: 本番前はACTION=ALERTでテストしてからBLOCKに切り替えること

## 使い方

```bash
# 初期化
terraform init

# 最小構成（パブリックHZ + ヘルスチェック + フェイルオーバー）
terraform apply \
  -var="domain_name=example.com" \
  -var="primary_endpoint_ip=203.0.113.10" \
  -var="secondary_endpoint_ip=203.0.113.20" \
  -var="alarm_email=ops@example.com"

# DNSSECも含めて有効化
terraform apply \
  -var="domain_name=example.com" \
  -var="enable_dnssec=true" \
  -var="account_id=123456789012" \
  ...

# 設定確認
dig SOA example.com @8.8.8.8
dig A failover.example.com @8.8.8.8
dig A example.com +dnssec @8.8.8.8
```

## 重要な制約事項

| 項目 | 制約 |
|------|------|
| DNSSECのKMSキー | **us-east-1固定**（他リージョン不可） |
| CloudFront証明書 | **us-east-1固定** |
| Route 53ヘルスチェックアラーム | **us-east-1固定** |
| CloudFrontホストゾーンID | **Z2FDTNDATAQYW2固定** |
| Route 53タグベース条件キー | `aws:ResourceTag/*`は**非サポート** |
| プライベートHZ NXDOMAIN | VPC内でHZに一致するレコードがない場合はパブリックDNSへのフォールバックなし |

## コスト概算（参考）

- ホストゾーン: $0.50/月/HZ
- ヘルスチェック（標準30秒）: $0.50/月/HZ
- ヘルスチェック（高速10秒）: $1.00/月/HZ
- Resolverエンドポイント: $0.125/時/エンドポイント
- DNS Firewallルールグループ: $1.00/月/ルールグループ/VPC + $0.60/100万クエリ


---

📝 [Notionで詳細を見る]()
