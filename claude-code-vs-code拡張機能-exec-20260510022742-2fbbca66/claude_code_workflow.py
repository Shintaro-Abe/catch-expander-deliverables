# PoC品質: このコードは概念実証(Proof of Concept)として提供されています。
# 本番環境での使用には追加のエラーハンドリングとテストが必要です。

"""
Claude Code VS Code拡張機能 — 開発ワークフロー自動化スクリプト

VS Code拡張が提供する主要ワークフローをPythonスクリプトで自動化します。
CI/CDパイプラインやバッチ処理への組み込みを想定しています。

対応ワークフロー:
- コードレビュー (差分ベース)
- テスト生成
- ドキュメント生成
- リファクタリング提案
- コミットメッセージ自動生成
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from claude_client import ClaudeCodeClient


# ==============================
# Gitユーティリティ
# ==============================

def get_git_diff(
    staged: bool = True,
    file_path: Optional[str] = None,
) -> str:
    """
    Gitの差分を取得します。

    VS Code拡張の「変更したファイル」をコンテキストとして渡す操作と同等。

    Args:
        staged: Trueならステージ済み差分、Falseなら未ステージ差分を取得。
        file_path: 特定ファイルのみ取得する場合に指定。

    Returns:
        git diff の出力テキスト。
    """
    cmd = ["git", "diff"]
    if staged:
        cmd.append("--staged")

    if file_path:
        cmd.extend(["--", file_path])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def get_changed_files(staged: bool = True) -> list[str]:
    """
    変更されたファイルの一覧を取得します。

    Args:
        staged: Trueならステージ済みファイル、Falseなら全変更ファイル。

    Returns:
        ファイルパスのリスト。
    """
    cmd = ["git", "diff", "--name-only"]
    if staged:
        cmd.append("--staged")

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    files = result.stdout.strip().split("\n")
    return [f for f in files if f]  # 空文字を除外


def read_file_content(file_path: str) -> Optional[str]:
    """
    ファイルの内容を読み取ります。
    VS Code拡張の @メンション でファイルをコンテキストに渡す操作と同等。

    Args:
        file_path: 読み取るファイルのパス。

    Returns:
        ファイルの内容。読み取り失敗時はNone。
    """
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return None


# ==============================
# ワークフロークラス
# ==============================

class DevelopmentWorkflow:
    """
    Claude Code VS Code拡張の主要ワークフローを自動化するクラス。

    【VS Code拡張との対応関係】
    - review_staged_changes() → @ファイルをメンションして「レビューして」
    - generate_commit_message() → 「コミットメッセージを書いて」
    - generate_tests_for_file() → 「テストを追加して」
    - suggest_refactoring() → 「リファクタリングして」
    """

    def __init__(self, client: Optional[ClaudeCodeClient] = None):
        """
        Args:
            client: ClaudeCodeClientインスタンス。未指定時は自動生成。
        """
        self.client = client or ClaudeCodeClient()

    # ------------------------------------------------------------------
    # コードレビュー（差分ベース）
    # ------------------------------------------------------------------

    def review_staged_changes(self, focus: str = "全般") -> str:
        """
        ステージ済みの変更をレビューします。

        VS Code拡張でコミット前に「この変更をレビューして」と依頼する操作と同等。
        CI/CDのpre-commitフックに組み込むことも可能です。

        Args:
            focus: レビューの重点観点。
                   例: "セキュリティ", "パフォーマンス", "バグ", "全般"

        Returns:
            レビュー結果のMarkdownテキスト。
        """
        diff = get_git_diff(staged=True)
        if not diff:
            return "ステージ済みの変更がありません。`git add` でファイルをステージしてください。"

        system_prompt = (
            "あなたはシニアソフトウェアエンジニアです。"
            "git diffの変更内容をレビューし、問題点と改善案を具体的に指摘してください。"
            "Markdownで構造化し、深刻度（🔴高/🟡中/🟢低）で分類してください。"
        )

        message = f"""
以下のgit diffの変更を「{focus}」の観点でレビューしてください。

```diff
{diff[:8000]}
```

評価項目:
1. 🔴 重大なバグ・セキュリティリスク
2. 🟡 パフォーマンス問題・コードスメル
3. 🟢 改善提案・ベストプラクティス
4. ✅ 良い点（モチベーション維持のため）

コミットして問題ないかの最終判断も明記してください。
"""
        return self.client.chat(message, system_prompt=system_prompt, max_tokens=4096)

    # ------------------------------------------------------------------
    # コミットメッセージ生成
    # ------------------------------------------------------------------

    def generate_commit_message(
        self,
        convention: str = "conventional",
    ) -> str:
        """
        ステージ済み変更からコミットメッセージを自動生成します。

        VS Code拡張の「コミットメッセージを書いて」依頼と同等。

        Args:
            convention: コミットメッセージの規約。
                        "conventional" → feat: / fix: / refactor: 等
                        "japanese"     → 日本語コミットメッセージ
                        "simple"       → シンプルな英語一文

        Returns:
            生成されたコミットメッセージ。
        """
        diff = get_git_diff(staged=True)
        if not diff:
            return "ステージ済みの変更がありません。"

        changed_files = get_changed_files(staged=True)
        files_summary = ", ".join(changed_files[:10])  # 最大10ファイルまで表示

        convention_instructions = {
            "conventional": (
                "Conventional Commits形式 (feat/fix/docs/refactor/test/chore: 説明) で。"
                "本文に変更の理由も記載。"
            ),
            "japanese": "日本語で、変更内容と理由を2-3行で。",
            "simple": "英語で1行、動詞から始める（例: Add feature X, Fix bug in Y）。",
        }

        instructions = convention_instructions.get(
            convention,
            convention_instructions["conventional"],
        )

        message = f"""
以下のgit diffからコミットメッセージを生成してください。

変更ファイル: {files_summary}

```diff
{diff[:6000]}
```

【要件】
- {instructions}
- 変更の「何を」「なぜ」が伝わること
- タイトルは72文字以内
- コミットメッセージのみを出力（説明不要）
"""
        return self.client.chat(message, max_tokens=512)

    # ------------------------------------------------------------------
    # テスト生成（ファイル指定）
    # ------------------------------------------------------------------

    def generate_tests_for_file(
        self,
        file_path: str,
        test_framework: str = "pytest",
        output_path: Optional[str] = None,
    ) -> str:
        """
        指定ファイルのテストを生成し、オプションでファイルに書き出します。

        VS Code拡張で「@src/utils.py のテストを追加して」と依頼する操作と同等。

        Args:
            file_path: テスト対象ファイルのパス。
            test_framework: 使用するテストフレームワーク。
            output_path: テストファイルの出力先（Noneなら画面表示のみ）。

        Returns:
            生成されたテストコード。
        """
        source_code = read_file_content(file_path)
        if source_code is None:
            return f"エラー: ファイル '{file_path}' を読み取れませんでした。"

        # ファイル名からテストファイル名を推定
        source_path = Path(file_path)
        language = _detect_language(source_path.suffix)

        test_code = self.client.generate_tests(
            source_code,
            language=language,
            test_framework=test_framework,
        )

        if output_path:
            test_path = Path(output_path)
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(test_code, encoding="utf-8")
            print(f"✅ テストを書き出しました: {test_path}")

        return test_code

    # ------------------------------------------------------------------
    # リファクタリング提案
    # ------------------------------------------------------------------

    def suggest_refactoring(
        self,
        file_path: str,
        goal: str = "可読性・保守性の向上",
    ) -> str:
        """
        ファイルのリファクタリング提案を生成します。

        VS Code拡張で「@ファイル をリファクタリングして」と依頼する操作と同等。

        Args:
            file_path: リファクタリング対象ファイル。
            goal: リファクタリングの目的。

        Returns:
            リファクタリング提案と改善後コード。
        """
        source_code = read_file_content(file_path)
        if source_code is None:
            return f"エラー: ファイル '{file_path}' を読み取れませんでした。"

        source_path = Path(file_path)
        language = _detect_language(source_path.suffix)

        message = f"""
以下の{language}コードを「{goal}」を目的にリファクタリングしてください。

ファイル: {file_path}

```{language}
{source_code[:6000]}
```

以下の形式で回答してください:
1. **現状の問題点** (箇条書き)
2. **リファクタリング方針** (変更の理由を含む)
3. **改善後のコード** (コードブロック)
4. **変更による影響** (メリット・デメリット両方)

既存の動作を変えずにリファクタリングすること（振る舞いの保持）。
"""
        return self.client.chat(message, max_tokens=6000)

    # ------------------------------------------------------------------
    # 比較分析（他ツールとの違いを理解するため）
    # ------------------------------------------------------------------

    def analyze_tool_comparison(self, topic: str) -> str:
        """
        Claude Codeと他のAIコーディングツールの比較分析を実行します。

        Args:
            topic: 比較したい観点（例: "価格", "プライバシー", "コード補完精度"）。

        Returns:
            比較分析レポート。
        """
        message = f"""
AIコーディングツール（Claude Code, GitHub Copilot, Cursor, Windsurf）を
「{topic}」の観点で比較分析してください。

以下の形式で:
- 各ツールの強み・弱みを表形式で
- 初学者・中級者・エンタープライズ別の推奨ツール
- 選択時の注意点

客観的な分析で、メリット・デメリット両面を提示してください。
"""
        return self.client.chat(message, max_tokens=4096)


# ==============================
# ヘルパー関数
# ==============================

def _detect_language(extension: str) -> str:
    """ファイル拡張子からプログラミング言語名を推定します。"""
    mapping = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".cs": "csharp",
        ".cpp": "cpp",
        ".c": "c",
        ".swift": "swift",
        ".kt": "kotlin",
    }
    return mapping.get(extension.lower(), "plaintext")


# ==============================
# CLIエントリポイント
# ==============================

def main() -> None:
    """コマンドラインから各ワークフローを実行するエントリポイント。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Claude Code VS Code拡張機能 開発ワークフロー自動化ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # ステージ済み変更をレビュー
  python claude_code_workflow.py review --focus セキュリティ

  # コミットメッセージ生成
  python claude_code_workflow.py commit --convention conventional

  # ファイルのテスト生成
  python claude_code_workflow.py test --file src/utils.py --output tests/test_utils.py

  # リファクタリング提案
  python claude_code_workflow.py refactor --file src/legacy.py

  # ツール比較分析
  python claude_code_workflow.py compare --topic プライバシー・セキュリティ
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="実行するコマンド")

    # review コマンド
    review_parser = subparsers.add_parser("review", help="コードレビューを実行")
    review_parser.add_argument(
        "--focus", default="全般", help="レビューの観点 (デフォルト: 全般)"
    )

    # commit コマンド
    commit_parser = subparsers.add_parser("commit", help="コミットメッセージを生成")
    commit_parser.add_argument(
        "--convention",
        default="conventional",
        choices=["conventional", "japanese", "simple"],
        help="コミットメッセージの形式",
    )

    # test コマンド
    test_parser = subparsers.add_parser("test", help="テストコードを生成")
    test_parser.add_argument("--file", required=True, help="テスト対象ファイル")
    test_parser.add_argument("--framework", default="pytest", help="テストフレームワーク")
    test_parser.add_argument("--output", help="テストファイルの出力先")

    # refactor コマンド
    refactor_parser = subparsers.add_parser("refactor", help="リファクタリング提案")
    refactor_parser.add_argument("--file", required=True, help="対象ファイル")
    refactor_parser.add_argument("--goal", default="可読性・保守性の向上", help="リファクタリングの目的")

    # compare コマンド
    compare_parser = subparsers.add_parser("compare", help="AIツール比較分析")
    compare_parser.add_argument("--topic", default="全般", help="比較観点")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # クライアント初期化
    try:
        workflow = DevelopmentWorkflow()
    except ValueError as e:
        print(f"❌ 初期化エラー: {e}")
        sys.exit(1)

    # コマンド実行
    result = ""
    if args.command == "review":
        result = workflow.review_staged_changes(focus=args.focus)
    elif args.command == "commit":
        result = workflow.generate_commit_message(convention=args.convention)
    elif args.command == "test":
        result = workflow.generate_tests_for_file(
            file_path=args.file,
            test_framework=args.framework,
            output_path=args.output,
        )
    elif args.command == "refactor":
        result = workflow.suggest_refactoring(file_path=args.file, goal=args.goal)
    elif args.command == "compare":
        result = workflow.analyze_tool_comparison(topic=args.topic)

    print(result)


if __name__ == "__main__":
    main()
