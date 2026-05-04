# =============================================================================
# PoC品質 (Proof of Concept)
# 本番環境での利用前に、セキュリティレビュー・詳細設定・業務ロジックの実装が必要です
# =============================================================================

# =============================================================================
# Lambda デプロイパッケージ (archive_file)
# -----------------------------------------------------------------------------
# archive_file: PythonソースコードをZIP化してLambdaにアップロード可能な形式に変換
# source_content: Terraformファイル内にPythonコードをインラインで記述 (PoC向け)
# 本番環境では: CI/CDパイプラインでビルド・パッケージングを実施することを推奨
# =============================================================================

# --- エントリポイント Lambda のソースコード ---
# 役割: API GatewayまたはEventBridgeからのリクエストを受け取り、Bedrock Agentへ転送
data "archive_file" "agent_entry_zip" {
  type                    = "zip"
  output_path             = "${path.module}/agent_entry.zip"
  source_content_filename = "agent_entry.py"

  source_content = <<-PYTHON
    # PoC品質 - 本番利用前にエラーハンドリング・認証・バリデーションを追加してください
    import json
    import os
    import uuid
    import boto3

    bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")


    def handler(event, context):
        """
        API Gateway (HTTP API) または EventBridge からのイベントを処理して
        Bedrock Agent のエージェントループを起動する
        """
        # イベント発生元に応じてリクエストボディを抽出
        if "body" in event:
            # API Gateway 経由 (同期リクエスト)
            body = json.loads(event.get("body") or "{}")
        else:
            # EventBridge 経由 (非同期イベント)
            body = event.get("detail", {})

        user_input = body.get("input", "").strip()
        session_id = body.get("session_id", str(uuid.uuid4()))

        if not user_input:
            return _response(400, {"error": "リクエストボディに 'input' フィールドが必要です"})

        try:
            # Bedrock Agent を呼び出してエージェントループを開始
            # invoke_agent はストリーミングレスポンスを返すイテレータ
            result = bedrock_agent_runtime.invoke_agent(
                agentId=os.environ["BEDROCK_AGENT_ID"],
                agentAliasId=os.environ["BEDROCK_AGENT_ALIAS_ID"],
                sessionId=session_id,
                inputText=user_input,
            )

            # ストリーミングチャンクを結合して最終レスポンスを構築
            completion = ""
            for event_data in result.get("completion", []):
                if "chunk" in event_data:
                    completion += event_data["chunk"]["bytes"].decode("utf-8")

            return _response(200, {
                "session_id": session_id,
                "response": completion,
            })

        except bedrock_agent_runtime.exceptions.ConflictException as e:
            # セッションが既に進行中の場合
            return _response(409, {"error": "セッション競合が発生しました", "detail": str(e)})
        except Exception as e:
            print(f"ERROR: {e}")
            return _response(500, {"error": "内部エラーが発生しました"})


    def _response(status_code, body):
        return {
            "statusCode": status_code,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False),
        }
  PYTHON
}

# --- ツール実行 Lambda のソースコード ---
# 役割: Bedrock Agent から Action Group の呼び出しを受け取り、ツールを実行して結果を返す
data "archive_file" "tool_executor_zip" {
  type                    = "zip"
  output_path             = "${path.module}/tool_executor.zip"
  source_content_filename = "tool_executor.py"

  source_content = <<-PYTHON
    # PoC品質 - 本番利用前に冪等性キー・エラーハンドリング・タイムアウトを実装してください
    import json
    import os
    from datetime import datetime, timezone
    import boto3

    dynamodb = boto3.resource("dynamodb")


    def handler(event, context):
        """
        Bedrock Agent (Action Group) からのツール呼び出しを処理する

        イベント形式:
          {
            "actionGroup": "ai-agent-harness-tools",
            "function": "search_knowledge",
            "parameters": [{"name": "query", "type": "string", "value": "..."}]
          }
        """
        function_name = event.get("function", "")
        # parameters はリスト形式 → dict に変換して扱いやすくする
        parameters = {
            p["name"]: p["value"]
            for p in event.get("parameters", [])
        }

        print(f"Tool called: {function_name}, params: {json.dumps(parameters, ensure_ascii=False)}")

        result = _dispatch(function_name, parameters)

        # Bedrock Agent が期待するレスポンス形式で返す (必須構造)
        return {
            "response": {
                "actionGroup": event.get("actionGroup", ""),
                "function": function_name,
                "functionResponse": {
                    "responseBody": {
                        "TEXT": {"body": json.dumps(result, ensure_ascii=False)}
                    }
                }
            }
        }


    def _dispatch(function_name, params):
        """ツール名に応じて処理をディスパッチ (分岐)"""
        dispatch_table = {
            "search_knowledge": _search_knowledge,
            "save_session_state": _save_session_state,
            "load_session_state": _load_session_state,
        }
        handler_fn = dispatch_table.get(function_name)
        if not handler_fn:
            return {"error": f"未知のツール: {function_name}"}
        try:
            return handler_fn(params)
        except Exception as e:
            print(f"Tool error [{function_name}]: {e}")
            return {"error": f"ツール実行エラー: {str(e)}"}


    def _search_knowledge(params):
        """
        知識ベースから情報を検索する
        TODO: OpenSearch / Bedrock Knowledge Base / 外部API へのクエリを実装
        """
        query = params.get("query", "")
        max_results = int(params.get("max_results", 5))
        # PoC: 実際の検索ロジックをここに実装
        return {
            "status": "ok",
            "query": query,
            "max_results": max_results,
            "results": [],  # TODO: 検索結果を返す
            "message": "[PoC] search_knowledge の業務ロジックを実装してください",
        }


    def _save_session_state(params):
        """
        セッション状態をDynamoDBに保存する
        冪等性: session_id + step_name を複合キーとして同じステップの重複書き込みを防ぐ
        """
        table = dynamodb.Table(os.environ["SESSION_TABLE_NAME"])
        session_id = params.get("session_id", "")
        state_data = params.get("state_data", "{}")
        step_name = params.get("step_name", "default")
        timestamp = datetime.now(timezone.utc).isoformat()

        # ttl: 24時間後に自動削除 (UNIX秒)
        ttl_value = int(datetime.now(timezone.utc).timestamp()) + 86400

        table.put_item(Item={
            "session_id": session_id,
            "timestamp": f"{step_name}#{timestamp}",
            "step_name": step_name,
            "state_data": state_data,
            "ttl": ttl_value,
        })

        return {
            "status": "saved",
            "session_id": session_id,
            "step_name": step_name,
            "timestamp": timestamp,
        }


    def _load_session_state(params):
        """
        以前保存したセッション状態をDynamoDBから読み込む
        step_name 省略時は最新のアイテムを返す
        """
        table = dynamodb.Table(os.environ["SESSION_TABLE_NAME"])
        session_id = params.get("session_id", "")
        step_name = params.get("step_name")

        if step_name:
            # 特定ステップのアイテムをクエリ
            response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key("session_id").eq(session_id)
                & boto3.dynamodb.conditions.Key("timestamp").begins_with(f"{step_name}#"),
                ScanIndexForward=False,
                Limit=1,
            )
        else:
            # 最新のアイテムを取得
            response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key("session_id").eq(session_id),
                ScanIndexForward=False,
                Limit=1,
            )

        items = response.get("Items", [])
        if not items:
            return {"status": "not_found", "session_id": session_id}

        return {
            "status": "ok",
            "session_id": session_id,
            "state_data": items[0].get("state_data", "{}"),
            "step_name": items[0].get("step_name"),
            "timestamp": items[0].get("timestamp"),
        }
  PYTHON
}

# =============================================================================
# Lambda 関数①: エージェントエントリポイント (agent_entry)
# -----------------------------------------------------------------------------
# 役割: ハーネスの「入口」として API Gateway および EventBridge からリクエストを受け取り
#       Bedrock Agent のエージェントループを起動する橋渡し関数
#
# timeout: API Gatewayのデフォルトタイムアウト(29秒)に近い値に設定
# environment: シークレットは含めない。ARN/設定のみ環境変数で渡す
# depends_on: ロググループを先に作成し、Lambda作成時にロールが確実に存在するようにする
# =============================================================================
resource "aws_lambda_function" "agent_entry" {
  function_name    = "${var.project_name}-agent-entry"
  filename         = data.archive_file.agent_entry_zip.output_path
  source_code_hash = data.archive_file.agent_entry_zip.output_base64sha256
  role             = aws_iam_role.lambda_entry_role.arn
  handler          = "agent_entry.handler"
  runtime          = "python3.12"
  timeout          = 29    # API Gatewayの最大統合タイムアウトに合わせる
  memory_size      = 256   # 256MB: 軽量な転送処理に十分

  environment {
    variables = {
      BEDROCK_AGENT_ID       = aws_bedrockagent_agent.harness_agent.agent_id
      BEDROCK_AGENT_ALIAS_ID = aws_bedrockagent_agent_alias.harness_agent_alias.agent_alias_id
      SESSION_TABLE_NAME     = aws_dynamodb_table.session_state.name
      LOG_LEVEL              = var.log_level
      POWERTOOLS_SERVICE_NAME = "${var.project_name}-agent-entry" # AWS Lambda Powertools用
    }
  }

  # 構造化ログ (JSON形式) を有効化 → CloudWatch Logs Insightsでクエリしやすくなる
  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.agent_entry_logs.name
  }

  depends_on = [
    aws_cloudwatch_log_group.agent_entry_logs,
    aws_iam_role_policy_attachment.lambda_entry_basic_execution,
    aws_iam_role_policy_attachment.lambda_entry_bedrock_policy,
  ]
}

# =============================================================================
# Lambda 関数②: ツール実行エグゼキューター (tool_executor)
# -----------------------------------------------------------------------------
# 役割: Bedrock Agent の Action Group Executor として、エージェントが選択した
#       ツール関数を実際に実行してDynamoDB/外部APIと連携する
#
# timeout: 外部API呼び出しを含むため長めに設定 (ツールごとに調整推奨)
# memory_size: 512MB: データ処理・複数ツール実行を考慮したサイジング
# =============================================================================
resource "aws_lambda_function" "tool_executor" {
  function_name    = "${var.project_name}-tool-executor"
  filename         = data.archive_file.tool_executor_zip.output_path
  source_code_hash = data.archive_file.tool_executor_zip.output_base64sha256
  role             = aws_iam_role.lambda_tool_role.arn
  handler          = "tool_executor.handler"
  runtime          = "python3.12"
  timeout          = 60    # ツール実行 (外部APIコール等) を考慮した余裕あるタイムアウト
  memory_size      = 512

  environment {
    variables = {
      SESSION_TABLE_NAME      = aws_dynamodb_table.session_state.name
      LOG_LEVEL               = var.log_level
      POWERTOOLS_SERVICE_NAME = "${var.project_name}-tool-executor"
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.tool_executor_logs.name
  }

  depends_on = [
    aws_cloudwatch_log_group.tool_executor_logs,
    aws_iam_role_policy_attachment.lambda_tool_basic_execution,
    aws_iam_role_policy_attachment.lambda_tool_dynamodb_policy,
  ]
}

# =============================================================================
# Lambda 実行権限 (Lambda Permission)
# -----------------------------------------------------------------------------
# Lambda 関数を呼び出せるサービスを明示的に許可する設定
# principal: 許可するAWSサービス識別子
# source_arn: より細かい制限 (特定のAPIや特定のエージェントのみに限定)
# =============================================================================

# API Gateway → agent_entry Lambda の呼び出し許可
resource "aws_lambda_permission" "api_gateway_invoke_entry" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent_entry.function_name
  principal     = "apigateway.amazonaws.com"
  # 特定のAPIステージ・メソッドのみに制限 (ワイルドカードは最小化)
  source_arn    = "${aws_apigatewayv2_api.harness_api.execution_arn}/*/*"
}

# EventBridge → agent_entry Lambda の呼び出し許可 (非同期起動)
resource "aws_lambda_permission" "eventbridge_invoke_entry" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent_entry.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.agent_trigger.arn
}

# Bedrock Agent → tool_executor Lambda の呼び出し許可 (Action Group実行)
resource "aws_lambda_permission" "bedrock_invoke_tool" {
  statement_id  = "AllowBedrockAgentInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.tool_executor.function_name
  principal     = "bedrock.amazonaws.com"
  # 特定のエージェントARNのみに制限 (最小権限の原則)
  source_arn    = aws_bedrockagent_agent.harness_agent.agent_arn
}

# =============================================================================
# API Gateway HTTP API (v2): エージェントへの同期リクエスト受け口
# -----------------------------------------------------------------------------
# HTTP API: REST API よりシンプルで低コスト。JWT認証・CORS設定が組み込み済み
# cors_configuration: ブラウザからの直接呼び出しを許可する場合に設定
# =============================================================================
resource "aws_apigatewayv2_api" "harness_api" {
  name          = "${var.project_name}-api"
  protocol_type = "HTTP"
  description   = "AIエージェントハーネスへのエントリポイント API"

  cors_configuration {
    allow_headers = ["Content-Type", "Authorization"]
    allow_methods = ["POST", "OPTIONS"]
    allow_origins = ["*"] # 本番では特定オリジンに制限してください
    max_age       = 300
  }
}

# API Gateway → Lambda Lambda統合
resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                 = aws_apigatewayv2_api.harness_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.agent_entry.invoke_arn
  payload_format_version = "2.0" # Lambda関数のイベント形式バージョン
}

# POST /invoke ルート定義
resource "aws_apigatewayv2_route" "invoke_route" {
  api_id    = aws_apigatewayv2_api.harness_api.id
  route_key = "POST /invoke"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

# API Gateway デフォルトステージ (自動デプロイ有効)
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.harness_api.id
  name        = "$default"
  auto_deploy = true # ルート変更時に自動デプロイ (PoC向け設定)

  # アクセスログを CloudWatch に出力
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access_logs.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
      integrationError = "$context.integrationErrorMessage"
    })
  }
}

# API Gateway アクセスログ用ロググループ
resource "aws_cloudwatch_log_group" "api_access_logs" {
  name              = "/aws/apigatewayv2/${var.project_name}"
  retention_in_days = var.log_retention_days
}
