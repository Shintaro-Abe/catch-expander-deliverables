# PoC品質: 本番環境での使用前に、セキュリティレビューおよびパラメータ見直しを必ず実施してください。

# ════════════════════════════════════════════════════════════════════
# WAF（Web Application Firewall）
# ════════════════════════════════════════════════════════════════════
# WAFはAPI Gatewayへの通信の「最初の防衛線」です。
# SQLインジェクション・XSS・不審なIPを自動的にブロックします。
# 評価順: WAF → リソースポリシー → IAM → Cognito Authorizer

resource "aws_wafv2_web_acl" "api" {
  count = var.waf_enabled ? 1 : 0

  name  = "${local.name_prefix}-api-waf"
  scope = "REGIONAL" # API Gatewayにはは REGIONAL を指定（CloudFrontには CLOUDFRONT）

  default_action {
    allow {} # ルールに一致しないリクエストはデフォルトで許可
  }

  # ── AWSマネージドルール（OWASP Top 10対応、メンテナンス不要） ──

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 10

    override_action { none {} } # ブロックアクションをそのまま使用

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-common-rules"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesSQLiRuleSet"
    priority = 20

    override_action { none {} }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-sqli-rules"
      sampled_requests_enabled   = true
    }
  }

  # ── カスタムルール: レートベース制限（DoS対策） ──────────────
  # 同一IPから5分間に閾値を超えるリクエストが来た場合にブロック

  rule {
    name     = "RateLimitRule"
    priority = 1

    action { block {} }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit # 5分間のIPごとの上限リクエスト数
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name_prefix}-waf"
    sampled_requests_enabled   = true
  }
}

# ════════════════════════════════════════════════════════════════════
# API Gateway REST API
# ════════════════════════════════════════════════════════════════════
# REST API: WAF統合・APIキー管理・キャッシュ・リクエスト検証など
# 高度な機能が必要なエンタープライズ用途に適しています。
# シンプルな用途には HTTP API（api_gateway.tf参照）の方が低コストです。

resource "aws_api_gateway_rest_api" "main" {
  name        = "${local.name_prefix}-rest-api"
  description = "Data API PoC - REST API（Cognito認証 + WAF + キャッシュ対応）"

  endpoint_configuration {
    types = ["REGIONAL"] # Regional: 同一リージョンのクライアントに最適
  }
}

# ── Cognito オーソライザー ────────────────────────────────────────
# CognitoのIDトークン/アクセストークンを検証してアクセスを制御します

resource "aws_api_gateway_authorizer" "cognito" {
  name          = "${local.name_prefix}-cognito-auth"
  rest_api_id   = aws_api_gateway_rest_api.main.id
  type          = "COGNITO_USER_POOLS"
  provider_arns = [aws_cognito_user_pool.main.arn]

  # Authorizationヘッダーからトークンを取得
  identity_source = "method.request.header.Authorization"
}

# ── リソース定義 ─────────────────────────────────────────────────
# REST APIはURLパス（リソース）とHTTPメソッドの組み合わせでエンドポイントを定義します

# /items リソース
resource "aws_api_gateway_resource" "items" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "items"
}

# GET /items メソッド（Cognito認証必須）
resource "aws_api_gateway_method" "get_items" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.items.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  # APIキーによるスロットリング制御（Usage Planと連携）
  api_key_required = true
}

# GET /items → Lambda 統合（プロキシ統合）
# プロキシ統合: リクエスト全体をLambdaに転送。マッピングテンプレートが不要でシンプル
resource "aws_api_gateway_integration" "get_items" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.items.id
  http_method             = aws_api_gateway_method.get_items.http_method
  integration_http_method = "POST" # Lambda呼び出しは常にPOST
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.api_handler.invoke_arn
}

# /sql リソース（RDS Data API経由でSQLを実行するエンドポイント）
resource "aws_api_gateway_resource" "sql" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "sql"
}

resource "aws_api_gateway_method" "post_sql" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.sql.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  api_key_required = true
}

resource "aws_api_gateway_integration" "post_sql" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.sql.id
  http_method             = aws_api_gateway_method.post_sql.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.api_handler.invoke_arn
}

# ── Lambda 呼び出し権限 ──────────────────────────────────────────
resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_handler.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# ── デプロイメント・ステージ ─────────────────────────────────────
resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  # メソッドや統合の変更を検知して再デプロイ
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.items.id,
      aws_api_gateway_method.get_items.id,
      aws_api_gateway_integration.get_items.id,
      aws_api_gateway_resource.sql.id,
      aws_api_gateway_method.post_sql.id,
      aws_api_gateway_integration.post_sql.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "main" {
  deployment_id = aws_api_gateway_deployment.main.id
  rest_api_id   = aws_api_gateway_rest_api.main.id
  stage_name    = var.environment

  # ── キャッシュ設定 ────────────────────────────────────────────
  # レスポンスキャッシュを有効にするとバックエンドへのリクエストが減り
  # レイテンシが改善しますが、追加コストが発生します（時間課金）。

  cache_cluster_enabled = var.api_gateway_cache_enabled
  cache_cluster_size    = var.api_gateway_cache_enabled ? var.api_gateway_cache_size : null

  # ── スロットリング設定 ────────────────────────────────────────
  # トークンバケットアルゴリズム: 429 Too Many Requestsを返して過負荷を防ぎます

  default_route_settings {
    # HTTP APIとの互換性のために空ブロックが必要
  }

  # X-Rayトレーシング（リクエストの処理経路を可視化）
  xray_tracing_enabled = true

  # アクセスログ設定（デバッグ・監査に重要）
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      caller         = "$context.identity.caller"
      user           = "$context.identity.user"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      resourcePath   = "$context.resourcePath"
      status         = "$context.status"
      responseLength = "$context.responseLength"
    })
  }
}

# ステージレベルのスロットリング設定（メソッドレベルはaws_api_gateway_method_settingsで設定）
resource "aws_api_gateway_method_settings" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  stage_name  = aws_api_gateway_stage.main.stage_name
  method_path = "*/*" # 全メソッドに適用

  settings {
    throttling_rate_limit  = var.api_throttle_rate_limit
    throttling_burst_limit = var.api_throttle_burst_limit

    # メトリクス・ログの有効化
    metrics_enabled    = true
    logging_level      = "INFO" # OFF / ERROR / INFO
    data_trace_enabled = false  # 本番環境では false（リクエスト本文がログに残るためセキュリティリスク）

    # キャッシュ設定（GET /items にキャッシュを適用する場合）
    caching_enabled = var.api_gateway_cache_enabled
    cache_ttl_in_seconds = 300 # デフォルト5分
  }
}

# WAFのWeb ACLをAPI Gatewayステージに関連付け
resource "aws_wafv2_web_acl_association" "api" {
  count = var.waf_enabled ? 1 : 0

  resource_arn = aws_api_gateway_stage.main.arn
  web_acl_arn  = aws_wafv2_web_acl.api[0].arn
}

# ── APIキー + 使用量プラン ────────────────────────────────────────
# APIキーはクライアントを識別し、スロットリングやクォータを適用します。
# セキュリティ目的ではなくトラフィック制御に使用してください。

resource "aws_api_gateway_api_key" "default" {
  name    = "${local.name_prefix}-default-key"
  enabled = true
}

resource "aws_api_gateway_usage_plan" "main" {
  name = "${local.name_prefix}-usage-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.main.id
    stage  = aws_api_gateway_stage.main.stage_name
  }

  # クォータ（月間リクエスト上限）
  quota_settings {
    limit  = 1000000
    period = "MONTH"
  }

  # スロットリング（APIキーレベル）
  throttle_settings {
    rate_limit  = var.api_throttle_rate_limit
    burst_limit = var.api_throttle_burst_limit
  }
}

resource "aws_api_gateway_usage_plan_key" "main" {
  key_id        = aws_api_gateway_api_key.default.id
  key_type      = "API_KEY"
  usage_plan_id = aws_api_gateway_usage_plan.main.id
}

# ── CloudWatch ロググループ ──────────────────────────────────────
resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${local.name_prefix}"
  retention_in_days = 30 # 本番環境では90〜365日を推奨
}

# API GatewayのCloudWatchログ出力に必要なIAMロール
resource "aws_iam_role" "api_gateway_cloudwatch" {
  name = "${local.name_prefix}-apigw-cw-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "api_gateway_cloudwatch" {
  role       = aws_iam_role.api_gateway_cloudwatch.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_api_gateway_account" "main" {
  cloudwatch_role_arn = aws_iam_role.api_gateway_cloudwatch.arn
}
