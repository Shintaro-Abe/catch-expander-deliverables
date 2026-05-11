#!/usr/bin/env python3
# PoC品質: 本番環境での利用前にエラーハンドリングと認証処理を強化してください
#
# Claude API を使った Playwright テスト失敗分析スクリプト
#
# 機能:
#   - Playwright のテスト結果 JSON を解析
#   - 失敗したテストのスクリーンショット・エラーメッセージを収集
#   - Claude API に送信して根本原因分析を取得
#   - GitHub PR にコメントとして投稿（gh CLI 使用）
#
# 使い方:
#   pip install anthropic
#   python scripts/claude_test_analyzer.py \
#     --results-dir ./test-results \
#     --pr-number 42
#
# 必要な環境変数:
#   ANTHROPIC_API_KEY  : Anthropic API キー（GitHub Secretsから注入）
#   GITHUB_TOKEN       : GitHub CLI 認証用（GitHub Actions で自動提供）

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import anthropic    # pip install anthropic


# =========================================================
# 定数設定
# =========================================================

# Claude モデルの選択指針:
#   - Opus 4.x  : 複雑な推論・深い分析が必要な場合（高コスト）
#   - Sonnet 4.6: コーディング・文書分析など大半の本番ワークロード（バランス型）
#   - Haiku 4.5 : 定型的な分類・抽出・大量処理（低コスト）
#
# テスト分析は中程度の複雑さなので Sonnet が適切
CLAUDE_MODEL = "claude-sonnet-4-6"

# 最大トークン数（コスト管理）
MAX_TOKENS = 2048

# プロンプトキャッシュ最小閾値: Sonnet 4.6 は 2048 トークン以上でキャッシュ対象
# システムプロンプトは繰り返し使うため cache_control を付けてコスト削減


# =========================================================
# テスト結果の収集
# =========================================================

def collect_test_results(results_dir: Path) -> dict:
    """
    Playwright が出力した test-results/ ディレクトリから失敗情報を収集する。

    Playwright の出力構造:
        test-results/
            <test-name>/
                test-failed-1.png  (失敗時スクリーンショット)
                trace.zip          (操作トレース)
                error.txt          (エラーメッセージ)

    Returns:
        {
          "failed_tests": [
            {
              "name": "テスト名",
              "error": "エラーメッセージ",
              "screenshot_b64": "base64エンコードされた画像"  # Claudeへ送るため
            }
          ],
          "total_failures": int
        }
    """
    failed_tests = []

    if not results_dir.exists():
        print(f"警告: テスト結果ディレクトリが見つかりません: {results_dir}")
        return {"failed_tests": [], "total_failures": 0}

    # テスト結果のサブディレクトリを走査
    for test_dir in sorted(results_dir.iterdir()):
        if not test_dir.is_dir():
            continue

        test_info = {"name": test_dir.name}

        # エラーメッセージを収集
        error_file = test_dir / "error.txt"
        if error_file.exists():
            test_info["error"] = error_file.read_text(encoding="utf-8")[:2000]  # 先頭2000字のみ

        # スクリーンショットを収集（Claude への画像入力に使用）
        screenshots = list(test_dir.glob("*.png"))
        if screenshots:
            # 最初のスクリーンショットのみ（コンテキスト削減のため）
            screenshot_bytes = screenshots[0].read_bytes()
            test_info["screenshot_b64"] = base64.standard_b64encode(screenshot_bytes).decode()
            test_info["screenshot_name"] = screenshots[0].name

        if "error" in test_info or "screenshot_b64" in test_info:
            failed_tests.append(test_info)

    return {
        "failed_tests": failed_tests,
        "total_failures": len(failed_tests)
    }


# =========================================================
# Claude API による分析
# =========================================================

def analyze_with_claude(
    test_results: dict,
    diff_text: str,
    client: anthropic.Anthropic
) -> str:
    """
    Claude API を呼び出してテスト失敗の根本原因を分析する。

    プロンプトキャッシュ戦略:
        - システムプロンプト（静的）に cache_control を付与
        - 各実行でキャッシュヒットさせてコスト削減（約90%削減）
        - Sonnet 4.6 のキャッシュヒット料金: 標準の 10%

    Returns:
        Claudeが生成した分析レポート（Markdown形式）
    """
    if test_results["total_failures"] == 0:
        return "テスト失敗は検出されませんでした。"

    # ユーザーメッセージのコンテンツを構築
    content = []

    # テキスト情報（エラーメッセージ・差分）
    failures_summary = f"## 失敗したテスト数: {test_results['total_failures']}\n\n"
    for test in test_results["failed_tests"][:5]:    # 最大5件（コンテキスト管理）
        failures_summary += f"### テスト: {test['name']}\n"
        if "error" in test:
            failures_summary += f"```\n{test['error']}\n```\n\n"

    content.append({
        "type": "text",
        "text": f"{failures_summary}\n\n## コード差分\n```diff\n{diff_text[:3000]}\n```"
    })

    # スクリーンショット（画像）をメッセージに追加
    # Claude は画像とテキストを組み合わせたマルチモーダル入力に対応
    for test in test_results["failed_tests"][:3]:    # 最大3枚（コスト管理）
        if "screenshot_b64" in test:
            content.append({
                "type": "text",
                "text": f"\n**スクリーンショット: {test.get('screenshot_name', 'unknown')}**"
            })
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": test["screenshot_b64"]
                }
            })

    content.append({
        "type": "text",
        "text": "\n以上の情報を基に、失敗の根本原因を分析し、修正案を提示してください。"
    })

    # Claude API 呼び出し
    # システムプロンプトに cache_control を付与（繰り返し呼び出しでキャッシュヒット）
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": (
                    "あなたはPlaywrightテストとフロントエンド開発の専門家です。\n"
                    "テスト失敗の分析では以下の4分類を必ず行ってください：\n"
                    "1. テストコードのバグ（セレクタの誤り・タイミング問題等）\n"
                    "2. アプリケーションのバグ（機能の不具合）\n"
                    "3. セレクタの変化（UIリファクタリングによる要素の変更）\n"
                    "4. フレーキーテスト（ネットワーク遅延・非決定的な失敗）\n\n"
                    "回答はMarkdown形式で日本語で記述し、修正コード例を含めてください。"
                ),
                # この cache_control により、同じシステムプロンプトのリクエストは
                # キャッシュから読み取られ、コストが約90%削減される
                "cache_control": {"type": "ephemeral"}
            }
        ],
        messages=[
            {"role": "user", "content": content}
        ]
    )

    # キャッシュ使用状況をログ出力（デバッグ・コスト監視用）
    usage = response.usage
    print(f"トークン使用量 - 入力: {usage.input_tokens}, "
          f"キャッシュ書き込み: {getattr(usage, 'cache_creation_input_tokens', 0)}, "
          f"キャッシュ読み取り: {getattr(usage, 'cache_read_input_tokens', 0)}, "
          f"出力: {usage.output_tokens}")

    return response.content[0].text


# =========================================================
# GitHub PR へのコメント投稿
# =========================================================

def post_pr_comment(pr_number: int, body: str) -> bool:
    """
    gh CLI を使って PR にコメントを投稿する。

    gh CLI は GitHub Actions で自動的に GITHUB_TOKEN で認証済み。
    ローカル実行時は `gh auth login` が必要。

    Returns:
        True: 投稿成功, False: 投稿失敗
    """
    comment_body = f"""## Playwright テスト失敗分析 (Claude による自動分析)

{body}

---
*このコメントは Claude Code Action により自動生成されました。*
*分析の精度は参考情報であり、必ず人間によるレビューを行ってください。*
"""

    result = subprocess.run(
        ["gh", "pr", "comment", str(pr_number), "--body", comment_body],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"PRコメント投稿エラー: {result.stderr}", file=sys.stderr)
        return False

    print(f"PR #{pr_number} にコメントを投稿しました。")
    return True


# =========================================================
# メイン処理
# =========================================================

def main():
    parser = argparse.ArgumentParser(
        description="Playwright テスト失敗を Claude API で分析して PR にコメント投稿"
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("./test-results"),
        help="Playwright のテスト結果ディレクトリ（デフォルト: ./test-results）"
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        help="PR 番号（指定時に GitHub PR へコメント投稿）"
    )
    parser.add_argument(
        "--base-ref",
        default="main",
        help="比較ベースブランチ（デフォルト: main）"
    )
    args = parser.parse_args()

    # API キーの確認（ハードコードは絶対に禁止 - 環境変数から取得）
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("エラー: ANTHROPIC_API_KEY 環境変数が設定されていません", file=sys.stderr)
        print("GitHub Secrets に ANTHROPIC_API_KEY を登録し、ワークフローで参照してください")
        sys.exit(1)

    # Anthropic クライアントの初期化
    client = anthropic.Anthropic(api_key=api_key)

    # コード差分の取得（失敗の原因特定に使用）
    diff_result = subprocess.run(
        ["git", "diff", f"origin/{args.base_ref}...HEAD"],
        capture_output=True,
        text=True
    )
    diff_text = diff_result.stdout if diff_result.returncode == 0 else "差分取得失敗"

    # テスト結果の収集
    print(f"テスト結果を収集中: {args.results_dir}")
    test_results = collect_test_results(args.results_dir)
    print(f"失敗テスト数: {test_results['total_failures']}")

    if test_results["total_failures"] == 0:
        print("失敗テストがありません。分析をスキップします。")
        sys.exit(0)

    # Claude による分析
    print("Claude API で分析中...")
    analysis = analyze_with_claude(test_results, diff_text, client)
    print("\n=== 分析結果 ===")
    print(analysis)

    # PR へのコメント投稿（PR番号が指定された場合のみ）
    if args.pr_number:
        post_pr_comment(args.pr_number, analysis)
    else:
        print("\n--pr-number が指定されていないため、PR コメントの投稿をスキップします")

    # 分析結果をファイルにも保存（GitHub Actions の Step Summary 等で参照可能）
    output_path = Path("/tmp/analysis.md")
    output_path.write_text(analysis, encoding="utf-8")
    print(f"分析結果を保存しました: {output_path}")


if __name__ == "__main__":
    main()
