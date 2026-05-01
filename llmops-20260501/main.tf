# PoC品質 - 本番環境での使用前に十分なセキュリティレビューが必要です
# LLMOps基盤インフラ: S3 / IAM / OpenSearch Serverless / Step Functions を定義します

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # default_tags = 全リソースに共通タグを自動付与する仕組み（コスト配賦・管理に有効）
  default_tags {
    tags = merge(
      {
        Project     = var.project_name
        Environment = var.environment
        ManagedBy   = "Terraform"
        Purpose     = "LLMOps"
      },
      var.tags
    )
  }
}

locals {
  name_prefix = "${var.project_name}-${var.environment}"
  account_id  = data.aws_caller_identity.current.account_id
}

data "aws_caller_identity" "current" {}

# ═══════════════════════════════════════════════════════════════════════════════
# S3 バケット群
# LLMOpsでは「データセット」「プロンプトテンプレート」「モデルアーティファクト」を
# 用途ごとに分けて管理することで、バージョン管理・アクセス制御・コスト分析が容易になります
# ═══════════════════════════════════════════════════════════════════════════════

module "s3_datasets" {
  source      = "./modules/s3_bucket"
  bucket_name = "${local.name_prefix}-datasets-${local.account_id}"
}

module "s3_prompts" {
  source      = "./modules/s3_bucket"
  bucket_name = "${local.name_prefix}-prompts-${local.account_id}"
}

module "s3_artifacts" {
  source      = "./modules/s3_bucket"
  bucket_name = "${local.name_prefix}-artifacts-${local.account_id}"
}

# ─── S3バケットモジュール（インライン定義） ──────────────────────────────────────
# Terraform標準ではmoduleブロックには別ディレクトリが必要なため、
# PoC用にルートモジュールへ直接リソースとして展開します

resource "aws_s3_bucket" "datasets" {
  bucket = "${local.name_prefix}-datasets-${local.account_id}"
}

resource "aws_s3_bucket" "prompts" {
  bucket = "${local.name_prefix}-prompts-${local.account_id}"
}

resource "aws_s3_bucket" "artifacts" {
  bucket = "${local.name_prefix}-artifacts-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "datasets" {
  bucket = aws_s3_bucket.datasets.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_versioning" "prompts" {
  bucket = aws_s3_bucket.prompts.id
  # バージョニング有効化 = プロンプトの変更履歴を保持し、ロールバック可能にします
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

# 全バケットでサーバーサイド暗号化を有効化
resource "aws_s3_bucket_server_side_encryption_configuration" "datasets" {
  bucket = aws_s3_bucket.datasets.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "AES256" } }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "prompts" {
  bucket = aws_s3_bucket.prompts.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "AES256" } }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "AES256" } }
}

# パブリックアクセスブロック（全バケット共通）
resource "aws_s3_bucket_public_access_block" "datasets" {
  bucket                  = aws_s3_bucket.datasets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "prompts" {
  bucket                  = aws_s3_bucket.prompts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ═══════════════════════════════════════════════════════════════════════════════
# IAM ロール
# 最小権限の原則に従い、各サービスが必要な操作のみを許可します
# ═══════════════════════════════════════════════════════════════════════════════

resource "aws_iam_role" "lambda_execution" {
  name = "${local.name_prefix}-lambda-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Lambda に Bedrock・S3・OpenSearch へのアクセス権を付与
resource "aws_iam_role_policy" "lambda_llmops" {
  name = "${local.name_prefix}-lambda-llmops"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockInference"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        # 特定モデルのみに限定することでセキュリティを強化
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}"
      },
      {
        Sid    = "S3PromptAccess"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject"]
        Resource = [
          "${aws_s3_bucket.prompts.arn}/*",
          "${aws_s3_bucket.datasets.arn}/*"
        ]
      },
      {
        Sid      = "OpenSearchServerlessAccess"
        Effect   = "Allow"
        Action   = ["aoss:APIAccessAll"]
        Resource = aws_opensearchserverless_collection.vector_store.arn
      },
      {
        Sid    = "XRayTracing"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role" "step_functions" {
  name = "${local.name_prefix}-step-functions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "step_functions_policy" {
  name = "${local.name_prefix}-step-functions-policy"
  role = aws_iam_role.step_functions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "LambdaInvoke"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "arn:aws:lambda:${var.aws_region}:${local.account_id}:function:${local.name_prefix}-*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      },
      {
        Sid    = "XRayTracing"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets"
        ]
        Resource = "*"
      }
    ]
  })
}

# ═══════════════════════════════════════════════════════════════════════════════
# OpenSearch Serverless（ベクトルストア）
#
# ベクトルDB とは：テキストを高次元の数値ベクトルに変換（埋め込み）し、
# 意味的に近いドキュメントを高速で検索するデータベースです。
# RAG（Retrieval-Augmented Generation）の中核コンポーネントとして、
# LLMが最新情報・社内情報を参照して回答精度を向上させるために使います。
# ═══════════════════════════════════════════════════════════════════════════════

resource "aws_opensearchserverless_encryption_policy" "vector_store" {
  name = "${local.name_prefix}-vec-enc"
  type = "encryption"
  policy = jsonencode({
    Rules = [{
      Resource     = ["collection/${local.name_prefix}-vectors"]
      ResourceType = "collection"
    }]
    AWSOwnedKey = true
  })
}

# PoC: パブリックアクセスを許可。本番環境ではVPCエンドポイントへ変更してください
resource "aws_opensearchserverless_network_policy" "vector_store" {
  name = "${local.name_prefix}-vec-net"
  type = "network"
  policy = jsonencode([{
    Rules = [
      {
        Resource     = ["collection/${local.name_prefix}-vectors"]
        ResourceType = "config"
      },
      {
        Resource     = ["collection/${local.name_prefix}-vectors"]
        ResourceType = "dashboard"
      }
    ]
    AllowFromPublic = true
  }])
}

resource "aws_opensearchserverless_access_policy" "vector_store" {
  name = "${local.name_prefix}-vec-access"
  type = "data"
  policy = jsonencode([{
    Rules = [
      {
        Resource = ["collection/${local.name_prefix}-vectors"]
        Permission = [
          "aoss:CreateCollectionItems",
          "aoss:DeleteCollectionItems",
          "aoss:UpdateCollectionItems",
          "aoss:DescribeCollectionItems"
        ]
        ResourceType = "collection"
      },
      {
        Resource = ["index/${local.name_prefix}-vectors/*"]
        Permission = [
          "aoss:CreateIndex",
          "aoss:DeleteIndex",
          "aoss:UpdateIndex",
          "aoss:DescribeIndex",
          "aoss:ReadDocument",
          "aoss:WriteDocument"
        ]
        ResourceType = "index"
      }
    ]
    Principal = [aws_iam_role.lambda_execution.arn]
  }])
}

resource "aws_opensearchserverless_collection" "vector_store" {
  name = "${local.name_prefix}-vectors"
  type = "VECTORSEARCH"

  depends_on = [
    aws_opensearchserverless_encryption_policy.vector_store,
    aws_opensearchserverless_network_policy.vector_store,
    aws_opensearchserverless_access_policy.vector_store
  ]
}

# ═══════════════════════════════════════════════════════════════════════════════
# AWS Step Functions: LLMパイプライン
#
# Step Functions = AWSのサーバーレスオーケストレーションサービス。
# 複数のステップ（検索→推論→評価）を視覚的に定義・管理できます。
# エラーハンドリング・リトライ・並列処理を宣言的に記述できるため、
# LLMパイプラインの信頼性向上に適しています。
#
# パイプライン構成:
#   [入力検証] → [RAG検索] → [Bedrock推論] → [品質評価] → [閾値判定] → [完了]
# ═══════════════════════════════════════════════════════════════════════════════

resource "aws_cloudwatch_log_group" "step_functions" {
  name              = "/aws/states/${local.name_prefix}-llm-pipeline"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "llm_pipeline" {
  name     = "${local.name_prefix}-llm-pipeline"
  role_arn = aws_iam_role.step_functions.arn

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  definition = jsonencode({
    Comment = "LLMOpsパイプライン: 入力検証 → RAG検索 → Bedrock推論 → 品質評価"
    StartAt = "ValidateInput"
    States = {

      # ステップ1: 入力バリデーション
      ValidateInput = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = "${aws_lambda_function.inference.arn}:$LATEST"
          Payload = {
            "action.$" = "States.Format('validate')"
            "input.$"  = "$"
          }
        }
        ResultPath = "$.validation"
        Next       = "RetrieveContext"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "HandleError"
          ResultPath  = "$.error"
        }]
      }

      # ステップ2: RAG検索（関連ドキュメントをベクトルDBから取得）
      RetrieveContext = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = "${aws_lambda_function.inference.arn}:$LATEST"
          Payload = {
            "action"  = "retrieve"
            "query.$" = "$.query"
            "top_k"   = 5
          }
        }
        ResultPath = "$.context"
        Next       = "InvokeBedrockModel"
        # リトライ設定: 指数バックオフで最大3回リトライ
        Retry = [{
          ErrorEquals     = ["States.TaskFailed", "Lambda.ServiceException"]
          IntervalSeconds = 2
          MaxAttempts     = 3
          BackoffRate     = 2.0
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "HandleError"
          ResultPath  = "$.error"
        }]
      }

      # ステップ3: Amazon Bedrock 推論
      InvokeBedrockModel = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = "${aws_lambda_function.inference.arn}:$LATEST"
          Payload = {
            "action"     = "infer"
            "query.$"    = "$.query"
            "context.$"  = "$.context"
            "model_id"   = var.bedrock_model_id
          }
        }
        ResultPath = "$.llm_response"
        Next       = "EvaluateResponse"
        # ThrottlingException = Bedrockのレート制限に引っかかった場合のリトライ
        Retry = [{
          ErrorEquals     = ["States.TaskFailed", "ThrottlingException"]
          IntervalSeconds = 5
          MaxAttempts     = 3
          BackoffRate     = 2.0
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "HandleError"
          ResultPath  = "$.error"
        }]
      }

      # ステップ4: LLM-as-Judge による品質評価
      # LLM-as-Judge = 別のLLMを使ってレスポンスの品質（忠実性・関連性）を自動採点する手法
      EvaluateResponse = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = "${aws_lambda_function.inference.arn}:$LATEST"
          Payload = {
            "action"      = "evaluate"
            "query.$"     = "$.query"
            "response.$"  = "$.llm_response"
            "context.$"   = "$.context"
          }
        }
        ResultPath = "$.evaluation"
        Next       = "CheckQualityThreshold"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          # 評価に失敗してもレスポンス自体は返す（サービス継続性を優先）
          Next       = "ReturnResponse"
          ResultPath = "$.eval_error"
        }]
      }

      # ステップ5: 品質スコアの閾値判定
      CheckQualityThreshold = {
        Type = "Choice"
        Choices = [{
          Variable                 = "$.evaluation.Payload.quality_score"
          NumericGreaterThanEquals = var.quality_score_threshold
          Next                     = "ReturnResponse"
        }]
        Default = "LogLowQuality"
      }

      # 品質閾値を下回った場合：ログに記録してからレスポンスを返す
      LogLowQuality = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = "${aws_lambda_function.inference.arn}:$LATEST"
          Payload = {
            "action"        = "log_low_quality"
            "evaluation.$"  = "$.evaluation"
            "query.$"       = "$.query"
          }
        }
        Next = "ReturnResponse"
      }

      ReturnResponse = {
        Type = "Succeed"
      }

      HandleError = {
        Type  = "Fail"
        Error = "LLMPipelineError"
        Cause = "LLMパイプライン処理中にエラーが発生しました"
      }
    }
  })
}

# ═══════════════════════════════════════════════════════════════════════════════
# 出力値（terraform output で確認できます）
# ═══════════════════════════════════════════════════════════════════════════════

output "s3_datasets_bucket" {
  description = "データセット保存S3バケット名"
  value       = aws_s3_bucket.datasets.bucket
}

output "s3_prompts_bucket" {
  description = "プロンプトテンプレート保存S3バケット名"
  value       = aws_s3_bucket.prompts.bucket
}

output "s3_artifacts_bucket" {
  description = "モデルアーティファクト保存S3バケット名"
  value       = aws_s3_bucket.artifacts.bucket
}

output "opensearch_endpoint" {
  description = "OpenSearch Serverlessコレクションエンドポイント（ベクトルDB接続先）"
  value       = aws_opensearchserverless_collection.vector_store.collection_endpoint
}

output "step_functions_arn" {
  description = "LLMパイプライン Step Functions ARN"
  value       = aws_sfn_state_machine.llm_pipeline.arn
}

output "lambda_execution_role_arn" {
  description = "Lambda実行ロールARN"
  value       = aws_iam_role.lambda_execution.arn
}
