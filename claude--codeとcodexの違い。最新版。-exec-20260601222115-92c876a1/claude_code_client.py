# PoC品質: 概念実証用スケルトンです。本番利用前にエラーハンドリング・レート制限処理を追加してください。
"""
Claude Code スタイル コード生成クライアント
- Anthropic SDK (claude-sonnet-4-6 / claude-opus-4-7) を使用
- プロンプトキャッシュ（cache_control）でコスト最大90%削減
- ツール呼び出し（read_file / write_file）でエージェント的ファイル操作を模倣
"""

import os
import json
import argparse
from typing import Any

import anthropic

# ---- 定数 ----------------------------------------------------------------

# 2026年6月時点の推奨モデル
MODELS = {
    "sonnet": "claude-sonnet-4-6",   # バランス型・コスト効率◎
    "opus":   "claude-opus-4-7",     # 最高精度・SWE-bench 87.6%
    "haiku":  "claude-haiku-4-5-20251001",  # 軽量・低コスト
}

# キャッシュ可能なシステムプロンプト（5分TTL）
SYSTEM_PROMPT = """あなたはプロフェッショナルなAIソフトウェアエンジニアです。
ユーザーの依頼に対して、動作するPythonコードを生成してください。

【コーディング規約】
- 型アノテーションを必ず付ける
- docstringは簡潔に1行で
- セキュリティ上の問題（インジェクション等）がないコードを書く
- ハードコードされたシークレットは絶対に含めない

【出力形式】
コードブロック（```python）で囲んで出力してください。
"""

# ---- ツール定義（エージェント的ファイル操作）---------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "指定パスのファイル内容を読み込む（PoC: 実際には安全なサンドボックス内で動作させること）",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "読み込むファイルの相対パス"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "指定パスにコンテンツを書き込む（PoC: 実際には書き込み範囲を制限すること）",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "書き込み先の相対パス"},
                "content": {"type": "string", "description": "書き込むコンテンツ"},
            },
            "required": ["path", "content"],
        },
    },
]

# ---- ツール実行ハンドラ（PoC実装）--------------------------------------

def handle_tool_call(tool_name: str, tool_input: dict[str, Any]) -> str:
    """ツール呼び出しの結果を返すハンドラ（PoC: 実際の実装では権限チェックが必須）"""
    if tool_name == "read_file":
        path = tool_input["path"]
        # PoC: 実際にはサンドボックス内のパスのみ許可する
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        return f"[ERROR] ファイルが見つかりません: {path}"

    if tool_name == "write_file":
        path = tool_input["path"]
        content = tool_input["content"]
        # PoC: 実際にはホワイトリスト内ディレクトリのみ許可する
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[OK] {path} に書き込みました"

    return f"[ERROR] 未知のツール: {tool_name}"

# ---- メインクライアント ------------------------------------------------

class ClaudeCodeClient:
    """
    Claude Code スタイルのエージェント型コード生成クライアント。
    プロンプトキャッシュとツール呼び出しで長セッションのコストを削減する。
    """

    def __init__(self, model: str = MODELS["sonnet"], max_turns: int = 5):
        self.client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から自動取得
        self.model = model
        self.max_turns = max_turns
        self._usage_log: list[dict[str, Any]] = []

    def generate_code(self, task: str, verbose: bool = True) -> str:
        """
        タスク指示からコードを生成する（エージェントループ付き）。

        プロンプトキャッシュにより、システムプロンプトが繰り返し送られても
        キャッシュヒット時のコストは通常の10%（最大90%削減）。
        """
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": task}
        ]

        final_response = ""

        for turn in range(self.max_turns):
            if verbose:
                print(f"\n[Turn {turn + 1}/{self.max_turns}] APIリクエスト送信中...")

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                # キャッシュ制御: システムプロンプトを5分間キャッシュ
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},  # ← キャッシュキー
                    }
                ],
                tools=TOOLS,
                messages=messages,
            )

            # トークン使用量をログ（コスト管理用）
            self._log_usage(response.usage, turn)

            if verbose:
                self._print_usage(response.usage)

            # ストップ条件の確認
            if response.stop_reason == "end_turn":
                # テキストブロックを結合して最終レスポンスを取得
                final_response = "\n".join(
                    block.text
                    for block in response.content
                    if hasattr(block, "text")
                )
                break

            # ツール呼び出し処理（エージェントループ）
            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []

                for block in response.content:
                    if block.type == "tool_use":
                        if verbose:
                            print(f"  [Tool] {block.name}({json.dumps(block.input, ensure_ascii=False)})")
                        result = handle_tool_call(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                messages.append({"role": "user", "content": tool_results})
                continue

            # 予期しないストップ理由
            print(f"[WARN] 予期しないstop_reason: {response.stop_reason}")
            break

        return final_response

    def _log_usage(self, usage: Any, turn: int) -> None:
        self._usage_log.append({
            "turn": turn,
            "input_tokens":          getattr(usage, "input_tokens", 0),
            "output_tokens":         getattr(usage, "output_tokens", 0),
            "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0),
            "cache_read_tokens":     getattr(usage, "cache_read_input_tokens", 0),
        })

    def _print_usage(self, usage: Any) -> None:
        cache_read = getattr(usage, "cache_read_input_tokens", 0)
        cache_created = getattr(usage, "cache_creation_input_tokens", 0)
        print(
            f"  [Usage] input={usage.input_tokens} / output={usage.output_tokens} "
            f"/ cache_read={cache_read} / cache_created={cache_created}"
        )

    def print_cost_summary(self) -> None:
        """セッション全体のコスト試算をターミナル出力する"""
        # Sonnet 4.6 単価（$/1Mトークン）
        PRICE = {
            "input":         3.00,
            "output":       15.00,
            "cache_write":   3.75,
            "cache_read":    0.30,
        }
        total_cost = 0.0
        print("\n" + "=" * 50)
        print("セッションコストサマリー")
        print("=" * 50)
        for log in self._usage_log:
            turn_cost = (
                log["input_tokens"]          / 1_000_000 * PRICE["input"]
                + log["output_tokens"]       / 1_000_000 * PRICE["output"]
                + log["cache_creation_tokens"] / 1_000_000 * PRICE["cache_write"]
                + log["cache_read_tokens"]   / 1_000_000 * PRICE["cache_read"]
            )
            total_cost += turn_cost
            print(
                f"  Turn {log['turn'] + 1}: "
                f"in={log['input_tokens']:,} / out={log['output_tokens']:,} / "
                f"cache_read={log['cache_read_tokens']:,} → ${turn_cost:.4f}"
            )
        print(f"\n  合計推定コスト: ${total_cost:.4f} (Sonnet 4.6 基準)")
        print("=" * 50)


# ---- CLI エントリポイント -----------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Claude Code スタイル コード生成クライアント (PoC)")
    parser.add_argument("--task",    default="FizzBuzzをPythonで型アノテーション付きで実装してください", help="生成タスク")
    parser.add_argument("--model",   default="sonnet", choices=list(MODELS.keys()), help="使用モデル")
    parser.add_argument("--turns",   default=5, type=int, help="最大エージェントターン数")
    parser.add_argument("--verbose", action="store_true", default=True, help="詳細ログを表示")
    args = parser.parse_args()

    client = ClaudeCodeClient(model=MODELS[args.model], max_turns=args.turns)
    print(f"\n[Claude Code Client] モデル: {MODELS[args.model]}")
    print(f"タスク: {args.task}\n")

    result = client.generate_code(task=args.task, verbose=args.verbose)

    print("\n" + "=" * 50)
    print("生成結果:")
    print("=" * 50)
    print(result)

    client.print_cost_summary()


if __name__ == "__main__":
    main()
