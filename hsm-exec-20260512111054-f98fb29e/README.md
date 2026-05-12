## IaCコード（Terraform または CloudFormation）

# AWS CloudHSM Terraform テンプレート（PoC品質）

> **注意**: このコードは概念実証・学習用途のスケルトンです。本番環境への適用前に
> セキュリティレビュー・コンプライアンス要件の確認を必ず実施してください。

---

## 概要

このテンプレートは **AWS CloudHSM**（ハードウェアセキュリティモジュール）クラスターを
Terraform で構築するための IaC コードです。以下の構成要素を含みます。

| ファイル | 内容 |
|---|---|
| `main.tf` | CloudHSM クラスター・HSM インスタンス・EC2 クライアント・IAM |
| `variables.tf` | 入力変数定義（バリデーション付き） |
| `security_groups.tf` | セキュリティグループ設計（TCP 2223-2225 制御） |
| `outputs.tf` | クラスター ID・ENI IP・初期化手順ガイドの出力 |

---

## HSM とは（初学者向け）

**HSM（Hardware Security Module）** は、暗号鍵を安全に生成・保管・管理するための
**専用ハードウェアデバイス**です。

- **何を守るか**: 暗号鍵（秘密鍵）がサーバーのメモリやストレージに平文で露出しないよう保護
- **なぜハードウェアか**: HSM は独立した専用チップ上で動作し、物理的改ざんを検知・対応（侵入時に鍵を自動消去）。OS やアプリへの攻撃から鍵を隔離できる

```
アプリケーション
   │ データを送信して処理を依頼
   ▼
 CloudHSM（ENI 経由）
   └─ 暗号化・復号・署名などをハードウェア上で実行
   └─ 鍵は HSM 外に平文で出ない（ブラックボックスモデル）
```

---

## アーキテクチャ

```
VPC
├── AZ-a (プライベートサブネット)
│   ├── HSM インスタンス 1  ←── ENI (Elastic Network Interface)
│   └── EC2 クライアント（オプション）
│
├── AZ-b (プライベートサブネット)
│   └── HSM インスタンス 2  ←── ENI
│
└── AZ-c (プライベートサブネット) ※ミッションクリティカル時
    └── HSM インスタンス 3  ←── ENI
```

> **ポイント**: HSM 本体は AWS 管理の別 VPC に存在し、ユーザー VPC には ENI のみ配置される。
> クライアントは ENI 経由で HSM と通信する（HSM 本体へは直接アクセスしない）。

---

## HA（高可用性）設計

| 構成 | HSM 台数 | 説明 |
|---|:---:|---|
| 最小 HA | 2台 | 異なる AZ に各1台。単一 AZ 障害時の冗長性を確保 |
| ミッションクリティカル | 3台以上 | 2以上の AZ に合計3台以上 |
| 最大 | 28台 | 1クラスターあたりの上限（リージョンデフォルト上限は6台） |

**自動ロードバランシング**: 複数 HSM 構成時、クライアント SDK が各 HSM の処理余力に基づいて
自動でロードバランシングを実施する。手動設定は不要。

---

## コスト概算

| 構成 | 月額概算 |
|---|---|
| 1 HSM（非HA、テスト用） | 約 $1,044〜$1,152 |
| 2 HSM（最小 HA 構成） | 約 $2,088〜$2,336 |
| 3 HSM（推奨構成） | 約 $3,132〜$3,504 |

> **AWS KMS との比較**: KMS は $1/キー/月〜 と大幅に安価。CloudHSM は PKCS#11/JCE/CNG 対応や
> 専有ハードウェア要件がある場合に選択する。月 7 億回以上の暗号操作でコスト差が逆転。

---

## メリット・デメリット

### メリット
- **シングルテナント専有ハードウェア**: FIPS 140-3 Level 3 認証済み（hsm2m.medium）
- **完全なキー制御**: 生成・バックアップ・エクスポートまで顧客が管理
- **標準インターフェース対応**: PKCS#11 / JCE / OpenSSL / CNG でレガシーアプリを移行容易
- **E2E 暗号化**: 暗号処理は AWS に不可視
- **特殊ユースケース**: TLS オフロード・CA 秘密鍵管理・Oracle TDE・コード署名等

### デメリット
- **高コスト**: 最低2台で月額 $2,000 以上（KMS の 100 倍以上になることも）
- **運用負担が高い**: クラスター管理・ユーザー管理・バックアップ・DR 手順が必要
- **初期設定が複雑**: クラスター初期化に証明書操作・鍵セレモニーが必要
- **AWS サービスとのネイティブ統合なし**: S3/RDS/EBS との直接統合には KMS が必要
- **専門人材が必要**: PKCS#11・暗号技術専門エンジニアの確保コストが高い

---

## 使い方

### 1. 事前準備

```bash
# Terraform 初期化
terraform init

# 変数ファイルを作成
cp terraform.tfvars.example terraform.tfvars  # 例: 下記の最小設定を参考に
```

### 2. 最小設定例（`terraform.tfvars`）

```hcl
aws_region   = "ap-northeast-1"
name_prefix  = "myapp-cloudhsm"
vpc_id       = "vpc-0abc1234567890def"

# 異なる AZ のプライベートサブネットを指定（最低2つ）
hsm_subnet_ids = [
  "subnet-0aaa111111111111a",  # ap-northeast-1a
  "subnet-0bbb222222222222b",  # ap-northeast-1c
]

common_tags = {
  Environment = "poc"
  ManagedBy   = "terraform"
}
```

### 3. デプロイ・初期化

```bash
# プランの確認
terraform plan

# デプロイ
terraform apply

# クラスター初期化手順の確認
terraform output next_steps
```

### 4. クラスター初期化（terraform apply 後）

```bash
# 1. CSR 取得
CLUSTER_ID=$(terraform output -raw cluster_id)
aws cloudhsmv2 describe-clusters --filters clusterIds=$CLUSTER_ID \
  --query 'Clusters[0].Certificates.ClusterCsr' --output text > cluster.csr

# 2. 自己署名 CA 作成（テスト用）
openssl genrsa -aes256 -out ca.key 4096
openssl req -new -x509 -days 3652 -key ca.key -out ca.crt

# 3. CSR 署名
openssl x509 -req -days 3652 -in cluster.csr -CA ca.crt \
  -CAkey ca.key -CAcreateserial -out cluster.crt

# 4. クラスター初期化
aws cloudhsmv2 initialize-cluster \
  --cluster-id $CLUSTER_ID \
  --signed-cert file://cluster.crt \
  --trust-anchor file://ca.crt
```

---

## セキュリティグループ設計

```
クライアント SG (hsm-client-sg)
    │ TCP 2223-2225 (アウトバウンド)
    ▼
クラスター自動生成 SG (cloudhsm-cluster-<id>-sg)
    │ TCP 2223-2225 (インバウンド ← クライアント SG)
    ▼
HSM ENI
```

| ポート | 用途 |
|:---:|---|
| **TCP 2223〜2225** | CloudHSM クライアント ↔ HSM ENI 通信（必須） |
| TCP 22 | SSH 管理（特定 IP のみ。`0.0.0.0/0` は禁止） |

---

## バックアップ戦略

- **自動バックアップ**: 最低 24 時間ごと + クラスターライフサイクルイベント時
- **保持期間**: デフォルト 90 日（`backup_retention_days` 変数で 7〜379 日に設定可）
- **バックアップ内容**: ユーザー・キー・証明書・設定・ポリシー
- **リージョン間コピー**: 対応済み（DR 用途）
- **復元**: `aws cloudhsmv2 create-cluster --source-backup-id` で新クラスターを復元可能

---

## KMS Custom Key Store（オプション機能）

`enable_kms_custom_key_store = true` を設定すると、KMS API 互換の CustomKey Store が作成される。

```
KMS API (使い慣れた操作感)
    │
    ▼
KMS Custom Key Store
    │ kmsuser CU 経由
    ▼
CloudHSM クラスター（シングルテナント専有 HSM）
```

**制約事項**:
- 対称暗号化キーのみ対応（非対称キー・HMAC キー不可）
- 自動キーローテーション不可
- インポートキーマテリアル不可
- マルチリージョンキー不可

---

## 関連 AWS ドキュメント

- [AWS CloudHSM ユーザーガイド](https://docs.aws.amazon.com/cloudhsm/latest/userguide/)
- [CloudHSM クラスターの初期化](https://docs.aws.amazon.com/cloudhsm/latest/userguide/initialize-cluster.html)
- [KMS Custom Key Store](https://docs.aws.amazon.com/kms/latest/developerguide/custom-key-store-overview.html)
- [Client SDK 5 インストールガイド](https://docs.aws.amazon.com/cloudhsm/latest/userguide/client-history.html)


---

📝 [Notionで詳細を見る](https://www.notion.so/HSM-35e47b55202e815ea8a0f103aab9d8f1)
