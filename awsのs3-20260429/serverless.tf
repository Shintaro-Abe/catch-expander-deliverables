# PoC品質: 本コードは概念実証用スケルトンです。本番利用前に十分なレビューを行ってください。
#
# サーバーレス構成: Lambda + API Gateway によるプレサインドURL発行API と
# S3 EventBridge → Lambda による非同期処理パイプラインを実装します。

# ─────────────────────────────────────────────
# Lambda: プレサインドURL発行（2ステップアップロードパターン）
# クライアント → API Gateway → Lambda（URL生成）→ クライアント → S3 直接アップロード
# ─────────────────────────────────────────────

# Lambda コード（インライン — PoC用）
data "archive_file" "presigned_url" {
  type        = "zip"
  output_path = "${path.module}/.build/presigned_url.zip"
  source {
    content  = <<-PYTHON
      # PoC品質: プレサインドURL生成Lambda
      import json
      import os
      import boto3
      from botocore.exceptions import ClientError

      s3_client = boto3.client("s3")
      BUCKET_NAME = os.environ["BUCKET_NAME"]
      EXPIRATION  = int(os.environ.get("PRESIGNED_URL_EXPIRATION", "3600"))

      def handler(event, context):
          body = json.loads(event.get("body") or "{}")
          object_key = body.get("key")
          if not object_key:
              return {"statusCode": 400, "body": json.dumps({"error": "key is required"})}

          # キーのパストラバーサル対策
          if ".." in object_key or object_key.startswith("/"):
              return {"statusCode": 400, "body": json.dumps({"error": "invalid key"})}

          try:
              url = s3_client.generate_presigned_url(
                  "put_object",
                  Params={"Bucket": BUCKET_NAME, "Key": f"uploads/{object_key}"},
                  ExpiresIn=EXPIRATION,
                  HttpMethod="PUT",
              )
          except ClientError as e:
              return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

          return {
              "statusCode": 200,
              "headers": {"Content-Type": "application/json"},
              "body": json.dumps({"upload_url": url, "expires_in": EXPIRATION}),
          }
    PYTHON
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "presigned_url" {
  function_name    = "${local.name_prefix}-presigned-url"
  role             = aws_iam_role.lambda_s3.arn
  handler          = "handler.handler"
  runtime          = var.lambda_runtime
  filename         = data.archive_file.presigned_url.output_path
  source_code_hash = data.archive_file.presigned_url.output_base64sha256
  timeout          = 10

  environment {
    variables = {
      BUCKET_NAME              = aws_s3_bucket.main.id
      PRESIGNED_URL_EXPIRATION = tostring(var.presigned_url_expiration_seconds)
    }
  }
  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "presigned_url" {
  name              = "/aws/lambda/${aws_lambda_function.presigned_url.function_name}"
  retention_in_days = 14
  tags              = local.common_tags
}

# ─────────────────────────────────────────────
# API Gateway REST API（プレサインドURL発行エンドポイント）
# POST /upload-url
# ─────────────────────────────────────────────
resource "aws_api_gateway_rest_api" "s3_api" {
  name        = "${local.name_prefix}-s3-api"
  description = "S3 presigned URL generation API"
  endpoint_configuration {
    types = ["REGIONAL"]
  }
  tags = local.common_tags
}

resource "aws_api_gateway_resource" "upload_url" {
  rest_api_id = aws_api_gateway_rest_api.s3_api.id
  parent_id   = aws_api_gateway_rest_api.s3_api.root_resource_id
  path_part   = "upload-url"
}

resource "aws_api_gateway_method" "post_upload_url" {
  rest_api_id   = aws_api_gateway_rest_api.s3_api.id
  resource_id   = aws_api_gateway_resource.upload_url.id
  http_method   = "POST"
  authorization = "AWS_IAM"
}

resource "aws_api_gateway_integration" "presigned_url" {
  rest_api_id             = aws_api_gateway_rest_api.s3_api.id
  resource_id             = aws_api_gateway_resource.upload_url.id
  http_method             = aws_api_gateway_method.post_upload_url.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.presigned_url.invoke_arn
}

resource "aws_lambda_permission" "apigw_presigned_url" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.presigned_url.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.s3_api.execution_arn}/*/*"
}

resource "aws_api_gateway_deployment" "s3_api" {
  rest_api_id = aws_api_gateway_rest_api.s3_api.id
  depends_on  = [aws_api_gateway_integration.presigned_url]
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.upload_url.id,
      aws_api_gateway_method.post_upload_url.id,
      aws_api_gateway_integration.presigned_url.id,
    ]))
  }
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "s3_api" {
  rest_api_id   = aws_api_gateway_rest_api.s3_api.id
  deployment_id = aws_api_gateway_deployment.s3_api.id
  stage_name    = var.environment
  tags          = local.common_tags
}

# ─────────────────────────────────────────────
# Lambda: S3イベント非同期処理（EventBridge経由）
# uploads/ にオブジェクトが作成されたら発火するサムネイル生成スケルトン
# ─────────────────────────────────────────────
data "archive_file" "s3_event_processor" {
  type        = "zip"
  output_path = "${path.module}/.build/s3_event_processor.zip"
  source {
    content  = <<-PYTHON
      # PoC品質: S3イベント処理Lambda（サムネイル生成スケルトン）
      import json
      import os
      import boto3

      s3_client      = boto3.client("s3")
      DEST_BUCKET    = os.environ.get("DEST_BUCKET", "")

      def handler(event, context):
          # EventBridgeイベント形式
          detail    = event.get("detail", {})
          src_bucket = detail.get("bucket", {}).get("name", "")
          src_key    = detail.get("object", {}).get("key", "")

          print(f"Processing: s3://{src_bucket}/{src_key}")

          # TODO: ここにサムネイル生成ロジックを実装（例: Pillowライブラリ使用）
          # obj = s3_client.get_object(Bucket=src_bucket, Key=src_key)
          # image_data = obj["Body"].read()
          # thumbnail = generate_thumbnail(image_data)
          # s3_client.put_object(Bucket=DEST_BUCKET, Key=f"thumbnails/{src_key}", Body=thumbnail)

          return {"statusCode": 200, "processed": src_key}
    PYTHON
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "s3_event_processor" {
  function_name    = "${local.name_prefix}-s3-event-processor"
  role             = aws_iam_role.lambda_s3.arn
  handler          = "handler.handler"
  runtime          = var.lambda_runtime
  filename         = data.archive_file.s3_event_processor.output_path
  source_code_hash = data.archive_file.s3_event_processor.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      DEST_BUCKET = aws_s3_bucket.main.id
    }
  }
  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "s3_event_processor" {
  name              = "/aws/lambda/${aws_lambda_function.s3_event_processor.function_name}"
  retention_in_days = 14
  tags              = local.common_tags
}

# EventBridge ルール: uploads/ へのオブジェクト作成イベントでLambdaを呼び出す
# （コンテンツベースフィルタリング — S3直接通知より高度）
resource "aws_cloudwatch_event_rule" "s3_object_created" {
  name        = "${local.name_prefix}-s3-object-created"
  description = "Trigger Lambda when objects are created under uploads/"
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.main.id] }
      object = { key = [{ prefix = "uploads/" }] }
    }
  })
  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "s3_event_processor" {
  rule      = aws_cloudwatch_event_rule.s3_object_created.name
  target_id = "S3EventProcessor"
  arn       = aws_lambda_function.s3_event_processor.arn
}

resource "aws_lambda_permission" "eventbridge_s3_event_processor" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.s3_event_processor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.s3_object_created.arn
}
