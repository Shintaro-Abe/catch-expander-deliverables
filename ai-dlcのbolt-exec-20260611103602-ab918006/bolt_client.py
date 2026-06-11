# PoC品質 - 本番利用前に認証・エラーハンドリング・レート制限対応を実装すること

"""
Bolt.new スタイルの AI コード生成クライアント
Anthropic Claude API を使用し、Bolt の「自然言語 → フルスタックアプリ」
生成パターンを Python から呼び出せる形で実装する。

AI-DLC における位置づけ: プロトタイピング・MVP生成フェーズ
"""

import os
import json
from typing import Iterator
import anthropic

# ---- 定数 ----------------------------------------------------------------

# Bolt.new が実際に使用しているシステムプロンプトの構造を再現
# 本家は数千トークンだが、ここではコア部分のみ抽出
BOLT_SYSTEM_PROMPT = """あなたはフルスタックWeb開発の専門家AIアシスタントです。
ユーザーのリクエストに基づき、<boltArtifact>タグ内に実装可能なコードを生成します。

## コード生成ルール
- ファイル操作は <boltAction type="file" filePath="..."> タグで示す
- シェルコマンドは <boltAction type="shell"> タグで示す
- 生成コードは即座に実行可能な完全なコードとする
- TypeScript / React / Tailwind CSS を優先使用する
- package.json には必要な依存パッケージをすべて含める

## 出力フォーマット例
<boltArtifact id="app" title="アプリケーション名">
  <boltAction type="file" filePath="package.json">
  { ... }
  </boltAction>
  <boltAction type="shell">npm install</boltAction>
  <boltAction type="file" filePath="src/App.tsx">
  ...
  </boltAction>
</boltArtifact>
"""

# プロンプトキャッシュ用の最小トークン数（Anthropic の仕様）
MIN_CACHE_TOKENS = 1024

# ---- メインクライアント ---------------------------------------------------

class BoltClient:
    """
    Bolt.new スタイルの AI コード生成クライアント。
    Anthropic Claude API のプロンプトキャッシュを活用し、
    反復的なコード生成のコストを削減する。
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 8192,
    ) -> None:
        self.client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self.model = model
        self.max_tokens = max_tokens
        # 会話履歴（マルチターン対応）
        self._history: list[dict] = []

    # ---- 単発生成 --------------------------------------------------------

    def generate(self, prompt: str) -> str:
        """
        自然言語プロンプトからフルスタックアプリのコードを生成する。
        システムプロンプトをキャッシュし、2回目以降のコスト・レイテンシを削減。
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": BOLT_SYSTEM_PROMPT,
                    # Anthropic プロンプトキャッシュ: システムプロンプトをキャッシュ
                    # 同一プロンプトの2回目以降はトークンコストが最大90%削減される
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    # ---- ストリーミング生成 -----------------------------------------------

    def generate_stream(self, prompt: str) -> Iterator[str]:
        """
        ストリーミングでコードを生成する。
        大規模プロジェクトのリアルタイム表示に使用。
        """
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": BOLT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield text

    # ---- マルチターン（フォローアップ）------------------------------------

    def chat(self, prompt: str) -> str:
        """
        マルチターン対話でコードを差分更新する。
        Bolt の「フォローアップで既存コードを修正」パターンを再現。
        """
        self._history.append({"role": "user", "content": prompt})

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": BOLT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=self._history,
        )

        assistant_message = response.content[0].text
        self._history.append({"role": "assistant", "content": assistant_message})
        return assistant_message

    def reset_history(self) -> None:
        """会話履歴をリセットする（新規プロジェクト開始時）"""
        self._history = []

    # ---- トークン使用量モニタリング ---------------------------------------

    def get_token_usage(self, prompt: str) -> dict:
        """
        トークン使用量を事前に見積もる。
        Bolt Pro プランの月間 10M トークン上限の管理に使用。
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1,  # コスト最小化のため1トークンのみ
            system=BOLT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return {
            "input_tokens": response.usage.input_tokens,
            "cache_read_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
            "cache_creation_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
        }


# ---- 使用例 --------------------------------------------------------------

def main() -> None:
    client = BoltClient()

    # 例1: 単発生成（ランディングページ）
    print("=== 例1: ランディングページ生成 ===")
    result = client.generate(
        "React + Tailwind CSS でシンプルなSaaSランディングページを作成してください。"
        "ヒーローセクション・特徴3点・CTAボタンを含めてください。"
    )
    print(result[:500], "...\n")

    # 例2: マルチターン（フォローアップ修正）
    print("=== 例2: マルチターン対話 ===")
    client.reset_history()
    resp1 = client.chat("TODOアプリのReactコンポーネントを作成してください。")
    print("初回生成完了")
    resp2 = client.chat("追加タスクの優先度（高/中/低）を選べるように修正してください。")
    print("フォローアップ修正完了")

    # 例3: ストリーミング出力
    print("\n=== 例3: ストリーミング生成 ===")
    for chunk in client.generate_stream("Next.js のAPIルートを1つ生成してください。"):
        print(chunk, end="", flush=True)
    print()


if __name__ == "__main__":
    main()
