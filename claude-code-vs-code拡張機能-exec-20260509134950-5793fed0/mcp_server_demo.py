# PoC品質: このコードは動作確認用のスケルトンです。本番環境での使用には追加の検証が必要です。
#
# MCP（Model Context Protocol）サーバー デモ実装
# Claude Code VS Code拡張が使用するMCPサーバーの仕組みをPythonで再現したデモです。
# 実際のClaude Code VS Code拡張は組み込みMCPサーバー経由でIDE診断情報・
# Jupyterカーネルへのアクセスを提供します。このコードはその仕組みを学習目的で示します。

import json
import sys
from dataclasses import asdict, dataclass
from typing import Any, Optional

import anthropic


# ============================================================
# MCP ツール定義（VS Code組み込みMCPサーバーを模倣）
# ============================================================

# Claude Code VS Code拡張が組み込みで提供する2つのMCPツール:
# 1. mcp__ide__getDiagnostics: VS Code ProblemsパネルのエラーをClaudeに提供
# 2. mcp__ide__executeCode: Jupyterノートブックのコードを実行

IDE_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "mcp__ide__getDiagnostics",
        "description": (
            "VS Code Problemsパネルのエラー・警告・情報を取得する。"
            "TypeScriptのコンパイルエラー、Lintエラーなど静的解析の結果を参照できる。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "診断情報を取得するファイルのパス（省略時は全ファイル）",
                },
                "severity": {
                    "type": "string",
                    "enum": ["error", "warning", "information", "hint"],
                    "description": "フィルタリングする重大度レベル",
                },
            },
        },
    },
    {
        "name": "mcp__ide__executeCode",
        "description": (
            "Jupyterノートブックのカーネルでコードを実行する。"
            "実行前にユーザーへの確認ダイアログが表示される。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "実行するPythonコード",
                },
                "kernel_id": {
                    "type": "string",
                    "description": "実行対象のJupyterカーネルID",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "read_file",
        "description": "ファイルの内容を読み込む（Claude Codeのファイル読み込み機能に相当）",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "読み込むファイルのパス",
                },
                "start_line": {
                    "type": "integer",
                    "description": "読み込み開始行番号（省略時は先頭から）",
                },
                "end_line": {
                    "type": "integer",
                    "description": "読み込み終了行番号（省略時は末尾まで）",
                },
            },
            "required": ["path"],
        },
    },
]


# ============================================================
# ツール実行ハンドラ（サーバーサイド処理）
# ============================================================

@dataclass
class Diagnostic:
    """VS Code診断情報（エラー・警告）を表すデータクラス"""
    file_path: str
    line: int
    column: int
    severity: str  # "error" | "warning" | "information" | "hint"
    message: str
    source: str  # 診断を生成したツール（例: "typescript", "pylint"）


def handle_get_diagnostics(
    file_path: Optional[str] = None,
    severity: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    IDE診断情報を返すモックハンドラ。

    実際のVS Code拡張では、VS Code Language Server Protocol（LSP）経由で
    本物の診断情報を取得する。ここではデモ用のダミーデータを返す。
    """
    # デモ用のダミー診断データ
    mock_diagnostics = [
        Diagnostic(
            file_path="src/utils.py",
            line=42,
            column=8,
            severity="error",
            message="NameError: 'undefined_variable' is not defined",
            source="pylint",
        ),
        Diagnostic(
            file_path="src/api.py",
            line=15,
            column=0,
            severity="warning",
            message="W0611: Unused import 'os'",
            source="pylint",
        ),
        Diagnostic(
            file_path="src/models.py",
            line=88,
            column=4,
            severity="information",
            message="Consider using a dataclass instead of a plain class",
            source="mypy",
        ),
    ]

    results = [asdict(d) for d in mock_diagnostics]

    if file_path:
        results = [r for r in results if r["file_path"] == file_path]
    if severity:
        results = [r for r in results if r["severity"] == severity]

    return results


def handle_execute_code(code: str, kernel_id: Optional[str] = None) -> dict[str, Any]:
    """
    Jupyterコード実行のモックハンドラ。

    実際のVS Code拡張では、Jupyter拡張APIを通じてカーネルでコードを実行する。
    ここではデモ用のダミーレスポンスを返す。
    """
    return {
        "status": "success",
        "output": f"[デモモード] コードを実行しました (kernel_id={kernel_id or 'default'})\n{code[:100]}...",
        "execution_count": 1,
    }


def handle_read_file(
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> dict[str, Any]:
    """ファイル読み込みハンドラ"""
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return {"error": f"ファイル '{path}' が見つかりません"}

    lines = p.read_text(encoding="utf-8").splitlines()
    if start_line is not None:
        lines = lines[start_line - 1 :]
    if end_line is not None:
        lines = lines[: end_line - (start_line or 1) + 1]

    return {
        "content": "\n".join(lines),
        "total_lines": len(lines),
        "path": path,
    }


def process_tool_call(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """ツール呼び出しをディスパッチするルーター"""
    if tool_name == "mcp__ide__getDiagnostics":
        return handle_get_diagnostics(
            file_path=tool_input.get("file_path"),
            severity=tool_input.get("severity"),
        )
    elif tool_name == "mcp__ide__executeCode":
        return handle_execute_code(
            code=tool_input["code"],
            kernel_id=tool_input.get("kernel_id"),
        )
    elif tool_name == "read_file":
        return handle_read_file(
            path=tool_input["path"],
            start_line=tool_input.get("start_line"),
            end_line=tool_input.get("end_line"),
        )
    else:
        return {"error": f"未知のツール: {tool_name}"}


# ============================================================
# エージェントループ（Claude Codeの自律実行ループを模倣）
# ============================================================

def run_agent_loop(
    client: anthropic.Anthropic,
    user_message: str,
    max_iterations: int = 10,
) -> str:
    """
    Claude Codeのエージェントループを模倣する。

    Claude Code VS Code拡張は「ツール呼び出し → 結果返却 → 次のアクション判断」
    というループを、タスクが完了するまで自律的に繰り返す。
    このアーキテクチャをPythonで再現する。

    Args:
        client: Anthropicクライアント
        user_message: ユーザーからの指示
        max_iterations: 最大反復回数（無限ループ防止）

    Returns:
        str: 最終的なClaudeの回答
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_message}
    ]

    print(f"エージェントループ開始: '{user_message}'")
    print("-" * 50)

    for iteration in range(max_iterations):
        print(f"\n[反復 {iteration + 1}/{max_iterations}]")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            tools=IDE_MCP_TOOLS,
            messages=messages,
            system=(
                "あなたはIDE内で動作するコーディングアシスタントです。"
                "利用可能なツールを積極的に使ってタスクを完了してください。"
                "日本語で回答し、実行したアクションを明確に説明してください。"
            ),
        )

        print(f"  stop_reason: {response.stop_reason}")

        # ツール使用がなければ（または end_turn なら）ループ終了
        if response.stop_reason == "end_turn":
            final_text = next(
                (block.text for block in response.content if hasattr(block, "text")),
                "（回答なし）",
            )
            print(f"\n完了: {final_text[:200]}...")
            return final_text

        # ツール呼び出しを処理
        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"  ツール呼び出し: {block.name}({json.dumps(block.input, ensure_ascii=False)[:80]})")
                result = process_tool_call(block.name, block.input)
                print(f"  結果: {json.dumps(result, ensure_ascii=False)[:100]}...")
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        # メッセージ履歴にアシスタント応答とツール結果を追加
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    return "最大反復回数に達しました。タスクが完了しませんでした。"


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """MCPサーバーデモを実行する"""
    print("MCP（Model Context Protocol）サーバー デモ")
    print("=" * 60)
    print("Claude Code VS Code拡張が使用するMCPアーキテクチャを再現します\n")

    api_key = __import__("os").environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("デモモード（APIキー未設定）\n")
        print("定義されたMCPツール:")
        for tool in IDE_MCP_TOOLS:
            print(f"  - {tool['name']}: {tool['description'][:60]}...")
        print("\nエージェントループの動作:")
        print("  1. ユーザー指示を受信")
        print("  2. Claudeがツール呼び出しを決定（例: getDiagnostics）")
        print("  3. ツール結果をClaudeに返却")
        print("  4. タスク完了まで繰り返し")
        return

    client = anthropic.Anthropic(api_key=api_key)

    # エージェントループのデモ実行
    result = run_agent_loop(
        client,
        "プロジェクトのエラーと警告を確認して、修正方法を提案してください。",
    )
    print(f"\n最終回答:\n{result}")


if __name__ == "__main__":
    main()
