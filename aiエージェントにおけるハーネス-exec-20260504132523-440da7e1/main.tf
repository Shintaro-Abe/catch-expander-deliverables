# =============================================================================
# PoC品質 (Proof of Concept)
# 本番環境での利用前に、セキュリティレビュー・詳細設定・業務ロジックの実装が必要です
# =============================================================================

# -----------------------------------------------------------------------------
# Terraform 設定ブロック
# required_providers: 使用するプロバイダー（AWSリソースの操作ライブラリ）を宣言
# archive: Lambdaデプロイ用のzipファイルを生成するプロバイダー
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# AWSプロバイダー設定
# 認証情報: 環境変数 (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY) または
# IAMロール (EC2/ECSインスタンスプロファイル) で設定してください
# ハードコードは絶対禁止 (セキュリティリスク)
# -----------------------------------------------------------------------------
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = var.common_tags
  }
}

# =============================================================================
# 変数定義 (Variables)
# terraform apply -var="project_name=myagent" のように上書き可能
# =============================================================================

variable "aws_region" {
  description = "デプロイ先のAWSリージョン"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "リソース名のプレフィックス (例: myagent-harness)"
  type        = string
  default     = "ai-agent-harness"
}

# foundation_model_id: 利用するLLM（大規模言語モデル）のID
# Bedrockで利用可能なモデル一覧: aws bedrock list-foundation-models
variable "foundation_model_id" {
  description = "Bedrock Agentが使用するLLMのモデルID"
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
}

variable "agent_instruction" {
  description = "エージェントへのシステムプロンプト（行動指針・ペルソナ定義）"
  type        = string
  default     = "あなたはタスクを自律的に完遂するAIエージェントです。与えられたツールを適切に使用し、ユーザーの要求を段階的に解決してください。不明な点があれば確認し、ツール実行結果を踏まえて次のアクションを決定してください。"
}

variable "session_ttl_seconds" {
  description = "エージェントセッションのアイドルタイムアウト（秒）"
  type        = number
  default     = 1800 # 30分
}

variable "log_retention_days" {
  description = "CloudWatchログの保存期間（日数）"
  type        = number
  default     = 30
}

variable "log_level" {
  description = "Lambdaのログレベル (DEBUG / INFO / WARNING / ERROR)"
  type        = string
  default     = "INFO"
}

variable "enable_pitr" {
  description = "DynamoDBのポイントインタイムリカバリを有効化（本番では true 推奨）"
  type        = bool
  default     = false
}

variable "error_alarm_threshold" {
  description = "CloudWatchアラームが発報するLambdaエラー数の閾値"
  type        = number
  default     = 5
}

variable "alarm_sns_topic_arn" {
  description = "アラーム通知先のSNSトピックARN（空文字列の場合は通知なし）"
  type        = string
  default     = ""
}

variable "common_tags" {
  description = "全リソースに付与する共通タグ"
  type        = map(string)
  default = {
    Project     = "ai-agent-harness"
    Environment = "poc"
    ManagedBy   = "terraform"
  }
}

# =============================================================================
# DynamoDB テーブル: セッション状態管理 (State Management)
# -----------------------------------------------------------------------------
# エージェントが複数ターンにわたってタスクの文脈を保持するための「記憶」ストア
# Agent = Model + Harness の等式において、状態管理はハーネスの中核コンポーネント
#
# テーブル設計:
#   session_id (ハッシュキー): セッション識別子
#   timestamp  (レンジキー) : 同一セッションの複数ステップを時系列管理
#   ttl        (TTL属性)    : UNIXタイムスタンプ超過後に自動削除
# =============================================================================
resource "aws_dynamodb_table" "session_state" {
  name         = "${var.project_name}-session-state"
  billing_mode = "PAY_PER_REQUEST" # オンデマンド課金: トラフィック予測が困難な場合に最適

  hash_key  = "session_id"
  range_key = "timestamp"

  attribute {
    name = "session_id"
    type = "S" # S = String型
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  # TTL: 指定フィールドのUNIXタイムスタンプを過ぎたアイテムを自動削除
  # 古いセッションデータのクリーンアップコストを削減
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  # 保存時暗号化 (デフォルトでAWS管理KMSキーを使用)
  server_side_encryption {
    enabled = true
  }

  # ポイントインタイムリカバリ: 35日以内の任意の時点に復元可能（本番では有効化推奨）
  point_in_time_recovery {
    enabled = var.enable_pitr
  }
}

# =============================================================================
# SQS デッドレターキュー (DLQ): エラーハンドリング
# -----------------------------------------------------------------------------
# EventBridgeがLambdaへの配信に2回失敗した場合、イベントをここに退避
# 保管されたイベントを後からリプレイ・調査することでチェックポイント復旧が可能
# (チェックポイント設計: 中断したワークフローを最初から再実行せずに再開)
# =============================================================================
resource "aws_sqs_queue" "agent_dlq" {
  name                      = "${var.project_name}-agent-dlq"
  message_retention_seconds = 1209600 # 14日間保存

  # SQS管理の暗号化キーで保存データを保護
  sqs_managed_sse_enabled = true
}

# =============================================================================
# EventBridge ルール: イベント駆動型エージェント起動
# -----------------------------------------------------------------------------
# 外部システムやスケジューラーからの非同期イベントでエージェントを起動
# event_pattern: マッチするイベントの条件 (source + detail-type で絞り込み)
#
# 利用例:
#   - 毎朝9時に定期レポートを生成するエージェントを起動
#   - S3にファイルがアップロードされたら分析エージェントを起動
# =============================================================================
resource "aws_cloudwatch_event_rule" "agent_trigger" {
  name        = "${var.project_name}-agent-trigger"
  description = "AIエージェントハーネスを非同期トリガーするEventBridgeルール"

  event_pattern = jsonencode({
    source      = ["${var.project_name}.events"]
    detail-type = ["AgentTaskRequested"]
  })
}

# EventBridge → Lambda 転送ターゲット設定
resource "aws_cloudwatch_event_target" "agent_lambda_target" {
  rule      = aws_cloudwatch_event_rule.agent_trigger.name
  target_id = "AgentEntryLambda"
  arn       = aws_lambda_function.agent_entry.arn

  # リトライポリシー: 最大2回リトライ、5分以内のイベントのみ処理
  retry_policy {
    maximum_retry_attempts       = 2
    maximum_event_age_in_seconds = 300
  }

  # 配信失敗時のDLQ転送
  dead_letter_config {
    arn = aws_sqs_queue.agent_dlq.arn
  }
}

# =============================================================================
# CloudWatch ロググループ: 可観測性 (Observability)
# -----------------------------------------------------------------------------
# 本番運用中のエージェントの94%が観測可能性を導入 (2026年調査)
# すべてのLambda関数とBedrockエージェントのログを集中管理し、
# トレーシング・デバッグ・コスト追跡に活用
# =============================================================================
resource "aws_cloudwatch_log_group" "agent_entry_logs" {
  name              = "/aws/lambda/${var.project_name}-agent-entry"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "tool_executor_logs" {
  name              = "/aws/lambda/${var.project_name}-tool-executor"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "bedrock_model_invocation_logs" {
  name              = "/aws/bedrock/agents/${var.project_name}"
  retention_in_days = var.log_retention_days
}

# =============================================================================
# CloudWatch アラーム: エラー監視
# Lambda関数のエラー率を監視し、閾値超過時にSNS経由で通知
# =============================================================================
resource "aws_cloudwatch_metric_alarm" "agent_entry_errors" {
  alarm_name          = "${var.project_name}-agent-entry-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 60 # 1分ごとに評価
  statistic           = "Sum"
  threshold           = var.error_alarm_threshold
  alarm_description   = "エージェントエントリLambdaのエラー数が${var.error_alarm_threshold}件/分を超えました"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.agent_entry.function_name
  }

  alarm_actions = var.alarm_sns_topic_arn != "" ? [var.alarm_sns_topic_arn] : []
}

# =============================================================================
# 出力値 (Outputs)
# terraform apply 完了後に表示される値。他のTerraformモジュールからも参照可能
# =============================================================================

output "api_endpoint" {
  description = "エージェントへのリクエスト送信先エンドポイントURL"
  value       = "${aws_apigatewayv2_stage.default.invoke_url}/invoke"
}

output "bedrock_agent_id" {
  description = "Bedrock AgentのID (APIから直接呼び出す場合に必要)"
  value       = aws_bedrockagent_agent.harness_agent.agent_id
}

output "bedrock_agent_alias_id" {
  description = "Bedrock AgentエイリアスID"
  value       = aws_bedrockagent_agent_alias.harness_agent_alias.agent_alias_id
}

output "session_table_name" {
  description = "セッション状態を保存するDynamoDBテーブル名"
  value       = aws_dynamodb_table.session_state.name
}

output "agent_entry_lambda_arn" {
  description = "エントリポイントLambdaのARN"
  value       = aws_lambda_function.agent_entry.arn
}
