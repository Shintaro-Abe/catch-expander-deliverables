## IaCコード（Terraform または CloudFormation）

# 標準AWS2.0 IaC スケルトン（Terraform）

> **PoC品質** — 本番環境への適用前に、必ずセキュリティレビューと適切なカスタマイズを行ってください。

---

## はじめに（要約）

本コードは「標準AWS2.0」の設計思想を Terraform で実装したスケルトンです。
2025〜2026年の AWS Well-Architected Framework 大規模更新・re:Invent 2025 の発表内容（ゼロトラスト・マルチアカウントガバナンス・GenAI基盤）を踏まえた構成です。

**一言でいうと：** 「セキュアなマルチアカウント基盤 ＋ Bedrock RAGパイプライン」を IaC でゼロから構築するためのテンプレートです。

---

## アーキテクチャ全体図

```
┌─────────────────────────────────────────────────────────────────┐
│  AWS Organizations（管理アカウント）                              │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ SCP ガードレール（組織全体に適用）                          │   │
│  │   ✘ 組織離脱禁止          ✘ 承認外リージョン禁止           │   │
│  │   ✘ rootユーザー使用禁止  ✘ GuardDuty停止禁止             │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ RCP ガードレール（リソース視点）                            │   │
│  │   ✘ S3非TLSアクセス禁止（全バケット対象）                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ IAM Identity Center（人的アクセスを一元管理）               │   │
│  │   ReadOnly (8h) │ Operator (4h) │ Admin (1h) │ SecAdm (1h) │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  ワークロードアカウント（dev / staging / prod）                    │
│                                                                 │
│  ┌── VPC (10.0.0.0/16) ──────────────────────────────────────┐  │
│  │  Public  /24×3AZ  → ALB, NAT Gateway                      │  │
│  │  Private /24×3AZ  → App, Lambda                           │  │
│  │  Intra   /24×3AZ  → DB, VPC Endpoints ←────────────────┐  │  │
│  │                          ├── S3 Gateway Endpoint          │  │  │
│  │                          ├── bedrock-runtime Interface     │  │  │
│  │                          └── bedrock-agent-runtime Intfc   │  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  KMS CMK（全リソース暗号化）  CloudTrail（全API操作の証跡記録）     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  GenAI / RAG パイプライン                                         │
│                                                                 │
│  [ドキュメント]                                                   │
│      ↓ アップロード                                               │
│  S3 Bucket（KMS暗号化・バージョニング・ライフサイクル）              │
│      ↓ 自動同期（Ingestion Job）                                  │
│  Bedrock Data Source                                            │
│      ↓ チャンキング（HIERARCHICAL: 親1500 / 子300 tokens）         │
│  Bedrock Knowledge Base                                         │
│      ↓ 埋め込み（Titan Embed Text v2: 1024次元）                  │
│  OpenSearch Serverless（VECTORSEARCH）                           │
│      ↓ k-NN 類似度検索                                           │
│  RetrieveAndGenerate API → Claude（コンテキスト注入）             │
│                         ↑                                       │
│  Bedrock Guardrails（PII匿名化・有害コンテンツ・プロンプト攻撃防御） │
└─────────────────────────────────────────────────────────────────┘
```

---

## ファイル構成

| ファイル | 役割 |
|---------|------|
| `variables.tf` | 全入力変数の定義・バリデーション |
| `main.tf` | Terraformプロバイダー設定・KMS CMK・VPC（3層分離）・VPCエンドポイント・CloudTrail |
| `iam_identity_center.tf` | IAM Identity Center パーミッションセット（4種）・SCP ガードレール・RCP ガードレール |
| `bedrock_rag.tf` | S3ドキュメントストア・OpenSearch Serverless・Bedrock Knowledge Base・Guardrails |

---

## 前提条件

| 要件 | 詳細 |
|------|------|
| Terraform | `>= 1.9.0` |
| AWS Provider | `~> 5.80`（RCPサポートには v5.60以降が必要） |
| AWS Organizations | 有効化済み・管理アカウントから実行 |
| IAM Identity Center | 有効化済み（東京リージョン） |
| Bedrock モデルアクセス | `amazon.titan-embed-text-v2:0` を有効化済み |
| terraform-aws-modules/vpc | v5.16以降（VPCモジュール） |

---

## 使い方

### ステップ1: 初期化

```bash
terraform init
```

### ステップ2: 実行計画の確認

```bash
terraform plan \
  -var="environment=dev" \
  -var="root_ou_id=r-xxxx"   # OrganizationsのルートOU ID
```

### ステップ3: デプロイ

```bash
terraform apply \
  -var="environment=dev" \
  -var="root_ou_id=r-xxxx"
```

### ステップ4: OpenSearch ベクトルインデックスの手動作成

> **Terraform はOpenSearchのインデックス作成に非対応のため、この手順は必須です。**

```bash
# Terraform OutputからエンドポイントURLを取得
ENDPOINT=$(terraform output -raw opensearch_collection_endpoint)

# ベクトルインデックスを作成（k-NN + Faiss エンジン）
curl -XPUT "${ENDPOINT}/bedrock-knowledge-base-default-index" \
  -H "Content-Type: application/json" \
  --aws-sigv4 "aws:amz:${AWS_REGION}:aoss" \
  -d '{
    "settings": { "index.knn": true },
    "mappings": {
      "properties": {
        "bedrock-knowledge-base-default-vector": {
          "type": "knn_vector",
          "dimension": 1024,
          "method": { "engine": "faiss", "name": "hnsw" }
        },
        "AMAZON_BEDROCK_TEXT_CHUNK": { "type": "text" },
        "AMAZON_BEDROCK_METADATA":   { "type": "text" }
      }
    }
  }'
```

### ステップ5: RAG の動作確認（Python）

```python
import boto3

client = boto3.client("bedrock-agent-runtime", region_name="ap-northeast-1")

response = client.retrieve_and_generate(
    input={"text": "社内の有給申請手順を教えてください"},
    retrieveAndGenerateConfiguration={
        "type": "KNOWLEDGE_BASE",
        "knowledgeBaseConfiguration": {
            "knowledgeBaseId": "<terraform output knowledge_base_id>",
            "modelArn": "arn:aws:bedrock:ap-northeast-1::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0",
            "generationConfiguration": {
                "guardrailConfiguration": {
                    "guardrailId": "<terraform output guardrail_id>",
                    "guardrailVersion": "<terraform output guardrail_version>"
                }
            }
        }
    }
)

print(response["output"]["text"])
```

---

## 設計のメリット・デメリット

### メリット

| 項目 | 内容 |
|------|------|
| **ゼロトラスト基盤** | VPC 3層分離 + VPCエンドポイントで、データがインターネットを経由しない |
| **ガバナンス一元化** | SCP/RCPで組織全体のセキュリティ不変条件をコード管理（人的ミスを防止） |
| **アクセス一元管理** | Identity Centerによりアカウント横断の人的アクセスを単一IdPで管理 |
| **GenAI即時利用** | Bedrock Knowledge BaseでRAGパイプラインをサーバーレス・フルマネージドで構築 |
| **監査完全性** | CloudTrail + KMS CMKで全操作を暗号化・追跡可能（コンプライアンス対応） |
| **FinOps対応** | `default_tags` による全リソースへの自動タグ付けでコスト配分を可視化 |

### デメリット・制約

| 項目 | 内容 |
|------|------|
| **Organizations必須** | 単一アカウント環境ではSCP/RCPは利用不可 |
| **OpenSearchの固定費** | 最小2 OCU ≈ $350〜/月（少量データのPoCにはコストが重い） |
| **インデックス手動作成** | OpenSearchベクトルインデックスはTerraform非対応 → 別途スクリプトが必要 |
| **東京のモデル制限** | 利用可能なBedrockモデルがus-east-1より少ない（2025年時点） |
| **RCPのサービス範囲** | S3・STS・KMS・Secrets Manager・SQSのみ対応（2025年時点） |
| **NAT GWコスト** | 本番AZ分散構成では3台 ≈ $135/月 + データ転送量 |

---

## コスト試算（東京リージョン / 月額概算）

| サービス | 費用目安 | 備考 |
|---------|---------|------|
| OpenSearch Serverless | ~$350〜/月 | 最小2 OCU（最大のコスト要因） |
| NAT Gateway（dev: 1台） | ~$45/月 | 本番3台: ~$135/月 |
| VPCエンドポイント（Interface × 3） | ~$30/月 | AZ数 × エンドポイント数で変動 |
| CloudTrail | 無料〜 | 最初の管理イベントトレイルは無料 |
| Bedrock Titan Embed v2 | $0.00002/1K tokens | 取り込みドキュメント量による |

> **PoC コスト削減ヒント:**
> OpenSearch Serverless の代わりに **Aurora PostgreSQL + pgvector** を使用すると、
> 少量データでは月額数ドル〜数十ドルに抑えられます。
> `storage_configuration` の `type` を `"RDS"` に変更するだけで切り替え可能です。

---

## 関連ドキュメント

- [AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/)
- [Bedrock Knowledge Bases ドキュメント](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html)
- [IAM Identity Center ベストプラクティス](https://docs.aws.amazon.com/singlesignon/latest/userguide/best-practices.html)
- [SCP と RCP の違い（AWS公式）](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html)
- [terraform-aws-modules/vpc](https://github.com/terraform-aws-modules/terraform-aws-vpc)


---

📝 [Notionで詳細を見る]()
