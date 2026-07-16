# PoC品質 — 本番環境への適用前に、必ずセキュリティレビューと適切な設定変更を行ってください
# 標準AWS2.0: Bedrock Knowledge Base + RAG パイプライン + Guardrails
#
# アーキテクチャ概要:
#   S3（ドキュメント格納）
#     → Bedrock Data Source（自動取り込み）
#     → Bedrock Knowledge Base（チャンキング・埋め込み）
#     → OpenSearch Serverless（ベクトル検索）
#     → RetrieveAndGenerate API（Claudeへのコンテキスト注入）
#   Guardrails でプロンプト・レスポンスを安全フィルタリング

# ====================================================
# S3バケット: RAGドキュメント格納
# PDF・テキスト・HTML・Markdown などを格納
# ====================================================
resource "aws_s3_bucket" "knowledge_base" {
  bucket        = "${var.project_name}-kb-docs-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.environment != "prod"
}

resource "aws_s3_bucket_versioning" "knowledge_base" {
  bucket = aws_s3_bucket.knowledge_base.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "knowledge_base" {
  bucket = aws_s3_bucket.knowledge_base.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "knowledge_base" {
  bucket                  = aws_s3_bucket.knowledge_base.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ライフサイクルポリシー: 旧バージョンを自動アーカイブ・削除（コスト最適化）
resource "aws_s3_bucket_lifecycle_configuration" "knowledge_base" {
  bucket = aws_s3_bucket.knowledge_base.id

  rule {
    id     = "archive-and-expire-noncurrent"
    status = "Enabled"
    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "STANDARD_IA"  # 30日後: アクセス頻度低いバージョンをIA移行
    }
    noncurrent_version_expiration {
      noncurrent_days = 365  # 1年後: 旧バージョン自動削除
    }
  }
}

# ====================================================
# OpenSearch Serverless: ベクトルデータベース
#
# Bedrock Knowledge Baseのデフォルト推奨ベクトルストア
# サーバーレスのためキャパシティ計画・パッチ管理が不要
#
# ⚠️ コスト注意: 最小2 OCU（≈$350〜/月）の固定費が発生
#    少量データのPoCではAurora PostgreSQL (pgvector) が安価な代替
# ====================================================

# 暗号化ポリシー（コレクション作成前に必須）
resource "aws_opensearchserverless_encryption_policy" "rag" {
  name        = "${var.opensearch_collection_name}-enc"
  type        = "encryption"
  description = "Bedrock RAG コレクション暗号化ポリシー（AWSマネージドキー使用）"

  policy = jsonencode({
    Rules = [{
      ResourceType = "collection"
      Resource     = ["collection/${var.opensearch_collection_name}"]
    }]
    AWSOwnedKey = true
    # CMK使用の場合（より高いセキュリティ要件向け）:
    # AWSOwnedKey = false
    # KmsARN = aws_kms_key.main.arn
  })
}

# ネットワークポリシー
# PoC: パブリックアクセスを許可（セットアップ簡易化）
# 本番: AllowFromPublic = false + SourceVPCEs にVPCエンドポイントIDを指定
resource "aws_opensearchserverless_network_policy" "rag" {
  name        = "${var.opensearch_collection_name}-net"
  type        = "network"
  description = "Bedrock RAG コレクション ネットワークポリシー（PoC: パブリック許可）"

  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.opensearch_collection_name}"]
      },
      {
        ResourceType = "dashboard"
        Resource     = ["collection/${var.opensearch_collection_name}"]
      }
    ]
    AllowFromPublic = true
    # 本番環境ではパブリックアクセスを無効化:
    # AllowFromPublic = false
    # SourceVPCEs = ["vpce-xxxxxxxxxx"]
  }])
}

# データアクセスポリシー: BedrockサービスロールへのCRUD権限
resource "aws_opensearchserverless_access_policy" "rag" {
  name        = "${var.opensearch_collection_name}-access"
  type        = "data"
  description = "Bedrock Knowledge Base 実行ロールからのコレクションアクセス"

  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "index"
        Resource     = ["index/${var.opensearch_collection_name}/*"]
        Permission = [
          "aoss:CreateIndex", "aoss:DeleteIndex",
          "aoss:UpdateIndex", "aoss:DescribeIndex",
          "aoss:ReadDocument", "aoss:WriteDocument"
        ]
      },
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.opensearch_collection_name}"]
        Permission   = ["aoss:CreateCollectionItems", "aoss:DescribeCollectionItems"]
      }
    ]
    Principal = [aws_iam_role.bedrock_kb.arn]
  }])
}

# OpenSearch Serverless コレクション本体（VECTORSEARCH タイプ）
resource "aws_opensearchserverless_collection" "rag" {
  name        = var.opensearch_collection_name
  type        = "VECTORSEARCH"
  description = "Bedrock Knowledge Base 用ベクトル検索コレクション"

  # ポリシーを先に作成してからコレクションを作成（依存関係）
  depends_on = [
    aws_opensearchserverless_encryption_policy.rag,
    aws_opensearchserverless_network_policy.rag,
    aws_opensearchserverless_access_policy.rag
  ]
}

# ====================================================
# IAMロール: Bedrock Knowledge Base 実行ロール
#
# 信頼ポリシーのConditionで「このアカウントのKBのみ」に制限
# （confused deputy問題への対策）
# ====================================================
data "aws_iam_policy_document" "bedrock_kb_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
    # confused deputy 対策: ソースアカウントとリソースARNで制限
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:knowledge-base/*"]
    }
  }
}

resource "aws_iam_role" "bedrock_kb" {
  name               = "${var.project_name}-bedrock-kb-role"
  assume_role_policy = data.aws_iam_policy_document.bedrock_kb_assume.json
  description        = "Bedrock Knowledge Base 実行ロール（最小権限）"
}

resource "aws_iam_role_policy" "bedrock_kb" {
  name = "${var.project_name}-bedrock-kb-policy"
  role = aws_iam_role.bedrock_kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BedrockEmbeddingModelInvoke"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = [var.bedrock_embedding_model_arn]
      },
      {
        Sid    = "S3DocumentAccess"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.knowledge_base.arn,
          "${aws_s3_bucket.knowledge_base.arn}/*"
        ]
      },
      {
        Sid      = "OpenSearchServerlessAccess"
        Effect   = "Allow"
        Action   = ["aoss:APIAccessAll"]
        Resource = [aws_opensearchserverless_collection.rag.arn]
      },
      {
        Sid    = "KMSForS3Decryption"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.main.arn]
        Condition = {
          StringEquals = {
            "kms:ViaService" = "s3.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

# ====================================================
# Bedrock Knowledge Base
#
# ポイント:
#   - type = "VECTOR" でセマンティック検索（意味検索）を実現
#   - embedding_model_arn でテキストをベクトルに変換するモデルを指定
#   - vector_index_name は次のステップで手動作成が必要（Terraform非対応）
#
# ⚠️ 重要: コレクション作成後、OpenSearch APIでベクトルインデックスを
#    手動作成してからこのリソースを apply してください（README参照）
# ====================================================
resource "aws_bedrockagent_knowledge_base" "main" {
  name        = var.bedrock_kb_name
  role_arn    = aws_iam_role.bedrock_kb.arn
  description = "標準AWS2.0 RAGナレッジベース — 社内ドキュメント・技術仕様書の意味検索基盤"

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = var.bedrock_embedding_model_arn
    }
  }

  storage_configuration {
    type = "OPENSEARCH_SERVERLESS"
    opensearch_serverless_configuration {
      collection_arn    = aws_opensearchserverless_collection.rag.arn
      vector_index_name = "bedrock-knowledge-base-default-index"

      field_mapping {
        vector_field   = "bedrock-knowledge-base-default-vector"
        text_field     = "AMAZON_BEDROCK_TEXT_CHUNK"
        metadata_field = "AMAZON_BEDROCK_METADATA"
      }
    }
  }

  depends_on = [
    aws_iam_role_policy.bedrock_kb,
    aws_opensearchserverless_collection.rag
  ]
}

# ====================================================
# Bedrock Data Source: S3からドキュメントを自動取り込み
#
# チャンキング戦略の選択肢:
#   FIXED_SIZE   : シンプル・高速（PoC向け）
#   SEMANTIC     : 意味境界でチャンク分割（高精度）
#   HIERARCHICAL : 親子チャンク構造（コンテキスト保持と検索精度を両立）← 推奨
# ====================================================
resource "aws_bedrockagent_data_source" "s3" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.main.id
  name              = "${var.project_name}-s3-datasource"
  description       = "S3バケット内ドキュメントのデータソース（PDF・テキスト・HTML対応）"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn = aws_s3_bucket.knowledge_base.arn
      # 特定プレフィックスのみ取り込む場合（本番環境でのディレクトリ分離に推奨）:
      # inclusion_prefixes = ["documents/", "manuals/", "policies/"]
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      # HIERARCHICAL: 親チャンク（広いコンテキスト）と子チャンク（高精度検索）を併用
      chunking_strategy = "HIERARCHICAL"
      hierarchical_chunking_configuration {
        level_configuration {
          max_token_count = 1500  # 親チャンク: 文書の広いコンテキストを保持
        }
        level_configuration {
          max_token_count = 300   # 子チャンク: 精緻な検索精度のための細分化
        }
        overlap_tokens = 60       # チャンク間のオーバーラップ（文脈の連続性を確保）
      }
    }
  }
}

# ====================================================
# Bedrock Guardrails: コンテンツ安全フィルター
#
# GenAI本番運用において必須のコンテンツポリシー実装
# 入力（プロンプト）と出力（レスポンス）の両方でフィルタリング
# Knowledge Baseの RetrieveAndGenerate API呼び出し時に自動適用可能
# ====================================================
resource "aws_bedrock_guardrail" "main" {
  name                      = "${var.project_name}-${var.environment}-guardrail"
  description               = "標準AWS2.0 コンテンツ安全ガードレール（有害コンテンツ・PII・プロンプトインジェクション防御）"
  blocked_input_messaging   = "ご質問の内容はお答えできません。内容を変更して再度お試しください。"
  blocked_outputs_messaging = "このコンテンツはポリシーにより表示できません。管理者にお問い合わせください。"

  # 有害コンテンツフィルター（入出力の強度を個別設定）
  content_policy_config {
    filters_config {
      type            = "SEXUAL"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "MEDIUM"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "INSULTS"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = "MEDIUM"
      output_strength = "HIGH"
    }
    filters_config {
      # プロンプトインジェクション（悪意のある命令の注入）を入力側でブロック
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  # PII（個人識別情報）の検出と自動匿名化
  sensitive_information_policy_config {
    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"  # メールアドレスをマスク
    }
    pii_entities_config {
      type   = "PHONE"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "NAME"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "ADDRESS"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "CREDIT_DEBIT_CARD_NUMBER"
      action = "BLOCK"  # クレジットカード番号は完全ブロック
    }
    pii_entities_config {
      type   = "AWS_ACCESS_KEY"
      action = "BLOCK"  # AWSアクセスキーの漏洩を防止
    }
  }

  # 業務外トピックの拒否設定
  topic_policy_config {
    topics_config {
      name       = "InvestmentAdvice"
      definition = "株式・仮想通貨・不動産などの具体的な投資助言・銘柄推奨"
      examples   = ["この株を今買うべきですか", "おすすめの投資先を教えて"]
      type       = "DENY"
    }
    topics_config {
      name       = "CompetitorBashing"
      definition = "特定の競合他社製品への否定的な比較・批判"
      examples   = ["他社サービスの欠点を教えて"]
      type       = "DENY"
    }
  }
}

# Guardrailsのバージョン固定（Knowledge Baseへの参照はバージョン指定が推奨）
resource "aws_bedrock_guardrail_version" "main" {
  guardrail_id = aws_bedrock_guardrail.main.guardrail_id
  description  = "初期リリース版"
}

# ====================================================
# 出力: アプリケーションコードから参照する値
# ====================================================
output "knowledge_base_id" {
  description = "Bedrock Knowledge Base ID（RetrieveAndGenerate API呼び出しに使用）"
  value       = aws_bedrockagent_knowledge_base.main.id
}

output "knowledge_base_arn" {
  description = "Bedrock Knowledge Base ARN"
  value       = aws_bedrockagent_knowledge_base.main.arn
}

output "guardrail_id" {
  description = "Bedrock Guardrail ID（API呼び出し時の safeguard に使用）"
  value       = aws_bedrock_guardrail.main.guardrail_id
}

output "guardrail_version" {
  description = "Bedrock Guardrail バージョン（固定バージョン指定推奨）"
  value       = aws_bedrock_guardrail_version.main.version
}

output "knowledge_base_s3_bucket" {
  description = "ナレッジベース ドキュメント格納S3バケット名"
  value       = aws_s3_bucket.knowledge_base.id
}

output "opensearch_collection_endpoint" {
  description = "OpenSearch Serverless エンドポイント（ベクトルインデックス手動作成に使用）"
  value       = aws_opensearchserverless_collection.rag.collection_endpoint
}
