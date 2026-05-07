# PoC品質: 本番環境での使用前に、セキュリティレビューおよびパラメータ見直しを必ず実施してください。

# ════════════════════════════════════════════════════════════════════
# Outputs（terraform apply 後に確認できる重要な値）
# ════════════════════════════════════════════════════════════════════

# ── API Gateway ──────────────────────────────────────────────────
output "api_gateway_invoke_url" {
  description = "API GatewayのベースURL。クライアントはこのURLにリクエストを送ります"
  value       = aws_api_gateway_stage.main.invoke_url
}

output "api_gateway_rest_api_id" {
  description = "API Gateway REST API ID（AWS CLIやコンソールでの操作に使用）"
  value       = aws_api_gateway_rest_api.main.id
}

output "api_key_value" {
  description = "APIキー（x-api-keyヘッダーに設定して使用。シークレット扱い）"
  value       = aws_api_gateway_api_key.default.value
  sensitive   = true # terraform output -raw api_key_value で取得
}

# ── AppSync ──────────────────────────────────────────────────────
output "appsync_graphql_url" {
  description = "AppSync GraphQL APIエンドポイントURL"
  value       = aws_appsync_graphql_api.main.uris["GRAPHQL"]
}

output "appsync_realtime_url" {
  description = "AppSync リアルタイムサブスクリプションURL（WebSocket接続用）"
  value       = aws_appsync_graphql_api.main.uris["REALTIME"]
}

output "appsync_api_key" {
  description = "AppSync APIキー（開発/テスト環境でのみ使用）"
  value       = aws_appsync_api_key.dev.key
  sensitive   = true
}

# ── Aurora Serverless v2 ──────────────────────────────────────────
output "aurora_cluster_endpoint" {
  description = "Auroraクラスターの書き込みエンドポイント（Writer接続用）"
  value       = aws_rds_cluster.main.endpoint
}

output "aurora_reader_endpoint" {
  description = "Auroraクラスターの読み取りエンドポイント（Reader接続用）"
  value       = aws_rds_cluster.main.reader_endpoint
}

output "aurora_cluster_arn" {
  description = "Aurora ClusterのARN（RDS Data API呼び出しに必要）"
  value       = aws_rds_cluster.main.arn
}

output "aurora_secret_arn" {
  description = "Aurora認証情報のSecrets Manager ARN（RDS Data API呼び出しに必要）"
  value       = aws_rds_cluster.main.master_user_secret[0].secret_arn
  sensitive   = true
}

# ── DynamoDB ─────────────────────────────────────────────────────
output "dynamodb_table_name" {
  description = "DynamoDBテーブル名"
  value       = aws_dynamodb_table.items.name
}

output "dynamodb_table_arn" {
  description = "DynamoDBテーブルARN"
  value       = aws_dynamodb_table.items.arn
}

output "dynamodb_stream_arn" {
  description = "DynamoDB StreamのARN（Lambda CDC連携に使用）"
  value       = aws_dynamodb_table.items.stream_arn
}

# ── Cognito ──────────────────────────────────────────────────────
output "cognito_user_pool_id" {
  description = "Cognito User Pool ID（フロントエンドの認証設定に使用）"
  value       = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "Cognito App Client ID（フロントエンドの認証設定に使用）"
  value       = aws_cognito_user_pool_client.api.id
}

# ── Lambda ──────────────────────────────────────────────────────
output "lambda_function_name" {
  description = "Lambda関数名"
  value       = aws_lambda_function.api_handler.function_name
}

output "lambda_function_arn" {
  description = "Lambda関数ARN"
  value       = aws_lambda_function.api_handler.arn
}

# ── WAF ─────────────────────────────────────────────────────────
output "waf_web_acl_arn" {
  description = "WAF Web ACL ARN（waf_enabled=falseの場合は空）"
  value       = var.waf_enabled ? aws_wafv2_web_acl.api[0].arn : "WAF無効"
}

# ── 動作確認コマンド（terraform apply 後にコピーして使用） ─────────
output "quickstart_commands" {
  description = "動作確認用のサンプルコマンド（APIキーとCognitoトークンを取得後に実行）"
  value       = <<-EOT
    # 1. APIキーを確認
    terraform output -raw api_key_value

    # 2. DynamoDBアイテム一覧をAppSync GraphQLで取得（APIキー認証）
    curl -X POST ${aws_appsync_graphql_api.main.uris["GRAPHQL"]} \
      -H "Content-Type: application/json" \
      -H "x-api-key: <appsync_api_key>" \
      -d '{"query": "{ listItems(limit: 5) { items { pk sk name } } }"}'

    # 3. REST API経由でDynamoDBアイテム一覧を取得（Cognito JWT必須）
    curl -X GET ${aws_api_gateway_stage.main.invoke_url}/items \
      -H "Authorization: Bearer <cognito_id_token>" \
      -H "x-api-key: <api_gateway_api_key>"

    # 4. REST API経由でRDS Data APIのSQLを実行
    curl -X POST ${aws_api_gateway_stage.main.invoke_url}/sql \
      -H "Authorization: Bearer <cognito_id_token>" \
      -H "x-api-key: <api_gateway_api_key>" \
      -H "Content-Type: application/json" \
      -d '{"sql": "SELECT NOW()"}'
  EOT
}
