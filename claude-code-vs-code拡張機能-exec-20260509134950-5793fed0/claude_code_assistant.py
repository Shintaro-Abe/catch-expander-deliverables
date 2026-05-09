# PoC品質: このコードは動作確認用のスケルトンです。本番環境での使用には追加の検証が必要です。
#
# Claude Code VS Code拡張機能 - Python実装デモ
# Claude Code（VS Code拡張）が提供するコードレビュー・テスト生成・リファクタリング支援を
# PythonスクリプトからAnthropicAPIを通じて再現するデモです。

import os
import sys
from pathlib import Path
from typing import Optional

import anthropic

# ============================================================
# 定数・設定
# ============================================================

# 使用するモデル（2026年5月時点の最新推奨）
# Claude Sonnet 4.6: コスト効率と性能のバランスが優れたモデル
MODEL = "claude-sonnet-4-6"

# プロンプトキャッシュ: 同じシステムプロンプトを繰り返す場合にトークンコストを削減できる
# キャッシュヒット時は通常価格の10%のみ課金される
SYSTEM_PROMPT = """あなたはVS Code上で動作するClaude Codeアシスタントです。
以下の役割を担います：
- コードレビュー: バグ・セキュリティ脆弱性・改善点の指摘
- テスト生成: 既存コードに対するユニットテストの自動生成
- リファクタリング: コード品質を維持しながら可読性・保守性を向上

回答は日本語で、初学者にもわかりやすく、専門用語には補足説明を加えてください。
コードブロックを積極的に使用し、視認性の高い形式で回答してください。"""


def create_client() -> anthropic.Anthropic:
    """
    Anthropicクライアントを初期化する。

    認証方式の優先順位（Claude Code VS Code拡張と同じ仕様）:
    1. ANTHROPIC_API_KEY 環境変数
    2. ANTHROPIC_AUTH_TOKEN 環境変数

    Returns:
        anthropic.Anthropic: 初期化済みクライアント
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("エラー: ANTHROPIC_API_KEY 環境変数が設定されていません。")
        print("設定方法: export ANTHROPIC_API_KEY='your-api-key-here'")
        sys.exit(1)
    return anthropic.Anthropic(api_key=api_key)


# ============================================================
# コードレビュー機能
# ============================================================

def review_code(client: anthropic.Anthropic, code: str, language: str = "python") -> str:
    """
    コードレビューを実行する。

    Claude Code VS Code拡張の「コードレビュー」機能に相当。
    セキュリティ脆弱性・バグ・改善点を複数の観点から指摘する。

    Args:
        client: Anthropicクライアント
        code: レビュー対象のコード文字列
        language: プログラミング言語（デフォルト: python）

    Returns:
        str: レビュー結果（Markdown形式）
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=[
            {
                # cache_control: 同じシステムプロンプトを繰り返す際のトークンコストを削減
                # type="ephemeral"は5分間キャッシュされる（頻繁な呼び出しに最適）
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"""以下の{language}コードをレビューしてください。

**確認ポイント:**
1. セキュリティ脆弱性（SQLインジェクション・XSS・認証の不備など）
2. バグ・ロジックエラー
3. パフォーマンス上の問題
4. コード品質・可読性

**メリット（良い点）とデメリット（改善が必要な点）の両面**を指摘してください。

```{language}
{code}
```""",
            }
        ],
    )
    return response.content[0].text


# ============================================================
# テスト生成機能
# ============================================================

def generate_tests(
    client: anthropic.Anthropic,
    code: str,
    framework: str = "pytest",
    language: str = "python",
) -> str:
    """
    コードに対するユニットテストを自動生成する。

    Claude Code VS Code拡張の「テスト生成」機能に相当。
    既存コードのスタイル・フレームワークを踏まえてテストを生成する。

    Args:
        client: Anthropicクライアント
        code: テスト対象のコード文字列
        framework: テストフレームワーク（pytest / unittest）
        language: プログラミング言語

    Returns:
        str: 生成されたテストコード（Markdown形式）
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"""以下の{language}コードに対して{framework}を使ったユニットテストを生成してください。

**テスト生成の方針:**
- 正常系（期待通りの動作）のテスト
- 異常系・エッジケース（境界値・空入力・無効データ）のテスト
- 既存コードのスタイルに合わせたアサーションパターン

```{language}
{code}
```""",
            }
        ],
    )
    return response.content[0].text


# ============================================================
# リファクタリング支援機能
# ============================================================

def suggest_refactoring(
    client: anthropic.Anthropic,
    code: str,
    goal: str = "可読性と保守性の向上",
    language: str = "python",
) -> str:
    """
    リファクタリングの提案を行う。

    Claude Code VS Code拡張の「リファクタリング」機能に相当。
    Plan Mode（変更前に計画を確認）と同様の安全なアプローチで提案する。

    Args:
        client: Anthropicクライアント
        code: リファクタリング対象のコード文字列
        goal: リファクタリングの目標（デフォルト: 可読性と保守性の向上）
        language: プログラミング言語

    Returns:
        str: リファクタリング提案（変更計画 + 修正後コード）
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"""以下の{language}コードをリファクタリングしてください。

**リファクタリング目標:** {goal}

**回答形式（Plan Mode風の安全な手順）:**
1. **変更計画**: 何をどう変えるか（変更前に確認できるよう）
2. **メリット**: このリファクタリングで得られる利点
3. **デメリット・注意点**: 変更によるリスクや注意事項
4. **リファクタリング後のコード**

```{language}
{code}
```""",
            }
        ],
    )
    return response.content[0].text


# ============================================================
# ファイル解析機能（@メンション相当）
# ============================================================

def analyze_file(client: anthropic.Anthropic, file_path: str) -> str:
    """
    ファイルを読み込んで解析する。

    Claude Code VS Code拡張の「@ファイル名メンション」機能に相当。
    ファイルの内容をコンテキストとしてClaudeに渡す。

    Args:
        client: Anthropicクライアント
        file_path: 解析対象ファイルのパス

    Returns:
        str: ファイル解析結果
    """
    path = Path(file_path)
    if not path.exists():
        return f"エラー: ファイル '{file_path}' が見つかりません。"

    # ファイルサイズ制限（100KB）: 大きなファイルはチャンクに分割が必要
    if path.stat().st_size > 100_000:
        return f"警告: ファイル '{file_path}' が100KBを超えています。分割処理が必要です。"

    code = path.read_text(encoding="utf-8")
    suffix = path.suffix.lstrip(".")
    language = suffix if suffix else "text"

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"""ファイル `{file_path}` の内容を解析してください。

**解析項目:**
- ファイルの概要・目的
- 主要な関数・クラスの説明
- 依存関係・インポート
- 改善が推奨される箇所

```{language}
{code}
```""",
            }
        ],
    )
    return response.content[0].text


# ============================================================
# ストリーミング対応（長文レスポンス向け）
# ============================================================

def stream_code_explanation(
    client: anthropic.Anthropic,
    code: str,
    question: str,
    language: str = "python",
) -> None:
    """
    コードの説明をストリーミングで出力する。

    Claude Code VS Code拡張がリアルタイムでレスポンスを表示するのと同様に、
    ストリーミングAPIを使って回答を逐次出力する。

    Args:
        client: Anthropicクライアント
        code: 対象コード
        question: コードに関する質問
        language: プログラミング言語
    """
    print("Claude からの回答（ストリーミング）:\n" + "=" * 50)

    # stream()コンテキストマネージャーでストリーミングを有効化
    with client.messages.stream(
        model=MODEL,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"""{question}

```{language}
{code}
```""",
            }
        ],
    ) as stream:
        for text in stream.text_stream:
            # text_stream: テキストデルタを逐次受信するジェネレータ
            print(text, end="", flush=True)

    print("\n" + "=" * 50)


# ============================================================
# メイン処理（デモ実行）
# ============================================================

def main() -> None:
    """デモコードを実行して各機能の動作を確認する。"""

    # デモ用サンプルコード（意図的にいくつかの問題を含む）
    sample_code = '''
def get_user(user_id):
    import sqlite3
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # 問題: SQLインジェクション脆弱性あり
    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(query)
    result = cursor.fetchone()
    conn.close()
    return result

def calculate_average(numbers):
    # 問題: 空リストの場合にZeroDivisionErrorが発生する
    total = sum(numbers)
    return total / len(numbers)

def process_data(items):
    results = []
    for i in range(len(items)):
        # 改善点: enumerate()を使うとより読みやすい
        results.append(items[i] * 2)
    return results
'''

    print("Claude Code VS Code拡張機能 - Python実装デモ")
    print("=" * 60)
    print("注意: 実行にはANTHROPIC_API_KEY環境変数が必要です\n")

    # 環境変数チェック（実際の実行時のみAPIを呼び出す）
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("デモモード: APIキーが未設定のため、実行フローのみ表示します\n")
        print("【コードレビュー】")
        print(f"  対象コード: {len(sample_code)}文字")
        print("  → review_code(client, sample_code) を呼び出す\n")
        print("【テスト生成】")
        print("  → generate_tests(client, sample_code, framework='pytest') を呼び出す\n")
        print("【リファクタリング提案】")
        print("  → suggest_refactoring(client, sample_code) を呼び出す\n")
        print("【ストリーミング解説】")
        print("  → stream_code_explanation(client, sample_code, 'このコードの問題点は？') を呼び出す")
        return

    client = create_client()

    # 1. コードレビュー
    print("【1. コードレビュー】")
    review_result = review_code(client, sample_code)
    print(review_result)
    print()

    # 2. テスト生成
    print("【2. テスト生成 (pytest)】")
    test_code = generate_tests(client, sample_code, framework="pytest")
    print(test_code)
    print()

    # 3. リファクタリング提案
    print("【3. リファクタリング提案】")
    refactor_result = suggest_refactoring(client, sample_code)
    print(refactor_result)
    print()

    # 4. ストリーミング解説
    print("【4. ストリーミング解説】")
    stream_code_explanation(client, sample_code, "このコードにはどんな問題点がありますか？初学者向けに解説してください。")


if __name__ == "__main__":
    main()
