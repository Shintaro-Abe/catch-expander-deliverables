# PoC品質 - 本番環境での使用前に十分なセキュリティレビューが必要です
# CloudWatchによるLLMOpsモニタリング・アラート・ダッシュボードを定義します
#
# 監視対象メトリクス:
#   - Lambda エラー数・レイテンシ（P50/P99）・同時実行数
#   - API Gateway 4xx/5xxエラー率
#   - LLMパイプライン（Step Functions）成功/失敗数
#   - 低品質レスポンス数（ハルシネーション・プロンプトドリフト検知）
#   - トークン使用量（コスト管理）

# ═══════════════════════════════════════════════════════════════════════════════
# SNS トピック（アラート通知の集約点）
# SNS = Simple Notification Service。メール・Slack・PagerDutyへ通知を転送できます
# ═══════════════════════════════════════════════════════════════════════════════

resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ═══════════════════════════════════════════════════════════════════════════════
# CloudWatch メトリクスフィルター
#
# Lambdaのログ出力からカスタムメトリクスを抽出します。
# ログ内の特定パターンにマッチした行をカウント・集計することで、
# LLMOps固有の指標（トークン使用量・品質スコア等）をCloudWatchに送れます。
# ═══════════════════════════════════════════════════════════════════════════════

# Bedrockトークン使用量フィルター（コスト管理用）
# ログ出力形式例: "bedrock_usage input_tokens=150 output_tokens=300 ..."
resource "aws_cloudwatch_log_metric_filter" "input_tokens" {
  name           = "${local.name_prefix}-input-tokens"
  log_group_name = aws_cloudwatch_log_group.lambda_inference.name
  pattern        = "[timestamp, requestId, level, msg=\"bedrock_usage\", input_tokens_label, input_tokens, ...]"

  metric_transformation {
    name          = "InputTokenCount"
    namespace     = "LLMOps/${var.project_name}"
    value         = "$input_tokens"
    default_value = "0"
    unit          = "Count"
  }
}

# 低品質レスポンス検出フィルター（ハルシネーション・ドリフト監視）
# ログ出力形式例: "LOW_QUALITY_RESPONSE query=... score=0.45"
resource "aws_cloudwatch_log_metric_filter" "low_quality" {
  name           = "${local.name_prefix}-low-quality"
  log_group_name = aws_cloudwatch_log_group.lambda_inference.name
  pattern        = "LOW_QUALITY_RESPONSE"

  metric_transformation {
    name          = "LowQualityResponseCount"
    namespace     = "LLMOps/${var.project_name}"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

# ═══════════════════════════════════════════════════════════════════════════════
# CloudWatch アラーム
#
# アラームの状態遷移:
#   OK（正常）→ ALARM（異常）: SNS経由でアラート送信
#   ALARM（異常）→ OK（回復）: 回復通知を送信
#
# 設計方針:
#   - 多層アプローチ: Lambda・API Gateway・Step Functions・LLM品質を別々に監視
#   - treat_missing_data = "notBreaching": データ欠損時はOKとみなす（FalsePositive防止）
# ═══════════════════════════════════════════════════════════════════════════════

# ── Lambda: エラー数 ──────────────────────────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${local.name_prefix}-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "Lambda関数のエラー数が1分間に5件を超えました。推論エンドポイントに障害が発生している可能性があります。"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.inference.function_name
  }
}

# ── Lambda: P99レイテンシ（ユーザー体感速度の監視） ──────────────────────────
# P99 = 99パーセンタイル。100リクエスト中で最も遅い1件のレスポンス時間
# SLA違反の予兆を早期に検知するために重要です
resource "aws_cloudwatch_metric_alarm" "lambda_duration_p99" {
  alarm_name          = "${local.name_prefix}-lambda-p99-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  period              = 60
  extended_statistic  = "p99"
  threshold           = 25000
  alarm_description   = "Lambda P99レイテンシが25秒を超えました。LLM推論の遅延が深刻です。"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.inference.function_name
  }
}

# ── Lambda: スロットリング（同時実行上限への接近を検知） ──────────────────────
# スロットリング = Lambdaの同時実行数が上限に達した際にリクエストが拒否される現象
resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  alarm_name          = "${local.name_prefix}-lambda-throttles"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Throttles"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "Lambda関数でスロットリングが発生しています。同時実行数の上限引き上げを検討してください。"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.inference.function_name
  }
}

# ── API Gateway: 5xxエラー ────────────────────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name_prefix}-api-5xx-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "5XXError"
  namespace           = "AWS/ApiGateway"
  period              = 60
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "API Gateway 5xxエラーが1分間に10件を超えました。バックエンドに重大な問題が発生しています。"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    ApiName   = aws_api_gateway_rest_api.llmops.name
    StageName = var.environment
  }
}

# ── LLM品質: 低品質レスポンス数（ハルシネーション・プロンプトドリフト検知） ─
# プロンプトドリフト = モデルのサイレント更新やユーザーパターン変化により
#                      プロンプト未変更でも出力品質が低下する現象
resource "aws_cloudwatch_metric_alarm" "low_quality_responses" {
  alarm_name          = "${local.name_prefix}-low-quality-responses"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "LowQualityResponseCount"
  namespace           = "LLMOps/${var.project_name}"
  period              = 300
  statistic           = "Sum"
  threshold           = var.low_quality_alert_count
  alarm_description   = "5分間の低品質レスポンス数が${var.low_quality_alert_count}件を超えました。プロンプトドリフトまたはモデル更新の影響を確認してください。"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"
}

# ── Step Functions: パイプライン実行失敗 ──────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "pipeline_failures" {
  alarm_name          = "${local.name_prefix}-pipeline-failures"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 60
  statistic           = "Sum"
  threshold           = 3
  alarm_description   = "LLMパイプライン（Step Functions）の実行失敗が1分間に3件を超えました。"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.llm_pipeline.arn
  }
}

# ═══════════════════════════════════════════════════════════════════════════════
# CloudWatch ダッシュボード
# LLMOpsの主要KPIを1画面で確認できます
# AWSコンソール > CloudWatch > ダッシュボード から閲覧できます
# ═══════════════════════════════════════════════════════════════════════════════

resource "aws_cloudwatch_dashboard" "llmops" {
  dashboard_name = "${local.name_prefix}-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      # ヘッダー
      {
        type   = "text"
        x      = 0; y = 0; width = 24; height = 2
        properties = {
          markdown = join("\n", [
            "## LLMOps モニタリングダッシュボード",
            "**環境:** ${upper(var.environment)} | **モデル:** `${var.bedrock_model_id}` | **プロジェクト:** ${var.project_name}",
            "---"
          ])
        }
      },

      # Lambda 呼び出し数
      {
        type   = "metric"
        x      = 0; y = 2; width = 6; height = 6
        properties = {
          title   = "Lambda 呼び出し数（/分）"
          view    = "timeSeries"
          stacked = false
          metrics = [[
            "AWS/Lambda", "Invocations",
            "FunctionName", aws_lambda_function.inference.function_name,
            { stat = "Sum", period = 60, label = "呼び出し数" }
          ]]
          region = var.aws_region
        }
      },

      # Lambda エラー数
      {
        type   = "metric"
        x      = 6; y = 2; width = 6; height = 6
        properties = {
          title   = "Lambda エラー数（/分）"
          view    = "timeSeries"
          stacked = false
          metrics = [[
            "AWS/Lambda", "Errors",
            "FunctionName", aws_lambda_function.inference.function_name,
            { stat = "Sum", period = 60, color = "#d62728", label = "エラー数" }
          ]]
          region = var.aws_region
          annotations = {
            horizontal = [{ label = "アラート閾値", value = 5, color = "#ff0000" }]
          }
        }
      },

      # Lambda レイテンシ（P50/P99）
      {
        type   = "metric"
        x      = 12; y = 2; width = 6; height = 6
        properties = {
          title   = "Lambda レイテンシ（ms）"
          view    = "timeSeries"
          stacked = false
          metrics = [
            [
              "AWS/Lambda", "Duration",
              "FunctionName", aws_lambda_function.inference.function_name,
              { stat = "p50", period = 60, label = "P50（中央値）" }
            ],
            [
              "AWS/Lambda", "Duration",
              "FunctionName", aws_lambda_function.inference.function_name,
              { stat = "p99", period = 60, color = "#ff7f0e", label = "P99（99パーセンタイル）" }
            ]
          ]
          region = var.aws_region
          annotations = {
            horizontal = [{ label = "P99アラート閾値（25秒）", value = 25000, color = "#ff0000" }]
          }
        }
      },

      # Lambda 同時実行数
      {
        type   = "metric"
        x      = 18; y = 2; width = 6; height = 6
        properties = {
          title   = "Lambda 同時実行数（最大値）"
          view    = "timeSeries"
          stacked = false
          metrics = [[
            "AWS/Lambda", "ConcurrentExecutions",
            "FunctionName", aws_lambda_function.inference.function_name,
            { stat = "Maximum", period = 60, label = "同時実行数" }
          ]]
          region = var.aws_region
        }
      },

      # 低品質レスポンス数（LLM品質モニタリング）
      {
        type   = "metric"
        x      = 0; y = 8; width = 8; height = 6
        properties = {
          title   = "低品質レスポンス数（品質スコア < ${var.quality_score_threshold}）"
          view    = "timeSeries"
          stacked = false
          metrics = [[
            "LLMOps/${var.project_name}", "LowQualityResponseCount",
            { stat = "Sum", period = 300, color = "#d62728", label = "低品質件数（5分合計）" }
          ]]
          region = var.aws_region
          annotations = {
            horizontal = [{ label = "アラート閾値", value = var.low_quality_alert_count, color = "#ff0000" }]
          }
        }
      },

      # Step Functions 実行状況
      {
        type   = "metric"
        x      = 8; y = 8; width = 8; height = 6
        properties = {
          title   = "LLMパイプライン 実行状況（/分）"
          view    = "timeSeries"
          stacked = false
          metrics = [
            [
              "AWS/States", "ExecutionsSucceeded",
              "StateMachineArn", aws_sfn_state_machine.llm_pipeline.arn,
              { stat = "Sum", period = 60, color = "#2ca02c", label = "成功" }
            ],
            [
              "AWS/States", "ExecutionsFailed",
              "StateMachineArn", aws_sfn_state_machine.llm_pipeline.arn,
              { stat = "Sum", period = 60, color = "#d62728", label = "失敗" }
            ],
            [
              "AWS/States", "ExecutionThrottled",
              "StateMachineArn", aws_sfn_state_machine.llm_pipeline.arn,
              { stat = "Sum", period = 60, color = "#ff7f0e", label = "スロットリング" }
            ]
          ]
          region = var.aws_region
        }
      },

      # API Gateway エラー率
      {
        type   = "metric"
        x      = 16; y = 8; width = 8; height = 6
        properties = {
          title   = "API Gateway エラー数（/分）"
          view    = "timeSeries"
          stacked = false
          metrics = [
            [
              "AWS/ApiGateway", "4XXError",
              "ApiName", aws_api_gateway_rest_api.llmops.name,
              "Stage", var.environment,
              { stat = "Sum", period = 60, color = "#ff7f0e", label = "4xxエラー（クライアント側）" }
            ],
            [
              "AWS/ApiGateway", "5XXError",
              "ApiName", aws_api_gateway_rest_api.llmops.name,
              "Stage", var.environment,
              { stat = "Sum", period = 60, color = "#d62728", label = "5xxエラー（サーバー側）" }
            ]
          ]
          region = var.aws_region
        }
      },

      # トークン使用量（コスト管理）
      {
        type   = "metric"
        x      = 0; y = 14; width = 12; height = 6
        properties = {
          title   = "Bedrock 入力トークン使用量（/分合計）"
          view    = "timeSeries"
          stacked = false
          metrics = [[
            "LLMOps/${var.project_name}", "InputTokenCount",
            { stat = "Sum", period = 60, color = "#1f77b4", label = "入力トークン数" }
          ]]
          region = var.aws_region
        }
      },

      # アラーム状態一覧
      {
        type   = "alarm"
        x      = 12; y = 14; width = 12; height = 6
        properties = {
          title = "アラーム状態一覧"
          alarms = [
            aws_cloudwatch_metric_alarm.lambda_errors.arn,
            aws_cloudwatch_metric_alarm.lambda_duration_p99.arn,
            aws_cloudwatch_metric_alarm.lambda_throttles.arn,
            aws_cloudwatch_metric_alarm.api_5xx.arn,
            aws_cloudwatch_metric_alarm.low_quality_responses.arn,
            aws_cloudwatch_metric_alarm.pipeline_failures.arn
          ]
        }
      }
    ]
  })
}

# ═══════════════════════════════════════════════════════════════════════════════
# 出力値
# ═══════════════════════════════════════════════════════════════════════════════

output "dashboard_url" {
  description = "CloudWatchダッシュボードURL（AWSコンソールで直接開けます）"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home#dashboards:name=${aws_cloudwatch_dashboard.llmops.dashboard_name}"
}

output "sns_alerts_topic_arn" {
  description = "アラート通知SNSトピックARN"
  value       = aws_sns_topic.alerts.arn
}
