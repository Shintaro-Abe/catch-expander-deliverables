# PoC品質 - 本番環境への適用前に十分なレビューと改修を行ってください
"""
AWS Data API サンプル: AppSync GraphQL — Lambda リゾルバー

概要:
  - AppSync の Lambda リゾルバーとして動作するハンドラーを実装する
  - Query / Mutation / Subscription ルーティングパターンを示す
  - マルチ認証モード（Cognito + IAM + API Key）の認証コンテキスト取得方法を含む
  - DynamoDB への Direct Resolver（Lambdaなし）との使い分け方針もコメントで補足

用語補足:
  - リゾルバー  : GraphQL のフィールドに対して実際のデータ取得/変更を行う関数
  - ミューテーション: GraphQL でのデータ変更操作（REST の POST/PUT/DELETE に相当）
  - サブスクリプション: GraphQL のリアルタイム更新機能（WebSocket ベース）
  - ctx.identity: AppSync が設定する認証情報オブジェクト

メリット（AppSync + Lambda リゾルバー）:
  ✅ 複数データソース（DynamoDB + Aurora + 外部 API 等）を単一 GraphQL で統合できる
  ✅ Cognito グループ・IAM ロール・API キーをフィールドレベルで組み合わせ可能
  ✅ クライアントが必要なフィールドだけ取得できる（Over-fetching を防止）

デメリット:
  ❌ AppSync のリクエスト単価は API Gateway HTTP API の約4倍（$4 vs $1 / 100万）
  ❌ DynamoDB 直接統合と比べ Lambda 起動コストが発生する
  ❌ GraphQL スキーマ設計・リゾルバー実装の学習コストが高い
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "posts")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)


# ════════════════════════════════════════════════════════
# 認証コンテキスト取得
# ════════════════════════════════════════════════════════

def _get_caller_identity(appsync_identity: dict | None) -> dict:
    """
    AppSync の認証コンテキスト（ctx.identity）からユーザー情報を取得する。

    AppSync はマルチ認証モードに対応しており、認証方式によって
    identity オブジェクトの構造が異なる:

      Cognito User Pools:
        identity.sub         → ユーザーの一意ID
        identity.username    → ユーザー名
        identity.groups      → 所属グループリスト

      IAM（管理操作向け）:
        identity.accountId   → AWS アカウントID
        identity.userArn     → IAM ユーザー/ロールの ARN

      API Key（パブリック読み取り向け）:
        identity は None

    戻り値:
      auth_type: "cognito" | "iam" | "api_key"
      user_id  : ユーザーの一意識別子（認証タイプ依存）
      groups   : 所属グループ（Cognito のみ）
    """
    if not appsync_identity:
        return {"auth_type": "api_key", "user_id": None, "groups": []}

    if "sub" in appsync_identity:
        return {
            "auth_type": "cognito",
            "user_id":   appsync_identity.get("sub"),
            "username":  appsync_identity.get("username"),
            "groups":    appsync_identity.get("groups", []),
        }

    if "accountId" in appsync_identity:
        return {
            "auth_type": "iam",
            "user_id":   appsync_identity.get("userArn"),
            "groups":    [],
        }

    return {"auth_type": "unknown", "user_id": None, "groups": []}


def _require_auth(caller: dict) -> None:
    """認証されていない呼び出し（API Key）を拒否する。"""
    if caller["auth_type"] == "api_key" or not caller["user_id"]:
        raise PermissionError("Authentication required")


def _require_group(caller: dict, group: str) -> None:
    """特定グループへの所属を要求する（ロールベースアクセス制御）。"""
    if group not in caller.get("groups", []):
        raise PermissionError(f"Group '{group}' membership required")


# ════════════════════════════════════════════════════════
# Query リゾルバー（データ取得）
# ════════════════════════════════════════════════════════

def resolve_get_post(args: dict, caller: dict) -> dict | None:
    """
    Query.getPost(id: ID!): Post

    パブリック読み取り可能（API Key でもアクセス可）。
    AppSync スキーマ:
      type Query {
        getPost(id: ID!): Post @aws_api_key @aws_cognito_user_pools
      }
    """
    result = table.get_item(Key={"id": args["id"]})
    return result.get("Item")


def resolve_list_posts(args: dict, caller: dict) -> dict:
    """
    Query.listPosts(limit: Int, nextToken: String): PostConnection

    limit / nextToken でページネーションを実装する。
    nextToken は LastEvaluatedKey を JSON 文字列化したもの。
    """
    limit = min(args.get("limit", 20), 100)
    kwargs: dict = {"Limit": limit}

    if next_token := args.get("nextToken"):
        kwargs["ExclusiveStartKey"] = json.loads(next_token)

    result = table.scan(**kwargs)
    next_token_out = (
        json.dumps(result["LastEvaluatedKey"]) if "LastEvaluatedKey" in result else None
    )

    return {
        "items":     result.get("Items", []),
        "nextToken": next_token_out,
    }


def resolve_get_my_posts(args: dict, caller: dict) -> list[dict]:
    """
    Query.getMyPosts: [Post!]! @aws_cognito_user_pools

    認証ユーザーのみアクセス可能。GSI を使ってオーナー別に検索する。
    """
    _require_auth(caller)

    result = table.query(
        IndexName="owner_id-index",
        KeyConditionExpression=Key("owner_id").eq(caller["user_id"]),
        ScanIndexForward=False,
    )
    return result.get("Items", [])


# ════════════════════════════════════════════════════════
# Mutation リゾルバー（データ変更）
# ════════════════════════════════════════════════════════

def resolve_create_post(args: dict, caller: dict) -> dict:
    """
    Mutation.createPost(input: CreatePostInput!): Post @aws_cognito_user_pools

    投稿作成: 認証ユーザーのみ、かつ 'authors' グループのメンバーのみ許可。
    """
    _require_auth(caller)
    _require_group(caller, "authors")

    inp = args["input"]
    title = inp.get("title", "").strip()
    if not title:
        raise ValueError("title is required")

    post = {
        "id":         str(uuid.uuid4()),
        "title":      title,
        "content":    inp.get("content", ""),
        "owner_id":   caller["user_id"],
        "published":  inp.get("published", False),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    table.put_item(
        Item=post,
        ConditionExpression="attribute_not_exists(id)",
    )
    logger.info("Post created: %s by %s", post["id"], caller["user_id"])
    return post


def resolve_update_post(args: dict, caller: dict) -> dict:
    """
    Mutation.updatePost(id: ID!, input: UpdatePostInput!): Post @aws_cognito_user_pools

    所有者のみ更新可能（FGAC）。
    """
    _require_auth(caller)

    inp = args["input"]
    post_id = args["id"]

    update_expr_parts = []
    expr_values: dict[str, Any] = {":uid": caller["user_id"]}
    expr_names:  dict[str, str] = {}

    if "title" in inp:
        update_expr_parts.append("#title = :title")
        expr_names["#title"] = "title"
        expr_values[":title"] = inp["title"]

    if "content" in inp:
        update_expr_parts.append("content = :content")
        expr_values[":content"] = inp["content"]

    if "published" in inp:
        update_expr_parts.append("published = :published")
        expr_values[":published"] = inp["published"]

    update_expr_parts.append("updated_at = :ts")
    expr_values[":ts"] = datetime.now(timezone.utc).isoformat()

    try:
        result = table.update_item(
            Key={"id": post_id},
            UpdateExpression="SET " + ", ".join(update_expr_parts),
            ConditionExpression="owner_id = :uid",
            ExpressionAttributeValues=expr_values,
            ExpressionAttributeNames=expr_names or None,
            ReturnValues="ALL_NEW",
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        raise PermissionError("Forbidden: not the post owner")

    return result["Attributes"]


def resolve_admin_delete_post(args: dict, caller: dict) -> bool:
    """
    Mutation.adminDeletePost(id: ID!): Boolean @aws_iam

    管理操作: IAM 認証（バックエンドサービス）のみアクセス可能。
    """
    if caller["auth_type"] != "iam":
        raise PermissionError("IAM authentication required for admin operations")

    table.delete_item(Key={"id": args["id"]})
    logger.info("Admin deleted post: %s by %s", args["id"], caller.get("user_id"))
    return True


# ════════════════════════════════════════════════════════
# メインルーター: AppSync の fieldName でディスパッチ
# ════════════════════════════════════════════════════════

# AppSync リゾルバーが呼び出すフィールド名 → ハンドラー関数のマッピング
RESOLVER_MAP: dict[str, Any] = {
    # Query
    "getPost":           resolve_get_post,
    "listPosts":         resolve_list_posts,
    "getMyPosts":        resolve_get_my_posts,
    # Mutation
    "createPost":        resolve_create_post,
    "updatePost":        resolve_update_post,
    "adminDeletePost":   resolve_admin_delete_post,
}


def handler(event: dict, context: object) -> Any:
    """
    AppSync Lambda リゾルバーのエントリポイント。

    AppSync から渡されるイベント構造:
      {
        "field":    "getPost",          // GraphQL フィールド名
        "arguments": {"id": "xxx"},     // GraphQL 引数
        "identity": {                   // 認証コンテキスト（認証方式により異なる）
          "sub": "...",
          "username": "...",
          "groups": [...]
        },
        "source":   null,              // 親リゾルバーの結果（ネストフィールド時に使用）
        "info": {
          "parentTypeName": "Query",
          "fieldName": "getPost"
        }
      }

    注意: AppSync Direct Resolver（VTL / JavaScript）は Lambda を介さないため
    シンプルな CRUD では直接統合の方がレイテンシ・コストともに有利。
    Lambda リゾルバーは複数データソースの集約や複雑なビジネスロジックに使う。
    """
    field_name = event.get("field") or event.get("info", {}).get("fieldName")
    args       = event.get("arguments", {})
    identity   = event.get("identity")

    logger.info("Resolving field: %s | auth: %s", field_name, identity)

    resolver = RESOLVER_MAP.get(field_name)
    if not resolver:
        raise ValueError(f"Unknown field: {field_name}")

    caller = _get_caller_identity(identity)

    try:
        return resolver(args, caller)
    except PermissionError as e:
        # AppSync はリゾルバーから例外が送出されるとエラーとして GraphQL レスポンスに含める
        logger.warning("Permission denied for field '%s': %s", field_name, e)
        raise
    except ValueError as e:
        logger.warning("Validation error for field '%s': %s", field_name, e)
        raise
    except Exception as e:
        logger.exception("Unexpected error for field '%s': %s", field_name, e)
        raise
