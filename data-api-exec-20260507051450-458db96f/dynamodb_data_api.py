# PoC品質 - 本番環境への適用前に十分なレビューと改修を行ってください
"""
AWS Data API サンプル: DynamoDB API — Classic API vs PartiQL 比較実装

概要:
  - DynamoDB の主要 API 操作（CRUD）を Classic API と PartiQL の両方で示す
  - トランザクション・バッチ操作・ページネーションのパターンを含む
  - On-Demand / Provisioned の切替え方針もコメントで補足

用語補足:
  - PartiQL  : DynamoDB に対して SQL ライクな構文でクエリできる言語
  - GSI      : Global Secondary Index（グローバルセカンダリインデックス）。
               別の属性をキーとして検索するためのインデックス
  - WCU / RCU: 書き込み / 読み取り容量ユニット（Provisioned モードの課金単位）
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Iterator

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "orders")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
client = boto3.client("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(TABLE_NAME)


# ════════════════════════════════════════════════════════
# 1. Classic API — 基本 CRUD
# ════════════════════════════════════════════════════════

class OrderRepository:
    """DynamoDB Classic API を使ったオーダーテーブルの CRUD 実装。"""

    # ── 書き込み ────────────────────────────────────────

    def create_order(self, order: dict) -> dict:
        """
        PutItem で注文を新規作成する。
        条件式: 同じ order_id が存在しない場合のみ書き込む（冪等性の担保）。
        """
        try:
            table.put_item(
                Item=order,
                ConditionExpression="attribute_not_exists(order_id)",
            )
            logger.info("Created order: %s", order["order_id"])
            return order
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"Order {order['order_id']} already exists") from e
            raise

    def update_order_status(self, order_id: str, user_id: str, new_status: str) -> dict:
        """
        UpdateItem で注文ステータスを更新する。
        - 条件式でデータ所有者のみ更新を許可（FGAC）
        - アトミック操作のため部分更新でも整合性を保つ
        """
        import datetime

        try:
            result = table.update_item(
                Key={"order_id": order_id, "user_id": user_id},
                UpdateExpression="SET #s = :status, updated_at = :ts",
                ConditionExpression="user_id = :uid AND attribute_exists(order_id)",
                ExpressionAttributeNames={"#s": "status"},  # 'status' は予約語のためエイリアス使用
                ExpressionAttributeValues={
                    ":status": new_status,
                    ":ts": datetime.datetime.utcnow().isoformat(),
                    ":uid": user_id,
                },
                ReturnValues="ALL_NEW",  # 更新後の全属性を返す
            )
            return result["Attributes"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise PermissionError("Forbidden: not the owner or item not found") from e
            raise

    # ── 読み取り ────────────────────────────────────────

    def get_order(self, order_id: str, user_id: str) -> dict | None:
        """GetItem で 1 件取得（最終的整合性読み取り）。"""
        result = table.get_item(
            Key={"order_id": order_id, "user_id": user_id},
            ConsistentRead=False,  # Eventually Consistent（デフォルト）= コスト半減
        )
        return result.get("Item")

    def list_orders_by_user(self, user_id: str, limit: int = 20) -> Iterator[list[dict]]:
        """
        Query で GSI (user_id-index) を使いユーザー別注文を取得する。
        ページネーションを yield で処理し、大量データでもメモリ効率を維持する。
        """
        kwargs: dict = {
            "IndexName": "user_id-index",      # GSI 名
            "KeyConditionExpression": Key("user_id").eq(user_id),
            "ScanIndexForward": False,          # 降順（新しい順）
            "Limit": limit,
        }

        while True:
            result = table.query(**kwargs)
            yield result.get("Items", [])

            # LastEvaluatedKey がなければ全件取得完了
            if "LastEvaluatedKey" not in result:
                break
            kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]

    # ── バッチ操作 ──────────────────────────────────────

    def batch_get_orders(self, order_ids: list[str], user_id: str) -> list[dict]:
        """
        BatchGetItem で最大 100 件を 1 ネットワークラウンドトリップで取得する。

        注意: BatchGetItem はレスポンスが 1 MB を超えると自動で分割される。
        未処理のキーは UnprocessedKeys に返るためリトライが必要。
        """
        keys = [{"order_id": oid, "user_id": user_id} for oid in order_ids[:100]]
        result = dynamodb.batch_get_item(
            RequestItems={TABLE_NAME: {"Keys": keys, "ConsistentRead": False}}
        )

        items = result["Responses"].get(TABLE_NAME, [])
        # UnprocessedKeys がある場合は本番でリトライロジックを実装すること
        if result.get("UnprocessedKeys"):
            logger.warning("UnprocessedKeys exist — implement retry in production")

        return items

    def batch_write_orders(self, orders: list[dict]) -> None:
        """
        BatchWriteItem で最大 25 件を一括書き込みする。
        トランザクション不要・高スループット用途向け。
        """
        with table.batch_writer() as batch:
            for order in orders[:25]:
                batch.put_item(Item=order)

    # ── トランザクション ────────────────────────────────

    def transfer_item_between_orders(
        self,
        src_order_id: str,
        dst_order_id: str,
        user_id: str,
        item_name: str,
    ) -> None:
        """
        TransactWriteItems で複数テーブル・複数アイテムを ACID 保証で更新する。
        All-or-Nothing: 1 つでも失敗すると全操作がロールバックされる。
        """
        client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": TABLE_NAME,
                        "Key": {"order_id": {"S": src_order_id}, "user_id": {"S": user_id}},
                        "UpdateExpression": "REMOVE #items.#item",
                        "ConditionExpression": "attribute_exists(#items.#item)",
                        "ExpressionAttributeNames": {"#items": "items", "#item": item_name},
                    }
                },
                {
                    "Update": {
                        "TableName": TABLE_NAME,
                        "Key": {"order_id": {"S": dst_order_id}, "user_id": {"S": user_id}},
                        "UpdateExpression": "SET #items.#item = :true",
                        "ExpressionAttributeNames": {"#items": "items", "#item": item_name},
                        "ExpressionAttributeValues": {":true": {"BOOL": True}},
                    }
                },
            ]
        )


# ════════════════════════════════════════════════════════
# 2. PartiQL — SQL ライクな操作
# ════════════════════════════════════════════════════════

class PartiQLRepository:
    """
    DynamoDB PartiQL API を使った操作例。
    Classic API と同等の操作を SQL 構文で記述できるが、
    フルスキャンを避けるためパーティションキー条件を必ず含めること。
    """

    def find_pending_orders(self, user_id: str) -> list[dict]:
        """ExecuteStatement で pending ステータスの注文を取得する。"""
        statement = (
            "SELECT * FROM \"{}\" "
            "WHERE user_id = ? AND #status = ?"
        ).format(TABLE_NAME)

        result = client.execute_statement(
            Statement=statement,
            Parameters=[
                {"S": user_id},
                {"S": "pending"},
            ],
        )
        # DynamoDB の低レベルレスポンスを Python dict に変換
        from boto3.dynamodb.types import TypeDeserializer
        deserializer = TypeDeserializer()
        return [
            {k: deserializer.deserialize(v) for k, v in item.items()}
            for item in result.get("Items", [])
        ]

    def batch_update_status(self, order_ids: list[str], user_id: str, new_status: str) -> None:
        """
        BatchExecuteStatement で複数の UPDATE を 1 ネットワークラウンドトリップで実行する。
        トランザクション保証はない（失敗した操作のみエラーが返る）。
        """
        statements = [
            {
                "Statement": (
                    "UPDATE \"{}\" SET #status = ? WHERE order_id = ? AND user_id = ?"
                ).format(TABLE_NAME),
                "Parameters": [
                    {"S": new_status},
                    {"S": oid},
                    {"S": user_id},
                ],
            }
            for oid in order_ids
        ]

        result = client.batch_execute_statement(Statements=statements)
        errors = [r for r in result.get("Responses", []) if "Error" in r]
        if errors:
            logger.error("BatchExecuteStatement partial failures: %s", errors)


# ════════════════════════════════════════════════════════
# 3. テーブル作成ユーティリティ（初期セットアップ用）
# ════════════════════════════════════════════════════════

def create_orders_table(
    billing_mode: str = "PAY_PER_REQUEST",  # On-Demand（初期・スパイク向け）
) -> None:
    """
    注文テーブルを作成する。

    billing_mode の選択指針:
      - PAY_PER_REQUEST (On-Demand): 予測困難なトラフィック / 月1000万RQ以下
      - PROVISIONED              : 安定・予測可能 / 高頻度・コスト最適化
    """
    kwargs: dict[str, Any] = {
        "TableName": TABLE_NAME,
        "KeySchema": [
            {"AttributeName": "order_id", "KeyType": "HASH"},   # パーティションキー
            {"AttributeName": "user_id",  "KeyType": "RANGE"},  # ソートキー
        ],
        "AttributeDefinitions": [
            {"AttributeName": "order_id", "AttributeType": "S"},
            {"AttributeName": "user_id",  "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "user_id-index",
                "KeySchema": [
                    {"AttributeName": "user_id",    "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        "BillingMode": billing_mode,
        "StreamSpecification": {
            "StreamEnabled": True,
            "StreamViewType": "NEW_AND_OLD_IMAGES",  # CDC 用: 変更前後のデータを保持
        },
    }

    if billing_mode == "PROVISIONED":
        # 安定トラフィック向け: 予約容量と組み合わせると最大77%割引
        kwargs["ProvisionedThroughput"] = {
            "ReadCapacityUnits": 10,
            "WriteCapacityUnits": 5,
        }

    try:
        table_resource = dynamodb.create_table(**kwargs)
        table_resource.wait_until_exists()
        logger.info("Table '%s' created successfully", TABLE_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            logger.info("Table '%s' already exists", TABLE_NAME)
        else:
            raise


# ── サンプル実行 ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import datetime, uuid

    logging.basicConfig(level=logging.INFO)

    repo = OrderRepository()
    sample_order = {
        "order_id": str(uuid.uuid4()),
        "user_id": "user-001",
        "status": "pending",
        "items": {"apple": True, "banana": True},
        "total": Decimal("1500"),
        "created_at": datetime.datetime.utcnow().isoformat(),
    }

    created = repo.create_order(sample_order)
    print("Created:", created["order_id"])

    fetched = repo.get_order(created["order_id"], created["user_id"])
    print("Fetched:", fetched)
