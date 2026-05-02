# PoC品質 - 本番利用前にセキュリティレビュー・入力バリデーションを行うこと
"""
ツールレジストリモジュール

設計原則:
  - ツールセットは最小限かつ単一目的に絞る（重複ツールは判断エラーの原因）
  - 各ツールは入力検証と出力のトークン効率化を担う
  - ツール定義（スキーマ）はAnthropicのtool_use API形式に準拠
"""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from typing import Any


# ---------------------------------------------------------------------------
# ツール基底クラス
# ---------------------------------------------------------------------------

class BaseTool(ABC):
    """
    全ツールの基底クラス。
    サブクラスは name・description・input_schema・execute を実装すること。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """ツール名（LLMがツール選択時に使用する識別子）"""

    @property
    @abstractmethod
    def description(self) -> str:
        """
        ツールの説明文。LLMが読むテキストなので明確・簡潔に書くこと。
        曖昧な説明は誤ったツール選択の原因になる。
        """

    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """JSON Schema形式の入力スキーマ"""

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        """ツールを実行して結果を返す"""

    def to_api_definition(self) -> dict:
        """Anthropic API の tools パラメータ形式に変換する"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# ---------------------------------------------------------------------------
# ツールレジストリ（集中管理）
# ---------------------------------------------------------------------------

class ToolRegistry:
    """
    ツールレジストリ（Tool Registry パターン）。
    厳選されたツールを集中管理し、LLM への定義提供とツール実行を担う。

    設計メモ:
      10個の明確に設計されたツールは50個の重複ツールより高性能。
      登録前にツール間の機能重複がないことを確認すること。
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """ツールを登録する"""
        if tool.name in self._tools:
            raise ValueError(f"ツール '{tool.name}' は既に登録済みです")
        self._tools[tool.name] = tool

    def get_definitions(self) -> list[dict]:
        """
        登録済みツールのAPI定義リストを返す。
        LLMに渡す tools パラメータとして使用する。
        """
        return [t.to_api_definition() for t in self._tools.values()]

    def execute(self, tool_name: str, tool_input: dict) -> Any:
        """
        ツール名と入力を受け取り、対応するツールを実行して結果を返す。

        Parameters
        ----------
        tool_name : str
            実行するツールの名前
        tool_input : dict
            LLMから渡された入力パラメータ

        Returns
        -------
        Any
            ツールの実行結果
        """
        if tool_name not in self._tools:
            return f"エラー: ツール '{tool_name}' は登録されていません。利用可能なツール: {list(self._tools.keys())}"

        tool = self._tools[tool_name]
        try:
            result = tool.execute(**tool_input)
            # 大容量出力（目安2000文字超）はトークン効率化のため切り詰める
            result_str = str(result)
            if len(result_str) > 8000:
                result_str = result_str[:8000] + "\n...[出力が長すぎるため切り詰めました]"
            return result_str
        except TypeError as exc:
            return f"エラー: 入力パラメータが不正です。{exc}"
        except Exception as exc:
            return f"エラー: ツール実行中に例外が発生しました。{exc}"

    def list_tools(self) -> list[str]:
        """登録済みツール名の一覧を返す"""
        return list(self._tools.keys())


# ---------------------------------------------------------------------------
# 組み込みツール実装
# ---------------------------------------------------------------------------

class EchoTool(BaseTool):
    """テスト用エコーツール（入力をそのまま返す）"""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "指定したメッセージをそのまま返すテスト用ツール。動作確認に使用する。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "エコーするメッセージ",
                }
            },
            "required": ["message"],
        }

    def execute(self, message: str) -> str:
        return f"Echo: {message}"


class ReadFileTool(BaseTool):
    """ファイルの内容を読み取るツール"""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "指定したファイルパスのテキスト内容を読み取って返す。"
            "バイナリファイルや大容量ファイル（10MB超）には使用しないこと。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "読み取るファイルのパス",
                },
                "start_line": {
                    "type": "integer",
                    "description": "読み取り開始行（省略時は先頭から）",
                },
                "end_line": {
                    "type": "integer",
                    "description": "読み取り終了行（省略時は末尾まで）",
                },
            },
            "required": ["path"],
        }

    def execute(self, path: str, start_line: int = 1, end_line: int = 200) -> str:
        # セキュリティ: パストラバーサル攻撃を防ぐ基本チェック
        # 本番では allowlist ベースのパス検証を追加すること
        if ".." in path or path.startswith("/etc") or path.startswith("/proc"):
            return f"エラー: パス '{path}' へのアクセスは禁止されています"

        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()

            selected = lines[start_line - 1 : end_line]
            numbered = [
                f"{start_line + i:4d}: {line.rstrip()}"
                for i, line in enumerate(selected)
            ]
            return "\n".join(numbered)
        except FileNotFoundError:
            return f"エラー: ファイル '{path}' が見つかりません"
        except PermissionError:
            return f"エラー: ファイル '{path}' への読み取り権限がありません"


class WriteFileTool(BaseTool):
    """ファイルに内容を書き込むツール"""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "指定したファイルパスにテキスト内容を書き込む。"
            "ファイルが存在する場合は上書きされる。システムファイルへの書き込みは禁止。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "書き込み先ファイルのパス",
                },
                "content": {
                    "type": "string",
                    "description": "書き込むテキスト内容",
                },
            },
            "required": ["path", "content"],
        }

    def execute(self, path: str, content: str) -> str:
        # セキュリティ: 書き込み禁止パスのチェック
        FORBIDDEN_PREFIXES = ["/etc", "/sys", "/proc", "/bin", "/usr/bin"]
        for prefix in FORBIDDEN_PREFIXES:
            if path.startswith(prefix):
                return f"エラー: '{path}' へのファイル書き込みは禁止されています"

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"成功: {path} に {len(content)} 文字を書き込みました"
        except PermissionError:
            return f"エラー: '{path}' への書き込み権限がありません"
        except OSError as exc:
            return f"エラー: ファイル書き込み失敗: {exc}"


class BashTool(BaseTool):
    """
    シェルコマンドを実行するツール。

    重要: このツールは強力な権限を持つため、
    AgentHarness のフック（example_block_hook 等）で
    危険なコマンドをブロックする実装を必ず追加すること。
    """

    def __init__(self, timeout: int = 30, allowed_commands: list[str] | None = None) -> None:
        # allowed_commands に非 None リストを渡すと許可コマンドのみ実行可能（最小権限）
        self.timeout = timeout
        self.allowed_commands = allowed_commands

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return (
            "Bashシェルコマンドを実行して stdout/stderr を返す。"
            "破壊的なコマンド（rm -rf 等）は実行前に必ずユーザー確認を行うこと。"
            f"タイムアウト: {self.timeout}秒"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "実行するBashコマンド",
                }
            },
            "required": ["command"],
        }

    def execute(self, command: str) -> str:
        # 許可コマンドリストによるフィルタリング（省略可能な追加セキュリティ層）
        if self.allowed_commands is not None:
            cmd_base = command.split()[0] if command.split() else ""
            if cmd_base not in self.allowed_commands:
                return (
                    f"エラー: コマンド '{cmd_base}' は許可リストにありません。"
                    f"許可コマンド: {self.allowed_commands}"
                )

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n[終了コード: {result.returncode}]"
            return output or "(出力なし)"
        except subprocess.TimeoutExpired:
            return f"エラー: コマンドがタイムアウト ({self.timeout}秒) しました"
        except Exception as exc:
            return f"エラー: コマンド実行失敗: {exc}"


class SearchKnowledgeTool(BaseTool):
    """
    外部知識ベース（ベクターDB等）を検索するツールのスケルトン。
    本番実装では Amazon Bedrock Knowledge Base や OpenSearch 等と接続する。
    """

    def __init__(self, knowledge_base_client: Any = None) -> None:
        self._kb_client = knowledge_base_client

    @property
    def name(self) -> str:
        return "search_knowledge"

    @property
    def description(self) -> str:
        return (
            "社内ドキュメント・FAQ・過去事例を意味的に検索して関連情報を返す。"
            "一般的なウェブ情報ではなく、組織固有の知識を探す場合に使用する。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索クエリ（自然言語で記述）",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返す検索結果の最大件数（デフォルト5）",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    def execute(self, query: str, top_k: int = 5) -> str:
        if self._kb_client is None:
            # スタブ実装: 本番ではベクターDB検索の結果を返す
            return json.dumps(
                {
                    "results": [
                        {
                            "id": "stub-001",
                            "content": f"[スタブ] クエリ '{query}' に関連するドキュメントがここに表示されます",
                            "score": 0.95,
                            "source": "knowledge_base/example.md",
                        }
                    ],
                    "total": 1,
                },
                ensure_ascii=False,
                indent=2,
            )

        # TODO: self._kb_client を使った実際の検索実装
        raise NotImplementedError("実際のknowledge_base_clientを渡してください")
