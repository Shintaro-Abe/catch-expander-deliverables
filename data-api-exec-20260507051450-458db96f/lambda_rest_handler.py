# PoC品質 - 本番環境への適用前に十分なレビューと改修を行ってください
"""
AWS Data API サンプル: API Gateway + Lambda (REST API ハンドラー)

概要:
  - API Gateway REST API のプロキシ統合を受け取る Lambda ハンドラー
  - Cognito JWT 認証・IAM 認証パターンを示す
  - スロットリング(429)・エラーハンドリングの実装例を含む

用語補足:
  - プロキシ統合 : API Gateway がリクエスト全体をそのまま Lambda に転送する方式
  - JWT          : JSON Web Token。ユーザー認証情報を含む署名済みトークン
  - コールドスタート: Lambda コンテナが初回起動するときの初期化遅延
"""

import json
import logging
import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# ── コンテナ初期化（コールドスタート時のみ実行）──────────────────────────────
# ハンドラー外で初期化することでウォームコンテナ時の遅延を削減
logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "items-table")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)


# ── 共通レスポンス生成 ───────────────────────────────────────────────────────

def _response(status_code: int, body: Any, headers: dict | None = None) -> dict:
    """API Gateway プロキシ統合用のレスポンスオブジェクトを生成する。"""
    default_headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",  # 本番では具体的なオリジンに制限すること
    }
    if headers:
        default_headers.update(headers)

    return {
        "statusCode": status_code,
        "headers": default_headers,
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def _error(status_code: int, message: str) -> dict:
    return _response(status_code, {"error": message})


# ── 認証コンテキスト取得 ─────────────────────────────────────────────────────

def _get_user_id(event: dict) -> str | None:
    """
    Cognito User Pools オーソライザーが付与した認証コンテキストからユーザーIDを取得する。

    API Gateway REST API + Cognito オーソライザー構成では、
    `requestContext.authorizer.claims` に JWT クレームが格納される。
    """
    try:
        claims = event["requestContext"]["authorizer"]["claims"]
        # Cognito の sub クレームはユーザーの一意識別子
        return claims.get("sub") or claims.get("cognito:username")
    except (KeyError, TypeError):
        return None


# ── ルーティング ─────────────────────────────────────────────────────────────

def _handle_get_items(event: dict) -> dict:
    """GET /items — テーブルを全件スキャン（ページネーション対応）"""
    # 本番ではユーザーID等でフィルタリングし、全スキャンは避けること
    query_params = event.get("queryStringParameters") or {}
    limit = min(int(query_params.get("limit", 20)), 100)

    exclusive_start_key = None
    if cursor := query_params.get("cursor"):
        # カーソルは LastEvaluatedKey を Base64 エンコードしたもの（実装省略）
        exclusive_start_key = json.loads(cursor)

    scan_kwargs: dict = {"Limit": limit}
    if exclusive_start_key:
        scan_kwargs["ExclusiveStartKey"] = exclusive_start_key

    result = table.scan(**scan_kwargs)
    next_cursor = json.dumps(result.get("LastEvaluatedKey")) if "LastEvaluatedKey" in result else None

    return _response(200, {
        "items": result.get("Items", []),
        "next_cursor": next_cursor,
        "count": result.get("Count", 0),
    })


def _handle_get_item(item_id: str) -> dict:
    """GET /items/{id} — 1件取得"""
    result = table.get_item(Key={"id": item_id})
    item = result.get("Item")
    if not item:
        return _error(404, f"Item '{item_id}' not found")
    return _response(200, item)


def _handle_create_item(event: dict) -> dict:
    """POST /items — 新規作成"""
    user_id = _get_user_id(event)
    if not user_id:
        return _error(401, "Unauthorized: missing authentication context")

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "Invalid JSON body")

    name = payload.get("name", "").strip()
    if not name:
        return _error(400, "Field 'name' is required")

    import uuid, datetime

    item = {
        "id": str(uuid.uuid4()),
        "name": name,
        "description": payload.get("description", ""),
        "owner_id": user_id,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }

    # condition_expression: 同一ID のアイテムが既に存在する場合は上書きしない
    table.put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(id)",
    )
    logger.info("Created item: %s by user: %s", item["id"], user_id)
    return _response(201, item)


def _handle_delete_item(event: dict, item_id: str) -> dict:
    """DELETE /items/{id} — 所有者のみ削除可能"""
    user_id = _get_user_id(event)
    if not user_id:
        return _error(401, "Unauthorized")

    # condition_expression でデータ所有者のみ削除を許可（FGAC: Fine-Grained Access Control）
    try:
        table.delete_item(
            Key={"id": item_id},
            ConditionExpression="owner_id = :uid",
            ExpressionAttributeValues={":uid": user_id},
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return _error(403, "Forbidden: you are not the owner of this item")

    return _response(204, {})


# ── メインハンドラー ─────────────────────────────────────────────────────────

def handler(event: dict, context: object) -> dict:
    """
    API Gateway REST API プロキシ統合のエントリポイント。

    ルーティング例:
      GET    /items       → 一覧取得
      GET    /items/{id}  → 1件取得
      POST   /items       → 新規作成
      DELETE /items/{id}  → 削除
    """
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}
    item_id = path_params.get("id")

    logger.info("Request: %s %s", method, path)

    try:
        if method == "GET" and path == "/items":
            return _handle_get_items(event)
        elif method == "GET" and item_id:
            return _handle_get_item(item_id)
        elif method == "POST" and path == "/items":
            return _handle_create_item(event)
        elif method == "DELETE" and item_id:
            return _handle_delete_item(event, item_id)
        else:
            return _error(404, f"Route not found: {method} {path}")

    except dynamodb.meta.client.exceptions.ProvisionedThroughputExceededException:
        # DynamoDB のスループット超過 → 503 を返してクライアントにリトライを促す
        logger.warning("DynamoDB throughput exceeded")
        return _error(503, "Service temporarily unavailable, please retry")

    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error: %s", exc)
        return _error(500, "Internal server error")
