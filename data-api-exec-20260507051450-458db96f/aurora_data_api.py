# PoC品質 - 本番環境への適用前に十分なレビューと改修を行ってください
"""
AWS Data API サンプル: Aurora Serverless v2 — RDS Data API

概要:
  - RDS Data API を使って Lambda から Aurora に TCP 接続なしで SQL を実行する
  - Secrets Manager による認証情報管理パターンを示す
  - Aurora Data API の制限事項（1MiB レスポンス制限など）を考慮した実装

用語補足:
  - RDS Data API: Aurora DBクラスターに対するHTTPSエンドポイント。
                  VPCやDB接続プールの管理が不要でサーバーレス向き
  - ACU         : Aurora Capacity Unit。Serverless v2 の課金単位（秒単位課金）
  - Secrets Manager: AWS でパスワードや認証情報を安全に管理するサービス

メリット:
  ✅ Lambda から VPC 設定なしで Aurora に接続できる
  ✅ 接続プール管理が不要（コネクション数の上限を気にしなくてよい）
  ✅ Secrets Manager で認証情報を一元管理

デメリット:
  ❌ レスポンスサイズ上限が 1 MiB（大量データの一括取得には不向き）
  ❌ Reader インスタンスへのルーティング不可（Writer のみ）
  ❌ Performance Insights で Data API 経由のクエリを監視できない
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# 環境変数から設定を取得（ハードコードしない）
CLUSTER_ARN = os.environ["AURORA_CLUSTER_ARN"]
SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DATABASE    = os.environ.get("DB_NAME", "app_db")
REGION      = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1")

# RDS Data API クライアント（コールドスタート対策でハンドラー外に配置）
rds_data = boto3.client("rds-data", region_name=REGION)


# ════════════════════════════════════════════════════════
# ユーティリティ: SQL パラメータ / レスポンス変換
# ════════════════════════════════════════════════════════

def _param(name: str, value: Any) -> dict:
    """
    Python の値を RDS Data API のパラメータ形式に変換する。

    RDS Data API はパラメータを型付き辞書で受け取る:
      {"name": "param_name", "value": {"stringValue": "..."}}

    プレースホルダーは SQL 中で :param_name の形式で使用する。
    """
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    if isinstance(value, bool):
        return {"name": name, "value": {"booleanValue": value}}
    if isinstance(value, int):
        return {"name": name, "value": {"longValue": value}}
    if isinstance(value, float):
        return {"name": name, "value": {"doubleValue": value}}
    return {"name": name, "value": {"stringValue": str(value)}}


def _rows_to_dicts(result: dict) -> list[dict]:
    """
    ExecuteStatement のレスポンスを Python の dict リストに変換する。

    RDS Data API は列名 (columnMetadata) と値 (records) を別々に返すため、
    組み合わせて使いやすい辞書形式に変換する。
    """
    columns = [col["name"] for col in result.get("columnMetadata", [])]
    rows = []
    for record in result.get("records", []):
        row = {}
        for col_name, field in zip(columns, record):
            # field は {"stringValue": "..."} / {"longValue": 1} 等
            value_key = next(iter(field))          # 型キーを取得
            if value_key == "isNull":
                row[col_name] = None
            else:
                row[col_name] = field[value_key]
            row[col_name] = field.get("stringValue") or field.get("longValue") \
                or field.get("doubleValue") or field.get("booleanValue") \
                or (None if field.get("isNull") else field)
        rows.append(row)
    return rows


# ════════════════════════════════════════════════════════
# 基本 SQL 実行ヘルパー
# ════════════════════════════════════════════════════════

def execute_sql(
    sql: str,
    parameters: list[dict] | None = None,
    transaction_id: str | None = None,
) -> dict:
    """
    ExecuteStatement を実行する共通関数。

    Args:
      sql           : 実行する SQL 文（プレースホルダーは :param_name 形式）
      parameters    : _param() で生成したパラメータリスト
      transaction_id: トランザクション内実行時に指定（begin_transaction() の戻り値）
    """
    kwargs: dict = {
        "resourceArn": CLUSTER_ARN,
        "secretArn":   SECRET_ARN,
        "database":    DATABASE,
        "sql":         sql,
        "includeResultMetadata": True,  # columnMetadata を含める
    }
    if parameters:
        kwargs["parameters"] = parameters
    if transaction_id:
        kwargs["transactionId"] = transaction_id

    try:
        return rds_data.execute_statement(**kwargs)
    except ClientError as e:
        logger.error("RDS Data API error: %s | SQL: %s", e, sql)
        raise


def batch_execute_sql(sql: str, parameter_sets: list[list[dict]]) -> dict:
    """
    BatchExecuteStatement: 同一 SQL を複数パラメータセットで一括実行する。
    INSERT/UPDATE の大量処理に有効（SELECT には使用不可）。
    """
    return rds_data.batch_execute_statement(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
        sql=sql,
        parameterSets=parameter_sets,
    )


# ════════════════════════════════════════════════════════
# トランザクション管理
# ════════════════════════════════════════════════════════

def begin_transaction() -> str:
    """トランザクションを開始し transaction_id を返す。"""
    result = rds_data.begin_transaction(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
    )
    return result["transactionId"]


def commit_transaction(transaction_id: str) -> None:
    rds_data.commit_transaction(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        transactionId=transaction_id,
    )
    logger.info("Transaction committed: %s", transaction_id)


def rollback_transaction(transaction_id: str) -> None:
    rds_data.rollback_transaction(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        transactionId=transaction_id,
    )
    logger.warning("Transaction rolled back: %s", transaction_id)


# ════════════════════════════════════════════════════════
# リポジトリ実装例: ユーザーテーブル
# ════════════════════════════════════════════════════════

class UserRepository:
    """Aurora Data API を使ったユーザーテーブルの CRUD 実装。"""

    def create_table(self) -> None:
        """初期セットアップ: テーブルを作成する（既存の場合はスキップ）。"""
        execute_sql("""
            CREATE TABLE IF NOT EXISTS users (
                id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
                email      VARCHAR(255) UNIQUE NOT NULL,
                name       VARCHAR(255) NOT NULL,
                is_active  BOOLEAN      NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            )
        """)
        logger.info("Table 'users' is ready")

    def create_user(self, email: str, name: str) -> dict:
        """INSERT — メールアドレスが重複する場合は例外を送出する。"""
        result = execute_sql(
            "INSERT INTO users (email, name) VALUES (:email, :name) RETURNING *",
            parameters=[_param("email", email), _param("name", name)],
        )
        rows = _rows_to_dicts(result)
        return rows[0] if rows else {}

    def get_user_by_email(self, email: str) -> dict | None:
        """SELECT — email でユーザーを検索する。"""
        result = execute_sql(
            "SELECT * FROM users WHERE email = :email",
            parameters=[_param("email", email)],
        )
        rows = _rows_to_dicts(result)
        return rows[0] if rows else None

    def list_active_users(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """
        SELECT — アクティブユーザー一覧を取得する（ページネーション対応）。

        注意: RDS Data API のレスポンスは 1 MiB が上限。
        大量データを返す場合は LIMIT / OFFSET で分割すること。
        """
        result = execute_sql(
            "SELECT id, email, name, created_at FROM users "
            "WHERE is_active = TRUE ORDER BY created_at DESC "
            "LIMIT :lim OFFSET :off",
            parameters=[_param("lim", limit), _param("off", offset)],
        )
        return _rows_to_dicts(result)

    def deactivate_users_batch(self, emails: list[str]) -> int:
        """
        BatchExecuteStatement — 複数ユーザーを一括で無効化する。
        各 email ごとに UPDATE を並列実行（一括ラウンドトリップ）。
        """
        parameter_sets = [[_param("email", e)] for e in emails]
        result = batch_execute_sql(
            "UPDATE users SET is_active = FALSE WHERE email = :email",
            parameter_sets=parameter_sets,
        )
        updated = sum(r.get("numberOfRecordsUpdated", 0) for r in result.get("updateResults", []))
        logger.info("Deactivated %d users", updated)
        return updated

    def transfer_credits_transactional(
        self,
        from_user_id: str,
        to_user_id: str,
        amount: int,
    ) -> None:
        """
        トランザクション — クレジットを安全に送金する（ACID 保証）。
        1 つでも失敗するとロールバックされる。
        """
        tx_id = begin_transaction()
        try:
            execute_sql(
                "UPDATE accounts SET balance = balance - :amount "
                "WHERE user_id = :uid AND balance >= :amount",
                parameters=[_param("amount", amount), _param("uid", from_user_id)],
                transaction_id=tx_id,
            )
            execute_sql(
                "UPDATE accounts SET balance = balance + :amount WHERE user_id = :uid",
                parameters=[_param("amount", amount), _param("uid", to_user_id)],
                transaction_id=tx_id,
            )
            commit_transaction(tx_id)
        except Exception:
            rollback_transaction(tx_id)
            raise


# ════════════════════════════════════════════════════════
# Lambda ハンドラー
# ════════════════════════════════════════════════════════

def handler(event: dict, context: object) -> dict:
    """
    Lambda エントリポイント。API Gateway または直接呼び出しに対応。

    Aurora Data API は Lambda と同じ VPC への配置が不要なため、
    シンプルなサーバーレス構成を実現できる。
    """
    logging.basicConfig(level=logging.INFO)
    action = event.get("action")
    repo = UserRepository()

    if action == "create":
        user = repo.create_user(
            email=event["email"],
            name=event["name"],
        )
        return {"statusCode": 201, "body": json.dumps(user, default=str)}

    if action == "get":
        user = repo.get_user_by_email(event["email"])
        if not user:
            return {"statusCode": 404, "body": json.dumps({"error": "Not found"})}
        return {"statusCode": 200, "body": json.dumps(user, default=str)}

    if action == "list":
        users = repo.list_active_users(
            limit=event.get("limit", 20),
            offset=event.get("offset", 0),
        )
        return {"statusCode": 200, "body": json.dumps(users, default=str)}

    return {"statusCode": 400, "body": json.dumps({"error": "Unknown action"})}
