## IaCコード（Terraform または CloudFormation）

# AWS S3 IaC サンプル（Terraform）

> **PoC品質**: 概念実証用スケルトンです。本番利用前に十分なセキュリティレビューを行ってください。

## 構成概要

```
┌─────────────────────────────────────────────────────┐
│  クライアント                                          │
│      │ POST /upload-url                              │
│      ▼                                               │
│  API Gateway (REST)                                  │
│      │ AWS_PROXY                                     │
│      ▼                                               │
│  Lambda: presigned-url ──► S3 GeneratePresignedUrl   │
│      │ PUT URL返却                                   │
│      ▼                                               │
│  クライアント ──────────────────────► S3 Main Bucket  │
│                  PUT (プレサインドURL)  uploads/        │
│                                           │           │
│                                    EventBridge        │
│                                           │           │
│                                           ▼           │
│                              Lambda: s3-event-processor│
│                              （サムネイル生成スケルトン） │
└─────────────────────────────────────────────────────┘
```

## 含まれる機能

| 機能 | 設定内容 |
|------|---------|
| **暗号化** | SSE-KMS + S3 Bucket Keys（KMSコスト削減） |
| **Block Public Access** | 全4設定を有効化（Security Hub S3.8準拠） |
| **バージョニング** | 有効 |
| **Object Ownership** | BucketOwnerEnforced（ACL廃止） |
| **ライフサイクル** | logs/: Standard→IA(30d)→Glacier(90d)→削除(365d)、archive/: Deep Archive即時移行 |
| **Object Lock** | オプション（GOVERNANCE/COMPLIANCE、`enable_object_lock=true`） |
| **CRR** | オプション（`enable_replication=true`） |
| **Intelligent-Tiering** | オプション（`enable_intelligent_tiering=true`） |
| **EventBridge連携** | バケット全イベントをEventBridgeへ送信 |
| **バケットポリシー** | HTTP拒否 + 署名鮮度制限（600秒） |
| **サーバーレスAPI** | Lambda + API Gateway によるプレサインドURL発行（最大5GB対応） |
| **S3イベント処理** | EventBridge → Lambda 非同期パイプライン（uploads/ プレフィックス） |

## 使い方

```bash
terraform init
terraform plan -var="project_name=myapp" -var="environment=dev"
terraform apply
```

### CRR有効化

```bash
terraform apply -var="enable_replication=true"
```

### Object Lock有効化（注意: 作成後変更不可）

```bash
terraform apply -var="enable_object_lock=true" -var="object_lock_mode=GOVERNANCE"
```

## ファイル構成

| ファイル | 内容 |
|---------|------|
| `main.tf` | S3バケット本体、KMS、IAM、CRR |
| `variables.tf` | 入力変数定義 |
| `outputs.tf` | 出力値 |
| `serverless.tf` | Lambda + API Gateway（プレサインドURL API・イベント処理） |

## 注意事項

- `object_lock_enabled` はバケット作成時のみ設定可能です（後から変更不可）
- `enable_replication=true` にする場合、レプリカリージョンのKMSキーも別途作成が必要です
- `serverless.tf` 内のLambdaコードはインラインPoC実装です。本番では依存ライブラリ（Pillow等）を含むデプロイパッケージに置き換えてください
- API Gatewayの認証は `AWS_IAM` を設定しています。用途に応じてCognitoやAPIキーへ変更してください


---

📝 [Notionで詳細を見る]()
