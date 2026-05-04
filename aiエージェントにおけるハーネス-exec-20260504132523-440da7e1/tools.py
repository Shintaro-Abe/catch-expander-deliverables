# PoC品質: 本番利用前に認証・エラー処理・ビジネスロジックの追加が必要です

"""
ツール管理モジュール（Tool Management）
========================================
エージェントハーネスの「ツール管理」コンポーネント。
AIモデルが外部システムを操作するための「手段」を登録・管理します。

■ PoC のスコープ
  - ツールのデコレータ定義
  - ToolRegistry（登録・検索・スキーマ生成）
  - サンプルツール実装（ファイル読み込み・計算・Web検索スタブ）
"""

import functools
import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# データクラス: ツールの定義を保持する構造体
# ---------------------------------------------------------------------------
@dataclass
class ToolDefinition:
    """Anthropic SDK の tool_use 形式に合わせたツール定義。"""
    name: str                   # ツール名（Claudeが呼び出す際に使う識別子）
    description: str            # ツールの説明（Claudeがいつ使うかを判断するために使う）
    input_schema: dict          # JSON Schema 形式の入力パラメータ定義
    fn: Callable                # 実際に実行する Python 関数
    requires_approval: bool = False  # True の場合、実行前に人間の承認を要求する


# ---------------------------------------------------------------------------
# ToolRegistry: ツールを一元管理するレジストリ
# ---------------------------------------------------------------------------
class ToolRegistry:
    """
    ツールの登録・検索・スキーマ生成を担う中央レジストリ。

    使い方:
        registry = ToolRegistry()

        @registry.register(description="...", input_schema={...})
        def my_tool(param: str) -> str:
            return "result"
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        description: str,
        input_schema: dict,
        requires_approval: bool = False,
    ) -> Callable:
        """デコレータ: 関数をツールとして登録する。"""
        def decorator(fn: Callable) -> Callable:
            self._tools[fn.__name__] = ToolDefinition(
                name=fn.__name__,
                description=description,
                input_schema=input_schema,
                fn=fn,
                requires_approval=requires_approval,
            )
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)
            return wrapper
        return decorator

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def to_anthropic_schema(self) -> list[dict]:
        """
        Anthropic SDK の tools パラメータに渡せる形式に変換する。
        プロンプトキャッシュを活かすため、ツール定義は変更しない限り再利用される。
        """
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def execute(self, name: str, tool_input: dict) -> Any:
        """ツール名と入力を受け取って実行し、結果を返す。"""
        tool = self.get(name)
        if tool is None:
            raise ValueError(f"ツール '{name}' は登録されていません")
        return tool.fn(**tool_input)


# ---------------------------------------------------------------------------
# サンプルツールの登録
# ---------------------------------------------------------------------------
# アプリケーション全体で共有するデフォルトレジストリ
default_registry = ToolRegistry()


@default_registry.register(
    description=(
        "指定したファイルパスのテキストを読み込んで返します。"
        "コードや設定ファイルを確認するときに使ってください。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "読み込むファイルのパス（相対パスも可）",
            },
            "encoding": {
                "type": "string",
                "description": "文字エンコード（省略時は utf-8）",
                "default": "utf-8",
            },
        },
        "required": ["file_path"],
    },
)
def read_file(file_path: str, encoding: str = "utf-8") -> str:
    """ファイルを読み込んで内容を文字列で返すツール。"""
    try:
        with open(file_path, encoding=encoding) as f:
            return f.read()
    except FileNotFoundError:
        return f"エラー: ファイル '{file_path}' が見つかりません"
    except OSError as e:
        return f"エラー: {e}"


@default_registry.register(
    description=(
        "数式を評価して計算結果を返します。"
        "四則演算・べき乗・三角関数（sin/cos/tan）・対数（log）・平方根（sqrt）が使えます。"
        "例: '2 ** 10', 'sqrt(144)', 'sin(3.14159 / 2)'"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "評価する数式の文字列",
            },
        },
        "required": ["expression"],
    },
)
def calculate(expression: str) -> str:
    """安全な数式評価ツール。eval の代わりに許可リストで制限する。"""
    # セキュリティ対策: 使用可能な関数・定数を明示的に許可リスト化
    allowed_names = {
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "log": math.log,
        "log10": math.log10,
        "exp": math.exp,
        "pi": math.pi,
        "e": math.e,
        "abs": abs,
        "round": round,
    }
    try:
        result = eval(expression, {"__builtins__": {}}, allowed_names)  # noqa: S307
        return str(result)
    except Exception as e:
        return f"計算エラー: {e}"


@default_registry.register(
    description=(
        "指定したキーワードでウェブ検索を行い、上位の検索結果を返します。"
        "最新情報・ニュース・製品仕様など、学習データに含まれない情報を調べるときに使ってください。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "検索クエリ（自然言語でも可）",
            },
            "max_results": {
                "type": "integer",
                "description": "返す検索結果の最大件数（省略時は 5）",
                "default": 5,
            },
        },
        "required": ["query"],
    },
)
def web_search(query: str, max_results: int = 5) -> str:
    """
    ウェブ検索スタブ（PoC）。
    本番では SerpAPI / Tavily / Brave Search API 等を呼び出す。
    """
    # PoC: 実際のAPIキーなしでもテストできるようにダミーデータを返す
    dummy_results = [
        {"title": f"検索結果 {i+1}: {query}", "url": f"https://example.com/{i+1}", "snippet": f"「{query}」に関する情報..."}
        for i in range(min(max_results, 3))
    ]
    return json.dumps(dummy_results, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# デバッグ用: 登録されたツールの一覧を確認する
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== 登録済みツール一覧 ===")
    for schema in default_registry.to_anthropic_schema():
        print(f"\n[{schema['name']}]")
        print(f"  説明: {schema['description'][:60]}...")
        print(f"  必須パラメータ: {schema['input_schema'].get('required', [])}")
