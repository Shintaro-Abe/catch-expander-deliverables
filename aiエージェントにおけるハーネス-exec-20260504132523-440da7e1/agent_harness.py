# PoC品質: 本番利用前に認証・エラー処理・ビジネスロジックの追加が必要です

"""
エージェントハーネス コアモジュール（Agent Harness Core）
==========================================================
「Agent = Model + Harness」の Harness 部分を実装します。

■ ハーネスの4つのコアコンポーネント（本ファイルが担う）
  1. ツール管理（Tool Management）       → tools.py と連携
  2. エージェントループ制御（Loop Control）→ ReAct パターンを実装
  3. プロンプト管理（Prompt Management） → システムプロンプト組み立て・キャッシュ
  4. 状態管理（State Management）        → 会話履歴・セッション状態の保持

■ 使用モデル
  claude-sonnet-4-6（Anthropic Claude SDK）
  プロンプトキャッシュを有効化してコスト削減。
"""

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import anthropic

from tools import ToolRegistry, default_registry


# ---------------------------------------------------------------------------
# 設定クラス
# ---------------------------------------------------------------------------
@dataclass
class HarnessConfig:
    """
    ハーネスの動作パラメータをまとめた設定クラス。
    環境変数から読み込むことで、コードを変更せずに調整できます。
    """
    model: str = "claude-sonnet-4-6"
    max_turns: int = 20          # 無限ループ防止のための最大ターン数
    max_tokens: int = 8192       # 1 回の応答で生成するトークンの上限
    system_prompt: str = (
        "あなたは有能なアシスタントエージェントです。"
        "ユーザーのタスクを達成するために利用可能なツールを積極的に活用してください。"
        "不明な点があれば確認し、安全性を最優先に行動してください。"
    )
    enable_prompt_cache: bool = True   # プロンプトキャッシュを使うか
    approval_required: bool = False    # 破壊的ツールの実行前に確認を求めるか


# ---------------------------------------------------------------------------
# セッション状態クラス
# ---------------------------------------------------------------------------
@dataclass
class SessionState:
    """
    エージェントループの一連の会話状態を保持するクラス。
    コンテキストウィンドウの蓄積を追跡し、コンパクションの判断に使います。
    """
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[dict] = field(default_factory=list)   # 会話履歴
    total_input_tokens: int = 0    # セッション累積入力トークン
    total_output_tokens: int = 0   # セッション累積出力トークン
    cache_creation_tokens: int = 0 # キャッシュ書き込みトークン（コスト半額）
    cache_read_tokens: int = 0     # キャッシュ読み出しトークン（コスト1/10）
    turn_count: int = 0
    started_at: float = field(default_factory=time.time)

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: list[dict]) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_result(self, tool_use_id: str, result: str) -> None:
        self.messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result,
            }],
        })

    def update_usage(self, usage: anthropic.types.Usage) -> None:
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        if hasattr(usage, "cache_creation_input_tokens"):
            self.cache_creation_tokens += usage.cache_creation_input_tokens or 0
        if hasattr(usage, "cache_read_input_tokens"):
            self.cache_read_tokens += usage.cache_read_input_tokens or 0

    def cost_summary(self) -> dict:
        """トークン使用状況のサマリーを返す（コスト最適化の参考に）。"""
        return {
            "session_id": self.session_id,
            "turns": self.turn_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "elapsed_seconds": round(time.time() - self.started_at, 2),
        }


# ---------------------------------------------------------------------------
# AgentHarness: ハーネスのメインクラス
# ---------------------------------------------------------------------------
class AgentHarness:
    """
    AIエージェントハーネスのコア実装。

    ReAct（Reasoning + Acting）パターンを使って
    「考える → ツールを使う → 観察する → 繰り返す」ループを実装します。

    使い方:
        harness = AgentHarness()
        result = harness.run("東京の天気を調べて、気温をケルビンに変換してください")
        print(result.final_text)
    """

    def __init__(
        self,
        config: HarnessConfig | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.config = config or HarnessConfig()
        self.registry = registry or default_registry
        # ANTHROPIC_API_KEY 環境変数から自動読み込み
        self.client = anthropic.Anthropic()

    # -----------------------------------------------------------------------
    # プロンプト管理: システムプロンプトの組み立て
    # -----------------------------------------------------------------------
    def _build_system_prompt(self) -> list[dict] | str:
        """
        プロンプトキャッシュ対応のシステムプロンプトを構築する。

        cache_control: {"type": "ephemeral"} を付けると、
        このプロンプトブロックが5分間キャッシュされ、
        同じ内容を繰り返し送るコストが約90%削減される。
        """
        if not self.config.enable_prompt_cache:
            return self.config.system_prompt

        return [
            {
                "type": "text",
                "text": self.config.system_prompt,
                # キャッシュ制御: 変化しないシステムプロンプトをキャッシュ
                "cache_control": {"type": "ephemeral"},
            }
        ]

    # -----------------------------------------------------------------------
    # ツール実行: モデルが要求したツールを実際に動かす
    # -----------------------------------------------------------------------
    def _execute_tool(self, tool_use_block: anthropic.types.ToolUseBlock) -> str:
        """
        ToolUseBlock を受け取り、対応するツールを実行して結果を文字列で返す。
        requires_approval=True のツールは、実行前にユーザー確認を求める（PoC では標準入力）。
        """
        tool_def = self.registry.get(tool_use_block.name)

        # 承認が必要なツールの確認フロー（Human-in-the-Loop）
        if (
            tool_def
            and tool_def.requires_approval
            and self.config.approval_required
        ):
            print(f"\n[承認要求] ツール '{tool_use_block.name}' の実行")
            print(f"  入力: {tool_use_block.input}")
            approval = input("  実行を許可しますか？ (y/n): ").strip().lower()
            if approval != "y":
                return "ユーザーによって実行が拒否されました"

        try:
            result = self.registry.execute(tool_use_block.name, tool_use_block.input)
            return str(result)
        except Exception as e:
            return f"ツール実行エラー [{tool_use_block.name}]: {e}"

    # -----------------------------------------------------------------------
    # エージェントループ: ReAct パターンの中核
    # -----------------------------------------------------------------------
    def run(self, user_input: str, session: SessionState | None = None) -> SessionState:
        """
        ユーザー入力を受け取り、エージェントループを回して結果を返す。

        ReAct ループの流れ:
            1. ユーザー入力をメッセージ履歴に追加
            2. Claude にメッセージ・ツール定義・システムプロンプトを送信
            3. Claude が text を返したら → ループ終了
            4. Claude が tool_use を返したら → ツール実行 → 結果を履歴に追加 → 2 に戻る
            5. max_turns に達したら → 強制終了

        Args:
            user_input: ユーザーからの入力テキスト
            session: 継続セッション（None の場合は新規作成）

        Returns:
            SessionState: 会話履歴・トークン使用量などが含まれる状態オブジェクト
        """
        if session is None:
            session = SessionState()

        session.add_user_message(user_input)
        tools_schema = self.registry.to_anthropic_schema()

        print(f"\n{'='*60}")
        print(f"セッション開始: {session.session_id}")
        print(f"{'='*60}")

        # ── ReAct ループ ──────────────────────────────────────────────────
        while session.turn_count < self.config.max_turns:
            session.turn_count += 1
            print(f"\n[ターン {session.turn_count}/{self.config.max_turns}] Claude に送信中...")

            # モデルへのリクエスト
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=self._build_system_prompt(),
                tools=tools_schema,
                messages=session.messages,
            )

            # トークン使用量を記録
            session.update_usage(response.usage)
            session.add_assistant_message(response.content)

            # ── 応答ブロックを処理 ────────────────────────────────────────
            has_tool_use = False
            for block in response.content:
                if block.type == "text":
                    print(f"\n[Claude]: {block.text}")

                elif block.type == "tool_use":
                    has_tool_use = True
                    print(f"\n[ツール呼び出し]: {block.name}({block.input})")
                    result = self._execute_tool(block)
                    print(f"[ツール結果]: {result[:200]}{'...' if len(result) > 200 else ''}")
                    session.add_tool_result(block.id, result)

            # ── ループ終了条件 ────────────────────────────────────────────
            # stop_reason == "end_turn": ツール呼び出しなし → タスク完了
            if response.stop_reason == "end_turn" and not has_tool_use:
                print(f"\n✓ タスク完了 (ターン数: {session.turn_count})")
                break

            # stop_reason == "tool_use": ツール結果を追加してループ継続
            if response.stop_reason == "tool_use":
                continue

            # 想定外の stop_reason はループを終了
            print(f"[警告] 予期しない stop_reason: {response.stop_reason}")
            break

        else:
            print(f"[警告] max_turns ({self.config.max_turns}) に達しました")

        # コスト情報を表示
        print(f"\n[コストサマリー] {session.cost_summary()}")
        return session

    @property
    def final_text(self) -> str:
        """最後のセッションのアシスタント応答テキストを取得するユーティリティ。"""
        raise AttributeError("SessionState オブジェクトから .messages[-1] を参照してください")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 環境変数 ANTHROPIC_API_KEY が必要です
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("エラー: 環境変数 ANTHROPIC_API_KEY を設定してください")
        raise SystemExit(1)

    harness = AgentHarness(
        config=HarnessConfig(
            max_turns=10,
            system_prompt=(
                "あなたは数学と情報検索が得意なアシスタントです。"
                "ツールを積極的に使ってユーザーの質問に答えてください。"
            ),
        )
    )

    session = harness.run("144 の平方根と、2の10乗をそれぞれ計算してください")
    print(f"\n最終メッセージ数: {len(session.messages)}")
