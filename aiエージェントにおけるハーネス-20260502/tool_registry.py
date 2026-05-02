# PoC品質 - このコードは概念実証用です。本番環境での利用前に十分なテストと改修を行ってください。
"""
ツールレジストリ（Tool Registry）モジュール

AIエージェントが利用可能なツールを登録・管理・発見するための集中型カタログ機構です。

【初学者向け補足】
ツールレジストリは「会社の内線電話帳」に相当します。
誰（ツール）がどこにいて、何ができるかを一覧で管理し、
必要な時に適切な担当者（ツール）に繋ぐ仕組みです。
"""

from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# データクラス：ツールのメタデータ定義
# --------------------------------------------------------------------------- #

@dataclass
class ToolMetadata:
    """
    ツールのメタデータを保持するデータクラス。

    Attributes:
        name        : ツールの一意な名前（snake_case 推奨）
        description : ツールの説明（LLMへのプロンプト注入に使用）
        version     : セマンティックバージョン文字列
        tags        : セマンティック検索・フィルタリング用タグ
        enabled     : True のときのみエージェントから呼び出し可能
        registered_at: Unix タイムスタンプ（登録日時）
    """
    name: str
    description: str
    version: str = "1.0.0"
    tags: List[str] = field(default_factory=list)
    enabled: bool = True
    registered_at: float = field(default_factory=time.time)


@dataclass
class ToolEntry:
    """
    レジストリ内の1エントリ。メタデータと実行可能な関数をペアで保持します。
    """
    metadata: ToolMetadata
    func: Callable[..., Any]

    def call(self, **kwargs: Any) -> Any:
        """ツール関数を呼び出す。"""
        return self.func(**kwargs)

    def to_schema(self) -> Dict[str, Any]:
        """
        LLM へ提供する JSON スキーマ形式（Anthropic Converse API 互換）に変換します。
        型アノテーションから inputSchema を自動生成します。
        """
        sig = inspect.signature(self.func)
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            annotation = param.annotation
            json_type = _python_type_to_json(annotation)
            properties[param_name] = {"type": json_type, "description": f"{param_name} パラメータ"}
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        return {
            "toolSpec": {
                "name": self.metadata.name,
                "description": self.metadata.description,
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
                },
            }
        }


def _python_type_to_json(annotation: Any) -> str:
    """Python の型アノテーションを JSON Schema の型文字列に変換するヘルパー。"""
    mapping = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}
    return mapping.get(annotation, "string")


# --------------------------------------------------------------------------- #
# ToolRegistry：ツールの登録・発見・実行を担うコアクラス
# --------------------------------------------------------------------------- #

class ToolRegistry:
    """
    エージェントが利用するツールを一元管理するレジストリ。

    主な機能:
    - register()     : ツール関数をデコレータまたは直接呼び出しで登録
    - discover()     : タグ・キーワードでツールをセマンティック検索
    - get_schema()   : LLM へ提供するツールスキーマ一覧を返す
    - call()         : ツールを名前で呼び出す
    - health_check() : 全ツールの稼働確認
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolEntry] = {}
        self._call_log: List[Dict[str, Any]] = []  # 監査ログ

    # --------------------------------------------------------------------- #
    # 登録
    # --------------------------------------------------------------------- #

    def register(
        self,
        name: Optional[str] = None,
        description: str = "",
        version: str = "1.0.0",
        tags: Optional[List[str]] = None,
    ) -> Callable:
        """
        ツール登録デコレータ。

        使い方:
            @registry.register(name="web_search", description="Webを検索する", tags=["search"])
            def web_search(query: str) -> str:
                ...
        """
        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            tool_desc = description or (inspect.getdoc(func) or "")
            metadata = ToolMetadata(
                name=tool_name,
                description=tool_desc,
                version=version,
                tags=tags or [],
            )
            entry = ToolEntry(metadata=metadata, func=func)
            self._tools[tool_name] = entry
            logger.info("ツール登録完了: name=%s version=%s", tool_name, version)
            return func

        return decorator

    def register_direct(self, func: Callable, **kwargs: Any) -> None:
        """デコレータを使わずに直接ツールを登録する。"""
        self.register(**kwargs)(func)

    # --------------------------------------------------------------------- #
    # 発見
    # --------------------------------------------------------------------- #

    def discover(self, keyword: str = "", tags: Optional[List[str]] = None) -> List[ToolEntry]:
        """
        キーワードまたはタグでツールを検索する。

        Args:
            keyword : ツール名・説明に含まれるキーワード
            tags    : 一つでも一致するタグを持つツールを返す
        Returns:
            マッチしたツールのリスト（有効なもののみ）
        """
        results: List[ToolEntry] = []
        for entry in self._tools.values():
            if not entry.metadata.enabled:
                continue
            name_match = keyword.lower() in entry.metadata.name.lower() if keyword else True
            desc_match = keyword.lower() in entry.metadata.description.lower() if keyword else True
            tag_match = bool(set(tags or []) & set(entry.metadata.tags)) if tags else True
            if (name_match or desc_match) and tag_match:
                results.append(entry)
        return results

    def get(self, name: str) -> Optional[ToolEntry]:
        """名前でツールエントリを取得する。"""
        return self._tools.get(name)

    # --------------------------------------------------------------------- #
    # スキーマ生成（LLM 連携用）
    # --------------------------------------------------------------------- #

    def get_schema(self, allowed_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        LLM へ渡す toolConfig 形式のスキーマ一覧を生成する。

        Just-in-Time インジェクション原則:
        allowed_tools を指定することで、関係のないツールのスキーマを
        プロンプトから除外し、LLM のアテンション効率を向上させる。
        """
        schemas = []
        for name, entry in self._tools.items():
            if not entry.metadata.enabled:
                continue
            if allowed_tools is not None and name not in allowed_tools:
                continue
            schemas.append(entry.to_schema())
        return schemas

    # --------------------------------------------------------------------- #
    # 実行
    # --------------------------------------------------------------------- #

    def call(self, name: str, **kwargs: Any) -> Any:
        """
        ツールを名前で呼び出す。実行結果を監査ログに記録する。

        Raises:
            KeyError   : ツールが登録されていない場合
            ValueError : ツールが無効化されている場合
        """
        entry = self._tools.get(name)
        if entry is None:
            raise KeyError(f"ツール '{name}' はレジストリに存在しません")
        if not entry.metadata.enabled:
            raise ValueError(f"ツール '{name}' は現在無効化されています")

        start = time.time()
        try:
            result = entry.call(**kwargs)
            status = "success"
            return result
        except Exception as exc:
            status = "error"
            raise exc
        finally:
            elapsed = time.time() - start
            self._call_log.append({
                "tool": name,
                "kwargs": kwargs,
                "status": status,
                "elapsed_sec": round(elapsed, 4),
                "timestamp": time.time(),
            })
            logger.debug("ツール呼び出し: tool=%s status=%s elapsed=%.4fs", name, status, elapsed)

    # --------------------------------------------------------------------- #
    # ヘルスチェック
    # --------------------------------------------------------------------- #

    def health_check(self) -> Dict[str, str]:
        """
        全ツールの稼働状態を確認する。
        本実装では enabled フラグのみ確認するシンプルな版。
        本番では実際にエンドポイントへの疎通確認を行う。
        """
        return {
            name: ("healthy" if entry.metadata.enabled else "disabled")
            for name, entry in self._tools.items()
        }

    @property
    def audit_log(self) -> List[Dict[str, Any]]:
        """監査ログ（読み取り専用）を返す。"""
        return list(self._call_log)


# --------------------------------------------------------------------------- #
# サンプルツール定義（動作確認用）
# --------------------------------------------------------------------------- #

# グローバルレジストリインスタンス（シングルトン相当）
registry = ToolRegistry()


@registry.register(
    name="calculator",
    description="四則演算を実行する電卓ツール。数値の加減乗除ができます。",
    tags=["math", "utility"],
)
def calculator(expression: str) -> str:
    """
    安全な数式評価（eval の代わりに限定的な演算のみ対応）。

    Args:
        expression: "1 + 2 * 3" 形式の数式文字列
    Returns:
        計算結果の文字列
    """
    import ast
    import operator

    ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            return ops[type(node.op)](_eval(node.left), _eval(node.right))
        raise ValueError(f"サポートされていない演算: {node}")

    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval(tree.body)
        return str(result)
    except Exception as exc:
        return f"エラー: {exc}"


@registry.register(
    name="get_current_time",
    description="現在の日時を ISO 8601 形式で返す時刻取得ツール。",
    tags=["utility", "time"],
)
def get_current_time() -> str:
    """現在日時を返す。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@registry.register(
    name="echo",
    description="受け取ったテキストをそのまま返すエコーツール。デバッグ用。",
    tags=["debug", "utility"],
)
def echo(text: str) -> str:
    """入力をそのまま返す。"""
    return text


# --------------------------------------------------------------------------- #
# 動作確認エントリポイント
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("=== ツールレジストリ動作確認 ===\n")

    # 1. ヘルスチェック
    print("[1] ヘルスチェック:")
    for name, status in registry.health_check().items():
        print(f"  {name}: {status}")

    # 2. セマンティック検索
    print("\n[2] タグ 'utility' でツール検索:")
    found = registry.discover(tags=["utility"])
    for entry in found:
        print(f"  - {entry.metadata.name}: {entry.metadata.description}")

    # 3. スキーマ生成
    print("\n[3] LLM 向けスキーマ（calculator のみ）:")
    import json
    schemas = registry.get_schema(allowed_tools=["calculator"])
    print(json.dumps(schemas, ensure_ascii=False, indent=2))

    # 4. ツール呼び出し
    print("\n[4] ツール実行:")
    print("  calculator('2 + 3 * 4') =", registry.call("calculator", expression="2 + 3 * 4"))
    print("  get_current_time()      =", registry.call("get_current_time"))
    print("  echo(text='hello')      =", registry.call("echo", text="hello"))

    # 5. 監査ログ
    print("\n[5] 監査ログ（最新2件）:")
    for log in registry.audit_log[-2:]:
        print(f"  tool={log['tool']} status={log['status']} elapsed={log['elapsed_sec']}s")
