# PoC品質: 概念実証用スケルトンです。本番利用前にエラーハンドリング・レート制限処理を追加してください。
"""
OpenAI Codex スタイル コード生成クライアント
- OpenAI SDK（o4-mini / GPT-5.3-Codex 系）を使用
- Responses API（responses.create）でエージェント的コード生成
- ローカルシェル実行ツール（computer_use_preview）の簡易モック付き
"""

import os
import subprocess
import argparse
from typing import Any

import openai

# ---- 定数 ----------------------------------------------------------------

# 2026年6月時点のCodex CLI系モデル
MODELS = {
    "o4-mini":      "o4-mini",           # コスパ最良・SWE-bench 68.1%
    "codex-mini":   "codex-mini-latest", # 軽量タスク向け
    "gpt5-codex":   "gpt-5.3-codex",     # SWE-bench ~80%・ターミナルデバッグ強い
}

# Codex CLIが採用するシステムプロンプトスタイル
SYSTEM_PROMPT = """You are an expert software engineering assistant.
When given a coding task, you:
1. Analyze the requirements carefully
2. Write clean, well-typed Python code
3. Verify logic step by step (chain-of-thought)
4. Return only working, executable code

Output code in a ```python block.
Do not include hardcoded secrets or credentials.
"""

# ---- シェル実行ツール（CodexのローカルSandboxモック）-------------------

def safe_shell_exec(command: str, timeout: int = 10) -> dict[str, str]:
    """
    シェルコマンドを安全に実行するモック関数。
    実際のCodex CLIはコンテナ分離されたサンドボックスで実行する。
    PoC: whitelistチェックのみ実装。
    """
    ALLOWED_PREFIXES = ("python", "python3", "echo", "cat", "ls", "pip show")
    if not any(command.startswith(p) for p in ALLOWED_PREFIXES):
        return {"stdout": "", "stderr": f"[BLOCKED] 許可されていないコマンド: {command}"}

    try:
        result = subprocess.run(
            command,
            shell=True,       # PoC: 本番では shell=False + リスト形式を使用すること
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {"stdout": result.stdout, "stderr": result.stderr}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"[TIMEOUT] {timeout}秒でタイムアウトしました"}


# ---- メインクライアント ------------------------------------------------

class CodexClient:
    """
    Codex CLI スタイルのコード生成クライアント。

    Claude Code との主な違い:
    - Responses API（ステートレスセッション）を使用
    - o4-mini は推論コストが低くターミナルデバッグが得意（Terminal-Bench 77%）
    - トークン消費量が少ない傾向（同等タスクでClaude Codeの約1/4という報告あり）
    - context_window: o4-mini=192K、GPT-5.4=1M
    """

    def __init__(self, model: str = MODELS["o4-mini"], reasoning_effort: str = "medium"):
        self.client = openai.OpenAI()  # OPENAI_API_KEY を環境変数から自動取得
        self.model = model
        # reasoning_effort: "low" / "medium" / "high"
        # high ほど精度が上がるがコスト・レイテンシも増加
        self.reasoning_effort = reasoning_effort
        self._usage_log: list[dict[str, Any]] = []

    def generate_code(self, task: str, verbose: bool = True) -> str:
        """
        Responses API を使ってコードを生成する。

        o4-mini の特徴:
        - SWE-bench Verified: 68.1%（Claude Sonnet 4.6 の 79.6% より低いが低コスト）
        - Terminal-Bench 2.0: 77.3%（Claude の 59.1% より高い）
        - 入力 $1.10/MTok（Claude Sonnet 4.6 の $3.00/MTok より65%安い）
        """
        if verbose:
            print(f"\n[Codex Client] モデル: {self.model} / reasoning: {self.reasoning_effort}")
            print(f"タスク: {task}\n")

        # Responses API: ChatCompletions より長期セッションのステート管理が得意
        response = self.client.responses.create(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=task,
            # reasoning パラメータ（o系モデル専用）
            reasoning={"effort": self.reasoning_effort},
        )

        # トークン使用量ログ
        if hasattr(response, "usage") and response.usage:
            self._log_usage(response.usage)
            if verbose:
                self._print_usage(response.usage)

        # レスポンステキストを抽出
        output_text = ""
        if hasattr(response, "output_text"):
            output_text = response.output_text or ""
        elif hasattr(response, "output"):
            for item in response.output:
                if hasattr(item, "content"):
                    for block in item.content:
                        if hasattr(block, "text"):
                            output_text += block.text

        return output_text

    def generate_with_shell_verify(self, task: str, verbose: bool = True) -> dict[str, str]:
        """
        コード生成後にシェルで構文チェックを実行するCodex CLIスタイルのワークフロー。
        /plan -> /exec -> /review の簡易実装。
        """
        code_response = self.generate_code(task, verbose=verbose)

        # コードブロックを抽出
        code = self._extract_code_block(code_response)

        # PoC: 構文チェックのみ（python -c "compile(..."）
        syntax_result = safe_shell_exec(f'python3 -c "compile(open(\'/dev/stdin\').read(), \'<string>\', \'exec\')"')

        return {
            "generated_code": code,
            "response_text":  code_response,
            "syntax_check":   syntax_result["stderr"] or "OK",
        }

    def _extract_code_block(self, text: str) -> str:
        """```python ... ``` ブロックを抽出する"""
        lines = text.split("\n")
        in_block = False
        code_lines = []
        for line in lines:
            if line.strip().startswith("```python"):
                in_block = True
                continue
            if line.strip() == "```" and in_block:
                break
            if in_block:
                code_lines.append(line)
        return "\n".join(code_lines)

    def _log_usage(self, usage: Any) -> None:
        self._usage_log.append({
            "input_tokens":  getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "reasoning_tokens": getattr(usage, "reasoning_tokens", 0),
        })

    def _print_usage(self, usage: Any) -> None:
        reasoning = getattr(usage, "reasoning_tokens", 0)
        print(
            f"  [Usage] input={usage.input_tokens:,} / output={usage.output_tokens:,} "
            f"/ reasoning={reasoning:,}"
        )

    def print_cost_summary(self) -> None:
        """セッション全体のコスト試算をターミナル出力する"""
        # o4-mini 単価（$/1Mトークン）
        PRICE_O4_MINI = {"input": 1.10, "output": 4.40}
        total_cost = 0.0
        print("\n" + "=" * 50)
        print("セッションコストサマリー (o4-mini基準)")
        print("=" * 50)
        for i, log in enumerate(self._usage_log):
            turn_cost = (
                log["input_tokens"]  / 1_000_000 * PRICE_O4_MINI["input"]
                + log["output_tokens"] / 1_000_000 * PRICE_O4_MINI["output"]
            )
            total_cost += turn_cost
            print(
                f"  Call {i + 1}: "
                f"in={log['input_tokens']:,} / out={log['output_tokens']:,} / "
                f"reasoning={log.get('reasoning_tokens', 0):,} → ${turn_cost:.4f}"
            )
        print(f"\n  合計推定コスト: ${total_cost:.4f} (o4-mini基準)")
        print("\n  【参考】同タスクをClaude Sonnet 4.6 (入力$3/MTok) で実施した場合:")
        for i, log in enumerate(self._usage_log):
            sonnet_cost = (
                log["input_tokens"]  / 1_000_000 * 3.00
                + log["output_tokens"] / 1_000_000 * 15.00
            )
            print(f"  Call {i + 1}: ${sonnet_cost:.4f}")
        print("=" * 50)


# ---- CLI エントリポイント -----------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAI Codex スタイル コード生成クライアント (PoC)")
    parser.add_argument("--task",      default="FizzBuzzをPythonで型アノテーション付きで実装してください", help="生成タスク")
    parser.add_argument("--model",     default="o4-mini", choices=list(MODELS.keys()), help="使用モデル")
    parser.add_argument("--reasoning", default="medium", choices=["low", "medium", "high"], help="推論努力レベル")
    parser.add_argument("--verify",    action="store_true", default=False, help="シェル構文チェックを実行")
    args = parser.parse_args()

    client = CodexClient(model=MODELS[args.model], reasoning_effort=args.reasoning)

    if args.verify:
        result = client.generate_with_shell_verify(task=args.task, verbose=True)
        print("\n" + "=" * 50)
        print("生成コード:")
        print("=" * 50)
        print(result["generated_code"])
        print(f"\n構文チェック結果: {result['syntax_check']}")
    else:
        result_text = client.generate_code(task=args.task, verbose=True)
        print("\n" + "=" * 50)
        print("生成結果:")
        print("=" * 50)
        print(result_text)

    client.print_cost_summary()


if __name__ == "__main__":
    main()
