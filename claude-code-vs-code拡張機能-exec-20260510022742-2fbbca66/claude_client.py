# PoC品質: このコードは概念実証(Proof of Concept)として提供されています。
# 本番環境での使用には追加のエラーハンドリングとセキュリティ対策が必要です。

"""
Claude Code VS Code拡張機能 — Anthropic APIクライアントラッパー

このモジュールはClaude Code VS Code拡張が内部で行う操作を
Pythonから直接実行するためのクライアントラッパーです。
ストリーミング・ツール使用・拡張思考(Extended Thinking)に対応しています。
"""

import os
import json
from typing import Iterator, Optional
import anthropic


# ==============================
# 定数定義
# ==============================

# 推奨モデル: 速度と性能のベストバランス
# Extended Thinking(拡張思考)対応で複雑な問題に強い
DEFAULT_MODEL = "claude-sonnet-4-6"

# 最大出力トークン数（デフォルト）
DEFAULT_MAX_TOKENS = 8192


class ClaudeCodeClient:
    """
    Claude Code VS Code拡張機能相当の操作をAPIで実現するクライアント。

    VS Code拡張が提供する主要機能:
    - コードレビュー・説明
    - ファイル編集提案(インラインDiff)
    - 拡張思考(Extended Thinking)
    - ストリーミング応答
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
    ):
        """
        クライアントを初期化します。

        Args:
            api_key: Anthropic APIキー。未指定時は環境変数 ANTHROPIC_API_KEY を使用。
            model: 使用するClaudeモデルID。
        """
        # APIキーは環境変数から取得するのが安全
        # .envファイルや settings.local.json に記載し、Gitにコミットしないこと
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError(
                "APIキーが未設定です。環境変数 ANTHROPIC_API_KEY を設定するか、"
                "引数 api_key に渡してください。"
            )

        self.client = anthropic.Anthropic(api_key=resolved_key)
        self.model = model

    # ------------------------------------------------------------------
    # 基本チャット
    # ------------------------------------------------------------------

    def chat(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        """
        シンプルな1ターンの対話を実行します。

        Args:
            user_message: ユーザーからのメッセージ。
            system_prompt: システムプロンプト（役割・制約の指定）。
            max_tokens: 最大出力トークン数。

        Returns:
            Claudeからの応答テキスト。
        """
        messages = [{"role": "user", "content": user_message}]
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = self.client.messages.create(**kwargs)
        return response.content[0].text

    # ------------------------------------------------------------------
    # ストリーミングチャット (VS Code拡張のリアルタイム表示に対応)
    # ------------------------------------------------------------------

    def stream_chat(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[str]:
        """
        ストリーミング形式で応答を逐次取得します。
        VS Code拡張のチャットパネルがリアルタイムで文字を表示する仕組みと同等。

        Args:
            user_message: ユーザーからのメッセージ。
            system_prompt: システムプロンプト。
            max_tokens: 最大出力トークン数。

        Yields:
            応答テキストのチャンク（断片）。
        """
        messages = [{"role": "user", "content": user_message}]
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        with self.client.messages.stream(**kwargs) as stream:
            for text_chunk in stream.text_stream:
                yield text_chunk

    # ------------------------------------------------------------------
    # コードレビュー (VS Code拡張の主要ユースケース)
    # ------------------------------------------------------------------

    def review_code(
        self,
        code: str,
        language: str = "python",
        focus: str = "全般",
    ) -> str:
        """
        コードのレビューを実行します。
        VS Code拡張でファイルを @メンション して「レビューして」と依頼する操作と同等。

        Args:
            code: レビュー対象のコード文字列。
            language: プログラミング言語名。
            focus: レビューの観点（例: "セキュリティ", "パフォーマンス", "全般"）。

        Returns:
            レビュー結果のMarkdownテキスト。
        """
        system_prompt = (
            f"あなたは{language}の熟練したシニアエンジニアです。"
            "コードレビューを行い、問題点・改善案・良い点を具体的に指摘してください。"
            "Markdownで構造化して回答してください。"
        )

        user_message = f"""
以下の{language}コードを「{focus}」の観点でレビューしてください。

```{language}
{code}
```

以下の項目について評価してください:
1. バグ・論理エラー
2. セキュリティリスク
3. パフォーマンス問題
4. コード品質・可読性
5. 改善提案
"""
        return self.chat(user_message, system_prompt=system_prompt, max_tokens=4096)

    # ------------------------------------------------------------------
    # 拡張思考(Extended Thinking) — 複雑な設計問題向け
    # ------------------------------------------------------------------

    def think_deeply(
        self,
        problem: str,
        effort: str = "medium",
    ) -> dict:
        """
        Extended Thinking(拡張思考)を使って複雑な問題を深く分析します。
        VS Code拡張の「/think」コマンドや拡張思考トグルに相当します。

        Args:
            problem: 解決したい問題や質問。
            effort: 思考の深さ。"low" | "medium" | "high" から選択。
                    "high"にするほど時間はかかるが、より深い分析が得られる。

        Returns:
            {
                "thinking": str,  # Claudeの思考プロセス（折りたたみ表示に対応）
                "response": str,  # 最終的な回答
            }
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            # effort パラメータで思考の深さを調整
            # "low": 素早く回答 / "medium": バランス / "high": 深い分析
            extra_body={"effort": effort},
            messages=[{"role": "user", "content": problem}],
        )

        thinking_text = ""
        response_text = ""

        for block in response.content:
            if block.type == "thinking":
                thinking_text = block.thinking
            elif block.type == "text":
                response_text = block.text

        return {
            "thinking": thinking_text,
            "response": response_text,
        }

    # ------------------------------------------------------------------
    # テスト生成 (VS Code拡張の実践的ユースケース)
    # ------------------------------------------------------------------

    def generate_tests(
        self,
        code: str,
        language: str = "python",
        test_framework: str = "pytest",
    ) -> str:
        """
        コードに対するテストコードを自動生成します。
        VS Code拡張で「テストを追加して」と依頼する操作と同等。

        Args:
            code: テスト対象のコード。
            language: プログラミング言語。
            test_framework: テストフレームワーク（例: pytest, unittest, jest）。

        Returns:
            生成されたテストコード。
        """
        system_prompt = (
            f"あなたは{language}と{test_framework}の専門家です。"
            "既存コードのスタイルとパターンに合わせたテストを書いてください。"
            "エッジケース・境界値・エラー条件も含めてください。"
        )

        user_message = f"""
以下の{language}コードに対する{test_framework}テストを生成してください。

```{language}
{code}
```

要件:
- 正常系・異常系の両方をカバー
- エッジケース（空入力・境界値・None等）を含む
- 各テストに日本語のdocstringで説明を追加
- モックが必要な場合は適切に使用
"""
        return self.chat(user_message, system_prompt=system_prompt, max_tokens=4096)

    # ------------------------------------------------------------------
    # ドキュメント生成
    # ------------------------------------------------------------------

    def generate_docs(self, code: str, language: str = "python") -> str:
        """
        コードのドキュメントを自動生成します。

        Args:
            code: ドキュメント対象のコード。
            language: プログラミング言語。

        Returns:
            ドキュメントが追加されたコード。
        """
        user_message = f"""
以下の{language}コードに適切なドキュメントコメントを追加してください。

```{language}
{code}
```

要件:
- 各関数・クラスにdocstringを追加
- 引数・戻り値の型と説明を記載
- 複雑なロジックには行内コメントを追加
- 日本語で記述
"""
        return self.chat(user_message)


# ==============================
# 使用例
# ==============================

if __name__ == "__main__":
    # 環境変数 ANTHROPIC_API_KEY が設定されている前提
    client = ClaudeCodeClient()

    # --- 例1: 基本チャット ---
    print("=== 基本チャット ===")
    response = client.chat("Pythonのリスト内包表記を簡潔に説明してください。")
    print(response)

    # --- 例2: ストリーミング ---
    print("\n=== ストリーミングチャット ===")
    for chunk in client.stream_chat("フィボナッチ数列とは何ですか？"):
        print(chunk, end="", flush=True)
    print()

    # --- 例3: コードレビュー ---
    sample_code = """
def divide(a, b):
    return a / b

result = divide(10, 0)
print(result)
"""
    print("\n=== コードレビュー ===")
    review = client.review_code(sample_code, language="python", focus="バグ・エラー処理")
    print(review)
