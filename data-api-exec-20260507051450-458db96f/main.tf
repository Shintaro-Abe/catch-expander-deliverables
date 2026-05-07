# PoC品質: 本番環境での使用前に、セキュリティレビューおよびパラメータ見直しを必ず実施してください。

terraform {
  required_version = ">= 1.6"
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

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

# ── ローカル値（共通で参照する変数） ─────────────────────────────
locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

# ════════════════════════════════════════════════════════════════════
# VPC・ネットワーク（Aurora用。最小構成）
# ════════════════════════════════════════════════════════════════════

# 既存VPCを使う場合は data "aws_vpc" で参照してください
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.name_prefix}-vpc" }
}

# AZを2つ取得（RDSのマルチAZ要件を満たすため）
data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${local.name_prefix}-private-${count.index + 1}" }
}

resource "aws_db_subnet_group" "main" {
  name       = "${local.name_prefix}-db-subnet-group"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "aurora" {
  name        = "${local.name_prefix}-aurora-sg"
  description = "Aurora Serverless v2 へのアクセス制御"
  vpc_id      = aws_vpc.main.id

  # Lambda（同じVPC）からのPostgreSQLアクセスのみ許可
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.lambda.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "lambda" {
  name        = "${local.name_prefix}-lambda-sg"
  description = "Lambda関数用セキュリティグループ"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ════════════════════════════════════════════════════════════════════
# Cognito User Pool（ユーザー認証基盤）
# ════════════════════════════════════════════════════════════════════
# Cognitoはユーザーのサインアップ/サインインを管理し、JWT（JWTトークン）を発行します。
# API GatewayはこのJWTを検証して認可を行います。

resource "aws_cognito_user_pool" "main" {
  name = "${local.name_prefix}-user-pool"

  # パスワードポリシー（本番環境ではより厳格にすることを推奨）
  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  # メールアドレスによるアカウント確認
  auto_verified_attributes = ["email"]

  # MFA設定（本番環境では OPTIONAL または ON を推奨）
  mfa_configuration = "OFF"

  # アカウント復旧設定
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "api" {
  name         = "${local.name_prefix}-api-client"
  user_pool_id = aws_cognito_user_pool.main.id

  # 認証フロー: USER_SRP_AUTH がセキュアな標準方式
  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  # トークン有効期限（分）
  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

# ════════════════════════════════════════════════════════════════════
# Aurora Serverless v2 + RDS Data API
# ════════════════════════════════════════════════════════════════════
# RDS Data APIを有効にすると、Lambda関数がVPC内に置かれていなくても
# HTTPS経由でSQLを実行できます。接続プール管理も不要になります。
# 制限: Writerインスタンスのみ対応、レスポンスサイズ上限1MiB

resource "aws_rds_cluster" "main" {
  cluster_identifier = "${local.name_prefix}-cluster"

  # Aurora PostgreSQL を使用（MySQLも選択可能）
  engine         = "aurora-postgresql"
  engine_mode    = "provisioned" # Serverless v2はprovisioned modeで設定
  engine_version = "15.4"

  database_name       = var.db_name
  master_username     = var.db_master_username

  # パスワードはSecrets Managerで自動管理（ハードコード不要）
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.aurora.id]

  # ★ RDS Data APIを有効化（HTTPSでSQL実行が可能になります）
  enable_http_endpoint = true

  # Serverless v2のスケーリング設定
  serverlessv2_scaling_configuration {
    min_capacity = var.aurora_min_capacity # 最小0.5 ACU（使用しない時間帯に節約）
    max_capacity = var.aurora_max_capacity # スパイク時の上限
  }

  # 削除保護（本番環境では true に設定してください）
  deletion_protection = false
  skip_final_snapshot = true # PoC用: 本番では false に設定
}

# Serverless v2はインスタンスクラス "db.serverless" を指定
resource "aws_rds_cluster_instance" "writer" {
  identifier         = "${local.name_prefix}-writer"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version
}

# ════════════════════════════════════════════════════════════════════
# DynamoDB テーブル（KVS/NoSQL）
# ════════════════════════════════════════════════════════════════════
# DynamoDB: ミリ秒レイテンシの高速CRUD操作に最適なNoSQLデータベース。
# スキーマレスのため、フレキシブルなデータ構造に対応。

resource "aws_dynamodb_table" "items" {
  name         = "${local.name_prefix}-items"
  billing_mode = var.dynamodb_billing_mode # PAY_PER_REQUEST推奨（スパイク対応）
  hash_key     = "pk"  # パーティションキー（プライマリキーの必須部分）
  range_key    = "sk"  # ソートキー（プライマリキーの省略可能な第2部分）

  attribute {
    name = "pk"
    type = "S" # String型
  }

  attribute {
    name = "sk"
    type = "S"
  }

  # GSI（グローバルセカンダリインデックス）: 別軸での検索を可能にする
  global_secondary_index {
    name            = "sk-pk-index"
    hash_key        = "sk"
    range_key       = "pk"
    projection_type = "ALL" # 全属性をコピー
  }

  # ポイントインタイムリカバリ（PITR）: 最大35日前の状態に復元可能
  point_in_time_recovery {
    enabled = true
  }

  # 保存時の暗号化（デフォルトでAWS管理キー使用）
  server_side_encryption {
    enabled = true
  }

  # DynamoDB Streams: データ変更をLambdaでキャプチャするためのCDC機能
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES" # 変更前後のデータを両方取得
}

# ════════════════════════════════════════════════════════════════════
# IAM ロール（Lambda実行用）
# ════════════════════════════════════════════════════════════════════

resource "aws_iam_role" "lambda_exec" {
  name = "${local.name_prefix}-lambda-exec-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Lambda基本実行権限（CloudWatchログ書き込み）
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# RDS Data APIへのアクセス権限
resource "aws_iam_role_policy" "lambda_data_api" {
  name = "${local.name_prefix}-lambda-data-api-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement",
          "rds-data:BatchExecuteStatement",
          "rds-data:BeginTransaction",
          "rds-data:CommitTransaction",
          "rds-data:RollbackTransaction",
        ]
        Resource = aws_rds_cluster.main.arn
      },
      {
        # Secrets Manager: DBパスワードを安全に取得
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_rds_cluster.main.master_user_secret[0].secret_arn
      },
      {
        # DynamoDBへのCRUD権限
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
    ]
  })
}

# ════════════════════════════════════════════════════════════════════
# Lambda 関数（APIバックエンドのスケルトン）
# ════════════════════════════════════════════════════════════════════
# Lambda: サーバー管理不要のサーバーレス関数実行環境。
# リクエストごとに課金され、自動スケールします。

# ダミーZIPを作成（実際のコードはCI/CDでデプロイする）
data "archive_file" "lambda_placeholder" {
  type        = "zip"
  output_path = "${path.module}/.terraform/lambda_placeholder.zip"

  source {
    content  = <<-PYTHON
      # PoC品質: 実際のビジネスロジックをここに実装してください
      import json
      import boto3
      import os

      rds_client = boto3.client('rds-data')
      dynamodb   = boto3.resource('dynamodb')

      DB_CLUSTER_ARN = os.environ['DB_CLUSTER_ARN']
      DB_SECRET_ARN  = os.environ['DB_SECRET_ARN']
      DB_NAME        = os.environ['DB_NAME']
      DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']

      def handler(event, context):
          """
          API Gatewayからのリクエストを処理するメインハンドラー。
          event['httpMethod'] と event['path'] でルーティングします。
          """
          http_method = event.get('httpMethod', 'GET')
          path        = event.get('path', '/')

          if path == '/items' and http_method == 'GET':
              return get_items_from_dynamodb()
          elif path == '/sql' and http_method == 'POST':
              return execute_sql(event)
          else:
              return {'statusCode': 404, 'body': json.dumps({'error': 'Not found'})}

      def get_items_from_dynamodb():
          """DynamoDBからアイテム一覧を取得"""
          table = dynamodb.Table(DYNAMODB_TABLE)
          response = table.scan(Limit=20)  # 本番ではQueryを推奨
          return {
              'statusCode': 200,
              'headers': {'Content-Type': 'application/json'},
              'body': json.dumps(response.get('Items', []))
          }

      def execute_sql(event):
          """RDS Data APIでSQLを実行（Aurora Serverless v2）"""
          body = json.loads(event.get('body', '{}'))
          sql  = body.get('sql', 'SELECT NOW()')  # PoC: 実際はSQLインジェクション対策必須

          response = rds_client.execute_statement(
              resourceArn = DB_CLUSTER_ARN,
              secretArn   = DB_SECRET_ARN,
              database    = DB_NAME,
              sql         = sql,
              # パラメータ化クエリ例: parameters=[{'name': 'id', 'value': {'longValue': 1}}]
          )
          return {
              'statusCode': 200,
              'headers': {'Content-Type': 'application/json'},
              'body': json.dumps({'records': response.get('records', [])})
          }
    PYTHON
    filename = "index.py"
  }
}

resource "aws_lambda_function" "api_handler" {
  function_name = "${local.name_prefix}-api-handler"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "index.handler"
  runtime       = "python3.12"
  timeout       = var.lambda_timeout_sec
  memory_size   = var.lambda_memory_mb

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  # VPC設定（AuroraへのプライベートアクセスにはVPCへの配置が必要）
  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      DB_CLUSTER_ARN = aws_rds_cluster.main.arn
      DB_SECRET_ARN  = aws_rds_cluster.main.master_user_secret[0].secret_arn
      DB_NAME        = var.db_name
      DYNAMODB_TABLE = aws_dynamodb_table.items.name
    }
  }

  depends_on = [aws_rds_cluster_instance.writer]
}

# Provisioned Concurrency（コールドスタート対策）
# lambda_provisioned_concurrency > 0 の場合のみ有効
resource "aws_lambda_alias" "live" {
  name             = "live"
  function_name    = aws_lambda_function.api_handler.function_name
  function_version = aws_lambda_function.api_handler.version
}

resource "aws_lambda_provisioned_concurrency_config" "main" {
  count = var.lambda_provisioned_concurrency > 0 ? 1 : 0

  function_name                     = aws_lambda_function.api_handler.function_name
  qualifier                         = aws_lambda_alias.live.name
  provisioned_concurrent_executions = var.lambda_provisioned_concurrency
}
