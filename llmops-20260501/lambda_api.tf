# PoC品質 - 本番環境での使用前に十分なセキュリティレビューが必要です
# Lambda（推論処理）+ API Gateway（HTTPエンドポイント）を定義します
#
# アーキテクチャ概要:
#   クライアント → API Gateway (REST) → Lambda → Amazon Bedrock
#                                               ↕
#                                     OpenSearch Serverless（RAG検索）

# ═══════════════════════════════════════════════════════════════════════════════
# Lambda関数コード（Python 3.12）
# archive_file でインラインコードをzip化し、Lambdaにアップロードします
# ═══════════════════════════════════════════════════════════════════════════════

data "archive_file" "inference_lambda" {
  type        = "zip"
  output_path = "${path.module}/inference_lambda.zip"

  source {
    filename = "index.py"
    content  = <<-PYTHON
# PoC品質 - 本番環境での使用前に十分なレビューが必要です
"""
LLMOps 推論Lambda関数
API GatewayおよびStep Functionsの両方から呼び出し可能な統合ハンドラー

対応アクション:
  - validate       : 入力バリデーション
  - retrieve       : RAG検索（ベクトルDB検索）
  - infer          : Bedrock推論（メイン処理）
  - evaluate       : LLM-as-Judgeによる品質評価
  - log_low_quality: 低品質レスポンスのCloudWatch記録
"""
import json
import os
import logging
import boto3
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 環境変数から設定を読み込む（認証情報は絶対にハードコードしないこと）
BEDROCK_MODEL_ID     = os.environ.get("BEDROCK_MODEL_ID", "")
PROMPTS_BUCKET       = os.environ.get("PROMPTS_BUCKET", "")
OPENSEARCH_ENDPOINT  = os.environ.get("OPENSEARCH_ENDPOINT", "")
QUALITY_THRESHOLD    = float(os.environ.get("QUALITY_THRESHOLD", "0.7"))

bedrock_runtime = boto3.client("bedrock-runtime")
s3_client       = boto3.client("s3")


def handler(event: dict, context: Any) -> dict:
    """Lambda統合エントリポイント。API Gateway・Step Functions双方に対応。"""
    logger.info("event: %s", json.dumps(event, ensure_ascii=False, default=str))

    # API Gatewayからの呼び出し: bodyフィールドにJSONが格納されている
    if "body" in event:
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _api_response(400, {"error": "リクエストボディのJSON形式が不正です"})
        action = body.get("action", "infer")
        query  = body.get("query", "")
        payload = body
    else:
        # Step Functionsからの直接呼び出し
        action  = event.get("action", "infer")
        query   = event.get("query", "")
        payload = event

    try:
        if action == "validate":
            result = _validate_input(payload)
        elif action == "retrieve":
            result = _retrieve_context(payload)
        elif action == "infer":
            result = _invoke_bedrock(query, payload.get("context", {}))
        elif action == "evaluate":
            result = _evaluate_response(payload)
        elif action == "log_low_quality":
            result = _log_low_quality(payload)
        else:
            result = _invoke_bedrock(query, {})

        # Step Functionsからの呼び出しは辞書をそのまま返す
        if "body" not in event:
            return result

        return _api_response(200, result)

    except ValueError as e:
        logger.warning("Validation error: %s", str(e))
        return _api_response(400, {"error": str(e)})
    except Exception as e:
        logger.error("Unexpected error: %s", str(e), exc_info=True)
        return _api_response(500, {"error": "内部エラーが発生しました"})


def _validate_input(payload: dict) -> dict:
    """入力クエリのバリデーション。不正入力はここで早期に弾きます。"""
    query = payload.get("query", "").strip()

    if not query:
        raise ValueError("クエリが空です")
    if len(query) > 10_000:
        raise ValueError(f"クエリが長すぎます（{len(query)}文字 / 上限10,000文字）")

    return {"status": "valid", "query_length": len(query)}


def _retrieve_context(payload: dict) -> dict:
    """
    RAG検索: ベクトルDBから関連ドキュメントを取得します（スケルトン実装）

    TODO: OpenSearch Serverlessへの埋め込み（Embedding）検索を実装してください。
    実際の実装では以下の手順が必要です:
      1. Amazon Titan Embeddings等でクエリをベクトル化
      2. OpenSearch Serverlessにk-NNクエリを送信
      3. 上位K件のドキュメントを返す
    """
    query = payload.get("query", "")
    top_k = payload.get("top_k", 5)

    logger.info("RAG retrieve: query=%s, top_k=%d", query[:100], top_k)

    # スケルトン: ダミーデータを返す。実装時はOpenSearch検索結果に置き換える
    return {
        "documents": [],
        "metadata": {
            "source":   "opensearch_serverless",
            "endpoint": OPENSEARCH_ENDPOINT,
            "top_k":    top_k
        }
    }


def _invoke_bedrock(query: str, context: dict) -> dict:
    """
    Amazon Bedrock（Anthropic Claude）でテキスト生成を実行します。

    プロンプトキャッシング（Anthropic Cache Control）を活用することで、
    繰り返し使うシステムプロンプトのトークンコストを最大90%削減できます。
    最小キャッシュブロックサイズ: 1,024トークン
    """
    if not BEDROCK_MODEL_ID:
        raise ValueError("環境変数 BEDROCK_MODEL_ID が設定されていません")

    # コンテキストドキュメントをプロンプトに組み込む（RAGの中核）
    documents = (context.get("Payload") or context).get("documents", [])
    context_section = ""
    if documents:
        context_section = "\n\n## 参考情報（ベクトルDB検索結果）\n" + "\n".join(
            f"- {doc}" for doc in documents[:5]
        )

    # システムプロンプト（本番ではS3から取得し、バージョン管理することを推奨）
    system_prompt = (
        "あなたは正確で信頼性の高いAIアシスタントです。"
        "与えられた参考情報のみを根拠として回答し、"
        "情報がない場合は「わかりません」と正直に答えてください。"
        "ハルシネーション（事実と異なる情報の生成）は厳禁です。"
    )

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                # cache_control: システムプロンプトをキャッシュしてコスト削減
                "cache_control": {"type": "ephemeral"}
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": f"{context_section}\n\n## 質問\n{query}"
            }
        ]
    }

    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(request_body),
        contentType="application/json",
        accept="application/json"
    )

    result    = json.loads(response["body"].read())
    answer    = result["content"][0]["text"]
    usage     = result.get("usage", {})

    # トークン使用量をCloudWatch Logsに出力（メトリクスフィルターで集計します）
    logger.info(
        "bedrock_usage input_tokens=%d output_tokens=%d cache_read=%d model=%s",
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("cache_read_input_tokens", 0),
        BEDROCK_MODEL_ID
    )

    return {
        "answer":   answer,
        "model_id": BEDROCK_MODEL_ID,
        "usage":    usage
    }


def _evaluate_response(payload: dict) -> dict:
    """
    LLM-as-Judge: レスポンスの品質を評価します（スケルトン実装）

    本番実装では以下のRAGASメトリクスを評価することを推奨します:
      - Faithfulness    : 回答がコンテキストに忠実か（ハルシネーション検出）
      - Answer Relevancy: 回答が質問に関連しているか
      - Context Recall  : 正解情報がコンテキストに含まれているか

    TODO: Bedrock InvokeModelを使ってジャッジLLMを呼び出し、
          各メトリクスを0〜1のスコアで採点する処理を実装してください。
    注意: LLM-as-Judgeは本番の重要パスで同期実行せず、
          バックグラウンドで非同期にサンプリング（例: 5%）して評価するのがベストプラクティスです。
    """
    logger.info("evaluate: query=%s", str(payload.get("query", ""))[:100])

    # スケルトン: ダミースコアを返す。実装時はLLM評価結果に置き換える
    return {
        "quality_score": 0.85,
        "metrics": {
            "faithfulness":     0.90,
            "answer_relevancy": 0.82,
            "context_recall":   0.78
        }
    }


def _log_low_quality(payload: dict) -> dict:
    """
    品質閾値を下回ったレスポンスをCloudWatch Logsに記録します。
    CloudWatch Metric Filterがこのログパターンを検知してアラームを発報します。
    """
    # CloudWatch Metric Filterが "LOW_QUALITY_RESPONSE" を検出してカウントします
    logger.warning(
        "LOW_QUALITY_RESPONSE query=%s score=%s",
        str(payload.get("query", ""))[:200],
        str(payload.get("evaluation", {}).get("Payload", {}).get("quality_score", "N/A"))
    )
    return {"logged": True}


def _api_response(status_code: int, body: dict) -> dict:
    """API Gateway プロキシ統合レスポンス形式を生成します。"""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type":                "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, ensure_ascii=False, default=str)
    }
PYTHON
  }
}

# ═══════════════════════════════════════════════════════════════════════════════
# CloudWatch Logs グループ（Lambda用）
# Lambda関数が作成される前にロググループを明示的に作成することで
# 保持期間・暗号化を確実に設定します
# ═══════════════════════════════════════════════════════════════════════════════

resource "aws_cloudwatch_log_group" "lambda_inference" {
  name              = "/aws/lambda/${local.name_prefix}-inference"
  retention_in_days = var.log_retention_days
}

# ═══════════════════════════════════════════════════════════════════════════════
# Lambda 関数
# ═══════════════════════════════════════════════════════════════════════════════

resource "aws_lambda_function" "inference" {
  function_name = "${local.name_prefix}-inference"
  role          = aws_iam_role.lambda_execution.arn
  handler       = "index.handler"
  runtime       = "python3.12"
  timeout       = var.lambda_timeout
  memory_size   = var.lambda_memory_size

  filename         = data.archive_file.inference_lambda.output_path
  source_code_hash = data.archive_file.inference_lambda.output_base64sha256

  environment {
    variables = {
      BEDROCK_MODEL_ID    = var.bedrock_model_id
      PROMPTS_BUCKET      = aws_s3_bucket.prompts.bucket
      OPENSEARCH_ENDPOINT = aws_opensearchserverless_collection.vector_store.collection_endpoint
      QUALITY_THRESHOLD   = tostring(var.quality_score_threshold)
    }
  }

  # X-Ray = AWSの分散トレーシングサービス。リクエストの流れを可視化します
  tracing_config {
    mode = "Active"
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda_inference,
    aws_iam_role_policy_attachment.lambda_basic,
    aws_iam_role_policy.lambda_llmops
  ]
}

# ═══════════════════════════════════════════════════════════════════════════════
# API Gateway REST API
#
# REST API = クライアントがHTTP(S)経由でLambdaを呼び出すための窓口です。
# /v1/infer エンドポイントに POST リクエストを送ることでLLM推論を実行します。
#
# エンドポイント構成:
#   POST /v1/infer  → Lambda（推論処理）
#   OPTIONS /v1/infer → CORS対応（ブラウザからの直接呼び出しを許可）
# ═══════════════════════════════════════════════════════════════════════════════

resource "aws_api_gateway_rest_api" "llmops" {
  name        = "${local.name_prefix}-api"
  description = "LLMOps推論エンドポイント（PoC）"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# /v1 リソース
resource "aws_api_gateway_resource" "v1" {
  rest_api_id = aws_api_gateway_rest_api.llmops.id
  parent_id   = aws_api_gateway_rest_api.llmops.root_resource_id
  path_part   = "v1"
}

# /v1/infer リソース
resource "aws_api_gateway_resource" "infer" {
  rest_api_id = aws_api_gateway_rest_api.llmops.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "infer"
}

# POST /v1/infer
resource "aws_api_gateway_method" "infer_post" {
  rest_api_id   = aws_api_gateway_rest_api.llmops.id
  resource_id   = aws_api_gateway_resource.infer.id
  http_method   = "POST"
  authorization = "NONE"
  # PoC: 認証なし。本番では "AWS_IAM" または "COGNITO_USER_POOLS" を使用してください
}

# Lambda プロキシ統合（リクエスト/レスポンスをそのままLambdaに転送）
resource "aws_api_gateway_integration" "infer_post" {
  rest_api_id             = aws_api_gateway_rest_api.llmops.id
  resource_id             = aws_api_gateway_resource.infer.id
  http_method             = aws_api_gateway_method.infer_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.inference.invoke_arn
}

# OPTIONS /v1/infer（CORSプリフライトリクエスト対応）
# CORS = ブラウザのセキュリティ機能。異なるオリジンからのAPIアクセスを制御します
resource "aws_api_gateway_method" "infer_options" {
  rest_api_id   = aws_api_gateway_rest_api.llmops.id
  resource_id   = aws_api_gateway_resource.infer.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "infer_options" {
  rest_api_id = aws_api_gateway_rest_api.llmops.id
  resource_id = aws_api_gateway_resource.infer.id
  http_method = aws_api_gateway_method.infer_options.http_method
  type        = "MOCK"
  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

resource "aws_api_gateway_method_response" "infer_options_200" {
  rest_api_id = aws_api_gateway_rest_api.llmops.id
  resource_id = aws_api_gateway_resource.infer.id
  http_method = aws_api_gateway_method.infer_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "infer_options" {
  rest_api_id = aws_api_gateway_rest_api.llmops.id
  resource_id = aws_api_gateway_resource.infer.id
  http_method = aws_api_gateway_method.infer_options.http_method
  status_code = aws_api_gateway_method_response.infer_options_200.status_code
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,X-Amz-Date,Authorization,X-Api-Key'"
    "method.response.header.Access-Control-Allow-Methods" = "'OPTIONS,POST'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }

  depends_on = [aws_api_gateway_integration.infer_options]
}

# APIデプロイ（設定変更後は新しいdeploymentを作成してstageに紐付けます）
resource "aws_api_gateway_deployment" "llmops" {
  rest_api_id = aws_api_gateway_rest_api.llmops.id

  depends_on = [
    aws_api_gateway_integration.infer_post,
    aws_api_gateway_integration_response.infer_options
  ]

  lifecycle {
    create_before_destroy = true
  }
}

# APIステージ（環境ごとに分離。URL: https://{api-id}.execute-api.{region}.amazonaws.com/{stage}/）
resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${local.name_prefix}"
  retention_in_days = var.log_retention_days
}

resource "aws_api_gateway_stage" "llmops" {
  deployment_id = aws_api_gateway_deployment.llmops.id
  rest_api_id   = aws_api_gateway_rest_api.llmops.id
  stage_name    = var.environment

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
  }

  xray_tracing_enabled = true

  depends_on = [aws_cloudwatch_log_group.api_gateway]
}

# API GatewayがLambdaを呼び出す権限
resource "aws_lambda_permission" "api_gateway_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.inference.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.llmops.execution_arn}/*/*"
}

# ═══════════════════════════════════════════════════════════════════════════════
# 出力値
# ═══════════════════════════════════════════════════════════════════════════════

output "api_endpoint" {
  description = "LLMOps推論APIエンドポイントURL（POST /v1/inferを呼び出してください）"
  value       = "${aws_api_gateway_stage.llmops.invoke_url}/v1/infer"
}

output "lambda_function_name" {
  description = "Lambda関数名"
  value       = aws_lambda_function.inference.function_name
}

output "lambda_function_arn" {
  description = "Lambda関数ARN"
  value       = aws_lambda_function.inference.arn
}

output "api_gateway_id" {
  description = "API Gateway REST API ID"
  value       = aws_api_gateway_rest_api.llmops.id
}
