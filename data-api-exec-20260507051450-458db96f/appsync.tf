# PoC品質: 本番環境での使用前に、セキュリティレビューおよびパラメータ見直しを必ず実施してください。

# ════════════════════════════════════════════════════════════════════
# AppSync GraphQL API
# ════════════════════════════════════════════════════════════════════
# AppSync: フルマネージドのGraphQL APIサービス。
# 特長: リアルタイムサブスクリプション / 複数データソース統合 / オフライン対応
# コスト: $4.00/100万操作（REST APIより高め。GraphQL機能が必要な場合に選択）
#
# REST APIとの選択指針:
#   - 画面/デバイスで必要なデータ形状が異なる      → AppSync が有利
#   - リアルタイム更新（チャット・通知）が必要      → AppSync が有利
#   - シンプルなCRUD / コスト重視                  → API Gateway HTTP API が有利

resource "aws_appsync_graphql_api" "main" {
  name                = "${local.name_prefix}-graphql-api"
  authentication_type = var.appsync_auth_type

  # Cognitoユーザープール認証の設定（authentication_type = AMAZON_COGNITO_USER_POOLS の場合）
  dynamic "user_pool_config" {
    for_each = var.appsync_auth_type == "AMAZON_COGNITO_USER_POOLS" ? [1] : []

    content {
      user_pool_id   = aws_cognito_user_pool.main.id
      aws_region     = var.aws_region
      default_action = "ALLOW" # ALLOW: 認証済みユーザーは基本的に許可
    }
  }

  # マルチ認証モード: デフォルト認証に加えてAPI_KEYも受け付ける（開発/テスト用途）
  # 本番ではAPI_KEYを削除し、Cognito + IAMの組み合わせを推奨
  additional_authentication_provider {
    authentication_type = "API_KEY"
  }

  additional_authentication_provider {
    authentication_type = "AWS_IAM"
  }

  # X-Rayトレーシング（AppSyncのパフォーマンス分析）
  xray_enabled = true

  # CloudWatchログ設定
  log_config {
    cloudwatch_logs_role_arn = aws_iam_role.appsync_logs.arn
    field_log_level          = "ERROR" # ALL / ERROR / NONE（本番ではERRORを推奨）
    exclude_verbose_content  = true
  }
}

# 開発/テスト用APIキー（最大365日有効）
resource "aws_appsync_api_key" "dev" {
  api_id  = aws_appsync_graphql_api.main.id
  expires = timeadd(timestamp(), "8760h") # 365日後
}

# ── GraphQL スキーマ ──────────────────────────────────────────────
# GraphQLスキーマ: APIで扱うデータ型とクエリ/ミューテーション/サブスクリプションを定義します。
# SDL（Schema Definition Language）という型付き言語で記述します。

resource "aws_appsync_graphql_api" "schema" {
  # スキーマはライフサイクル上 aws_appsync_graphql_api に含める
  # （別ファイルに分離する場合は aws_appsync_domain_name を使用）
  count = 0 # プレースホルダー。実際は aws_appsync_graphql_api.main に schema 引数を追加
}

locals {
  graphql_schema = <<-GRAPHQL
    # PoC品質スキーマ: 実際のビジネス要件に合わせて拡張してください

    type Item {
      pk: String!
      sk: String!
      name: String
      description: String
      createdAt: String
      # @aws_cognito_user_pools: Cognito認証ユーザーのみアクセス可能
      # @aws_api_key: APIキーでもアクセス可能（開発用）
    }

    type ItemConnection {
      items: [Item]
      nextToken: String  # ページネーション用トークン
    }

    type Query {
      # 単一アイテム取得（Cognito認証必須）
      getItem(pk: String!, sk: String!): Item
        @aws_cognito_user_pools @aws_api_key

      # アイテム一覧取得（20件まで / ページネーション対応）
      listItems(limit: Int, nextToken: String): ItemConnection
        @aws_cognito_user_pools @aws_api_key

      # SQL実行経由でのデータ取得（Aurora Serverless v2 Data API）
      queryBySQL(sql: String!, parameters: [SQLParameter]): SQLResult
        @aws_iam  # バックエンドサービスのみ許可（IAM認証必須）
    }

    type Mutation {
      createItem(input: CreateItemInput!): Item
        @aws_cognito_user_pools

      updateItem(pk: String!, sk: String!, input: UpdateItemInput!): Item
        @aws_cognito_user_pools

      deleteItem(pk: String!, sk: String!): Boolean
        @aws_cognito_user_pools
    }

    type Subscription {
      # リアルタイム更新: createItemが実行されると購読者に通知
      onCreateItem: Item
        @aws_subscribe(mutations: ["createItem"])
        @aws_cognito_user_pools
    }

    input CreateItemInput {
      pk:          String!
      sk:          String!
      name:        String
      description: String
    }

    input UpdateItemInput {
      name:        String
      description: String
    }

    input SQLParameter {
      name:        String!
      stringValue: String
      longValue:   Int
      booleanValue: Boolean
    }

    type SQLResult {
      records:         [[Field]]
      numberOfRecordsUpdated: Int
    }

    type Field {
      stringValue:  String
      longValue:    Int
      booleanValue: Boolean
      isNull:       Boolean
    }
  GRAPHQL
}

# スキーマをAppSync APIに関連付け
resource "aws_appsync_schema" "main" {
  api_id     = aws_appsync_graphql_api.main.id
  definition = local.graphql_schema
}

# ── IAM ロール（AppSyncのログ出力・データソースアクセス用） ────────

resource "aws_iam_role" "appsync_logs" {
  name = "${local.name_prefix}-appsync-logs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "appsync.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "appsync_logs" {
  role       = aws_iam_role.appsync_logs.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppSyncPushToCloudWatchLogs"
}

resource "aws_iam_role" "appsync_datasource" {
  name = "${local.name_prefix}-appsync-datasource-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "appsync.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "appsync_dynamodb" {
  name = "${local.name_prefix}-appsync-dynamodb-policy"
  role = aws_iam_role.appsync_datasource.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
        ]
        Resource = [
          aws_dynamodb_table.items.arn,
          "${aws_dynamodb_table.items.arn}/index/*",
        ]
      },
      {
        # AppSync → Lambda経由でRDS Data APIにアクセス
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.api_handler.arn
      },
    ]
  })
}

# ── データソース定義 ─────────────────────────────────────────────
# AppSyncリゾルバーはデータソースを通じてバックエンドにアクセスします。
# DynamoDB / Lambda / HTTP / RDS Data API などをデータソースとして設定できます。

# DynamoDBデータソース（直接統合: Lambdaなしで高速・低コスト）
resource "aws_appsync_datasource" "dynamodb" {
  api_id           = aws_appsync_graphql_api.main.id
  name             = "DynamoDBItems"
  type             = "AMAZON_DYNAMODB"
  service_role_arn = aws_iam_role.appsync_datasource.arn

  dynamodb_config {
    table_name = aws_dynamodb_table.items.name
    region     = var.aws_region

    # デルタ同期（オフライン対応）を有効化する場合は delta_sync_config を追加
  }
}

# Lambdaデータソース（RDS Data API実行 / 複雑なビジネスロジック用）
resource "aws_appsync_datasource" "lambda" {
  api_id           = aws_appsync_graphql_api.main.id
  name             = "LambdaDataAPI"
  type             = "AWS_LAMBDA"
  service_role_arn = aws_iam_role.appsync_datasource.arn

  lambda_config {
    function_arn = aws_lambda_function.api_handler.arn
  }
}

# ── リゾルバー（JavaScriptランタイム） ─────────────────────────────
# リゾルバー: GraphQLクエリをバックエンドのデータソース操作に変換する処理です。
# AWSはVTL（Velocity Template Language）より可読性の高いJavaScriptランタイムを推奨。

# Query.getItem リゾルバー（DynamoDB GetItem）
resource "aws_appsync_resolver" "get_item" {
  api_id      = aws_appsync_graphql_api.main.id
  type        = "Query"
  field       = "getItem"
  data_source = aws_appsync_datasource.dynamodb.name
  kind        = "UNIT" # 単一データソースへの1操作

  runtime {
    name            = "APPSYNC_JS"
    runtime_version = "1.0.0"
  }

  code = <<-JS
    // PoC品質: エラーハンドリング・バリデーションを追加してください
    import { util } from '@aws-appsync/utils'
    import * as ddb from '@aws-appsync/utils/dynamodb'

    // リクエストハンドラー: GraphQL引数をDynamoDB操作に変換
    export function request(ctx) {
      return ddb.get({
        key: {
          pk: ctx.args.pk,
          sk: ctx.args.sk,
        }
      })
    }

    // レスポンスハンドラー: DynamoDBの結果をGraphQLレスポンスに変換
    export function response(ctx) {
      if (ctx.error) {
        util.error(ctx.error.message, ctx.error.type)
      }
      return ctx.result
    }
  JS

  depends_on = [aws_appsync_schema.main]
}

# Query.listItems リゾルバー（DynamoDB Scan + ページネーション）
resource "aws_appsync_resolver" "list_items" {
  api_id      = aws_appsync_graphql_api.main.id
  type        = "Query"
  field       = "listItems"
  data_source = aws_appsync_datasource.dynamodb.name
  kind        = "UNIT"

  runtime {
    name            = "APPSYNC_JS"
    runtime_version = "1.0.0"
  }

  code = <<-JS
    import { util } from '@aws-appsync/utils'
    import * as ddb from '@aws-appsync/utils/dynamodb'

    export function request(ctx) {
      // 本番環境ではScanよりQueryを推奨（パーティションキー指定で効率的）
      return ddb.scan({
        limit:             ctx.args.limit ?? 20,
        nextToken:         ctx.args.nextToken,
        consistentRead:    false, // Eventually Consistent（コスト半分）
      })
    }

    export function response(ctx) {
      if (ctx.error) {
        util.error(ctx.error.message, ctx.error.type)
      }
      return {
        items:     ctx.result.items,
        nextToken: ctx.result.nextToken,
      }
    }
  JS

  depends_on = [aws_appsync_schema.main]
}

# Mutation.createItem パイプラインリゾルバー（認可チェック → DynamoDB書き込み）
# Pipeline Resolver: 複数のFunctionを直列実行する高度なリゾルバー
resource "aws_appsync_function" "authorize_create" {
  api_id      = aws_appsync_graphql_api.main.id
  name        = "AuthorizeCreate"
  data_source = aws_appsync_datasource.dynamodb.name

  runtime {
    name            = "APPSYNC_JS"
    runtime_version = "1.0.0"
  }

  code = <<-JS
    // Pipeline Function 1: 認可チェック
    // ctx.identity でログインユーザー情報を取得できます
    export function request(ctx) {
      // 認証済みユーザーのみ通過（AppSyncが事前検証済み）
      // 追加の認可ロジック（例: グループチェック）をここに実装
      ctx.stash.userId = ctx.identity.sub  // Cognitoユーザー固有ID
      return {} // NONEデータソースのリクエストは空オブジェクト
    }

    export function response(ctx) {
      return ctx.prev.result
    }
  JS
}

resource "aws_appsync_function" "write_item" {
  api_id      = aws_appsync_graphql_api.main.id
  name        = "WriteItem"
  data_source = aws_appsync_datasource.dynamodb.name

  runtime {
    name            = "APPSYNC_JS"
    runtime_version = "1.0.0"
  }

  code = <<-JS
    import { util } from '@aws-appsync/utils'
    import * as ddb from '@aws-appsync/utils/dynamodb'

    // Pipeline Function 2: DynamoDB書き込み
    export function request(ctx) {
      const input = ctx.args.input
      return ddb.put({
        key: { pk: input.pk, sk: input.sk },
        item: {
          ...input,
          createdAt: util.time.nowISO8601(),
          createdBy: ctx.stash.userId,  // Function 1で設定したユーザーID
        },
        condition: { pk: { attributeExists: false } }, // 重複防止
      })
    }

    export function response(ctx) {
      if (ctx.error) {
        util.error(ctx.error.message, ctx.error.type)
      }
      return ctx.result
    }
  JS
}

resource "aws_appsync_resolver" "create_item" {
  api_id = aws_appsync_graphql_api.main.id
  type   = "Mutation"
  field  = "createItem"
  kind   = "PIPELINE" # Pipeline Resolver

  runtime {
    name            = "APPSYNC_JS"
    runtime_version = "1.0.0"
  }

  # Functionを直列実行（authorize_create → write_item）
  pipeline_config {
    functions = [
      aws_appsync_function.authorize_create.function_id,
      aws_appsync_function.write_item.function_id,
    ]
  }

  # Before/Afterステップ（前処理・後処理）
  code = <<-JS
    // Before step: パイプライン全体の前処理
    export function request(ctx) {
      return {}
    }
    // After step: 最終レスポンスの整形
    export function response(ctx) {
      return ctx.prev.result
    }
  JS

  depends_on = [aws_appsync_schema.main]
}

# CloudWatchロググループ（AppSync用）
resource "aws_cloudwatch_log_group" "appsync" {
  name              = "/aws/appsync/apis/${aws_appsync_graphql_api.main.id}"
  retention_in_days = 30
}
