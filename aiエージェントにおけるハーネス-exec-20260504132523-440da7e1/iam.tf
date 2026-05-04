# =============================================================================
# PoC品質 (Proof of Concept)
# 本番環境での利用前に、セキュリティレビュー・詳細設定・業務ロジックの実装が必要です
# =============================================================================
#
# IAM設計方針 (最小権限の原則 / Principle of Least Privilege):
#   - ワイルドカード (s3:*, bedrock:*) は使用しない
#   - 権限は特定リソースARNと特定アクションに限定する
#   - 役割ごとにロールを分離 (Bedrock Agent / Lambda Entry / Lambda Tool)
#   - Trust Policy: そのロールを引き受けられるサービスを最小化
#
# 参考: OWASP LLM09 - 過剰な権限付与はエージェントの誤動作時の影響範囲を拡大させる
# =============================================================================

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# =============================================================================
# IAM ロール①: Bedrock Agent 実行ロール (bedrock_agent_execution_role)
# -----------------------------------------------------------------------------
# 用途: Bedrock Agent が LLM を呼び出し、Action Group (Lambda) を実行するためのロール
# Trust Policy: bedrock.amazonaws.com のみがこのロールを引き受けられる
# =============================================================================
resource "aws_iam_role" "bedrock_agent_execution_role" {
  name        = "${var.project_name}-bedrock-agent-role"
  description = "Bedrock Agent がLLM呼び出しとAction Group実行に使用するロール"

  # 信頼ポリシー: bedrock.amazonaws.com だけがこのロールを利用できる
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "bedrock.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:bedrock:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:agent/*"
          }
        }
      }
    ]
  })
}

# Bedrock Agent: LLM (基盤モデル) 呼び出し権限
# bedrock:InvokeModel: 指定したモデルIDのみに制限
resource "aws_iam_policy" "bedrock_agent_model_invoke" {
  name        = "${var.project_name}-bedrock-agent-model-invoke"
  description = "Bedrock AgentがLLMを呼び出す権限 (指定モデルのみ)"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowInvokeFoundationModel"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ]
        Resource = [
          "arn:aws:bedrock:${data.aws_region.current.name}::foundation-model/${var.foundation_model_id}"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "bedrock_agent_model_invoke" {
  role       = aws_iam_role.bedrock_agent_execution_role.name
  policy_arn = aws_iam_policy.bedrock_agent_model_invoke.arn
}

# =============================================================================
# IAM ロール②: Lambda エントリポイント実行ロール (lambda_entry_role)
# -----------------------------------------------------------------------------
# 用途: agent_entry Lambda が Bedrock Agent を呼び出すためのロール
# 権限: CloudWatch Logs書き込み + Bedrock Agent Runtime呼び出しのみ
# =============================================================================
resource "aws_iam_role" "lambda_entry_role" {
  name        = "${var.project_name}-lambda-entry-role"
  description = "agent_entry LambdaがBedrock Agentを呼び出すためのロール"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

# AWS管理ポリシー: Lambda基本実行権限 (CloudWatch Logsへの書き込みのみ)
resource "aws_iam_role_policy_attachment" "lambda_entry_basic_execution" {
  role       = aws_iam_role.lambda_entry_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Bedrock Agent Runtime 呼び出し権限 (特定エージェントのみ)
resource "aws_iam_policy" "lambda_entry_bedrock_policy" {
  name        = "${var.project_name}-lambda-entry-bedrock"
  description = "agent_entry LambdaがBedrock Agent Runtimeを呼び出す権限"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowInvokeBedrockAgent"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeAgent",
        ]
        Resource = [
          # 特定エージェントとエイリアスのみに制限
          "arn:aws:bedrock:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:agent/${aws_bedrockagent_agent.harness_agent.agent_id}",
          "arn:aws:bedrock:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:agent-alias/${aws_bedrockagent_agent.harness_agent.agent_id}/*",
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_entry_bedrock_policy" {
  role       = aws_iam_role.lambda_entry_role.name
  policy_arn = aws_iam_policy.lambda_entry_bedrock_policy.arn
}

# =============================================================================
# IAM ロール③: Lambda ツール実行ロール (lambda_tool_role)
# -----------------------------------------------------------------------------
# 用途: tool_executor Lambda が DynamoDB へのアクセスや外部ツール実行に使うロール
# 権限: CloudWatch Logs書き込み + DynamoDB 特定テーブルの読み書きのみ
#
# セキュリティ注意点:
#   tool_executor は Bedrock Agent から呼び出されるため、
#   プロンプトインジェクション攻撃に備えて権限を最小化する (OWASP LLM01対策)
# =============================================================================
resource "aws_iam_role" "lambda_tool_role" {
  name        = "${var.project_name}-lambda-tool-role"
  description = "tool_executor LambdaがDynamoDBとツール実行に使うロール"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_tool_basic_execution" {
  role       = aws_iam_role.lambda_tool_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# DynamoDB 読み書き権限 (特定テーブルのみ・ワイルドカード禁止)
resource "aws_iam_policy" "lambda_tool_dynamodb_policy" {
  name        = "${var.project_name}-lambda-tool-dynamodb"
  description = "tool_executor LambdaがセッションテーブルをCRUDする権限"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSessionTableAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          # BatchWrite: 一括操作が必要な場合のみ追加
        ]
        Resource = [
          aws_dynamodb_table.session_state.arn,
          # GSI (グローバルセカンダリインデックス) を追加した場合は /index/* も追加
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_tool_dynamodb_policy" {
  role       = aws_iam_role.lambda_tool_role.name
  policy_arn = aws_iam_policy.lambda_tool_dynamodb_policy.arn
}

# =============================================================================
# SQS DLQ アクセス権限: EventBridge がDLQにメッセージを送信するためのポリシー
# -----------------------------------------------------------------------------
# EventBridge は SQS に書き込む際に、キューのリソースベースポリシーが必要
# (IAMロールポリシーではなく、SQSキュー側にポリシーをアタッチする)
# =============================================================================
resource "aws_sqs_queue_policy" "dlq_policy" {
  queue_url = aws_sqs_queue.agent_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEventBridgeSendMessage"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.agent_dlq.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_cloudwatch_event_rule.agent_trigger.arn
          }
        }
      }
    ]
  })
}

# =============================================================================
# API Gateway CloudWatch Logs 書き込み権限
# -----------------------------------------------------------------------------
# API Gateway v2 がアクセスログを CloudWatch Logs に書き込むためのアカウントレベル設定
# 注意: このリソースはAWSアカウント単位で1つのみ存在可能
# =============================================================================
resource "aws_api_gateway_account" "api_gw_account" {
  cloudwatch_role_arn = aws_iam_role.api_gateway_cloudwatch_role.arn
}

resource "aws_iam_role" "api_gateway_cloudwatch_role" {
  name        = "${var.project_name}-apigw-cloudwatch-role"
  description = "API GatewayがCloudWatch Logsに書き込むためのロール"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "apigateway.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "api_gateway_cloudwatch" {
  role       = aws_iam_role.api_gateway_cloudwatch_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}
