# PoC品質 - 本番利用前に認証・エラーハンドリング・セキュリティレビューを行うこと
"""
AIエージェントハーネス - コアモジュール

概念: Agent = Model + Harness
ハーネスはLLM（大規模言語モデル）本体を除く「すべて」を担う層。
ツール管理・メモリ・コンテキスト・ライフサイクルフック・信頼性制御を一元管理する。
"""

from __future__ import annotations

import time
import uuid
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

import anthropic  # pip install anthropic

from memory import MemoryManager
from observability import ObservabilityCollector, Span
from tools import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

class HookEvent(Enum):
    """ライフサイクルフックの発火点"""
    SESSION_START   = auto()   # セッション開始時
    PRE_TOOL_USE    = auto()   # ツール実行前
    POST_TOOL_USE   = auto()   # ツール実行後
    PRE_COMPACT     = auto()   # コンテキスト圧縮前
    POST_COMPACT    = auto()   # コンテキスト圧縮後
    STOP            = auto()   # エージェント停止試行時


@dataclass
class HookContext:
    """フック呼び出し時に渡されるコンテキスト"""
    event: HookEvent
    session_id: str
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_result: Optional[Any] = None
    # フック側から "block=True" をセットするとツール実行を拒否できる
    block: bool = False
    block_reason: str = ""


@dataclass
class AgentConfig:
    """
    ハーネスの設定。シークレット類は環境変数から取得すること。
    （ANTHROPIC_API_KEY 等は os.environ 経由で渡す）
    """
    model: str = "claude-opus-4-7"        # 使用するClaudeモデル
    max_tokens: int = 4096                 # 1回の推論での最大出力トークン数
    max_turns: int = 20                    # エージェントループの最大ターン数
    context_window_limit: int = 160_000   # コンテキスト上限（トークン概算）
    compact_threshold: float = 0.8        # この割合を超えたらコンパクション実行
    retry_max: int = 3                    # LLM呼び出し失敗時の最大リトライ回数
    retry_base_delay: float = 1.0         # 指数バックオフの基底秒数
    enable_prompt_cache: bool = True      # プロンプトキャッシング有効化フラグ


# ---------------------------------------------------------------------------
# サーキットブレーカー（信頼性パターン）
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    CLOSED    = "closed"      # 正常（全リクエスト通過）
    OPEN      = "open"        # 障害検知（リクエスト即時拒否）
    HALF_OPEN = "half_open"   # 回復確認中（一部リクエストのみ通過）


class CircuitBreaker:
    """
    3状態ステートマシンによるサーキットブレーカー。
    LLM APIの一時的障害からシステム全体を保護する。
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 2,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        # OPEN 状態でも回復タイムアウト経過後は HALF_OPEN へ自動遷移
        if (
            self._state == CircuitState.OPEN
            and self._last_failure_time is not None
            and time.monotonic() - self._last_failure_time >= self.recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_calls = 0
        return self._state

    def allow_request(self) -> bool:
        s = self.state
        if s == CircuitState.CLOSED:
            return True
        if s == CircuitState.HALF_OPEN:
            return self._half_open_calls < self.half_open_max_calls
        return False  # OPEN

    def record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_calls += 1
            if self._half_open_calls >= self.half_open_max_calls:
                logger.info("[CircuitBreaker] 回復確認完了 → CLOSED に遷移")
                self._state = CircuitState.CLOSED
                self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            logger.warning(
                "[CircuitBreaker] 障害しきい値超過 → OPEN に遷移 "
                f"(failures={self._failure_count})"
            )
            self._state = CircuitState.OPEN


# ---------------------------------------------------------------------------
# ハーネス本体
# ---------------------------------------------------------------------------

class AgentHarness:
    """
    AIエージェントハーネス本体。

    責務:
      - エージェントループの制御（何ターン・どのツールを・いつ呼ぶか）
      - ツール実行と結果のLLMへのフィードバック
      - コンテキストウィンドウの監視と自動コンパクション
      - ライフサイクルフックによる外部制約の強制
      - 指数バックオフ付きリトライ＋サーキットブレーカー
      - 観測性テレメトリ（トレース・メトリクス・ログ）の収集

    使い方:
        harness = AgentHarness(config=AgentConfig(), tools=my_registry)
        harness.add_hook(HookEvent.PRE_TOOL_USE, audit_hook)
        result = harness.run("コードベースのバグを修正してください")
    """

    def __init__(
        self,
        config: AgentConfig,
        tools: ToolRegistry,
        memory: Optional[MemoryManager] = None,
        obs: Optional[ObservabilityCollector] = None,
    ) -> None:
        self.config = config
        self.tools = tools
        self.memory = memory or MemoryManager()
        self.obs = obs or ObservabilityCollector()
        self._hooks: dict[HookEvent, list[Callable[[HookContext], None]]] = {
            e: [] for e in HookEvent
        }
        self._circuit_breaker = CircuitBreaker()
        # Anthropic クライアント（APIキーは環境変数 ANTHROPIC_API_KEY から自動取得）
        self._client = anthropic.Anthropic()

    # ------------------------------------------------------------------
    # フック登録 API
    # ------------------------------------------------------------------

    def add_hook(self, event: HookEvent, fn: Callable[[HookContext], None]) -> None:
        """指定イベントにフック関数を登録する"""
        self._hooks[event].append(fn)

    def _fire_hook(self, ctx: HookContext) -> HookContext:
        """登録済みフックを順番に実行し、コンテキストを返す"""
        for fn in self._hooks[ctx.event]:
            fn(ctx)
            if ctx.block:
                logger.warning(
                    f"[Hook] ツール '{ctx.tool_name}' がブロックされました: {ctx.block_reason}"
                )
                break
        return ctx

    # ------------------------------------------------------------------
    # メインエージェントループ
    # ------------------------------------------------------------------

    def run(self, user_prompt: str, session_id: Optional[str] = None) -> str:
        """
        ユーザープロンプトを受け取り、エージェントループを実行して最終応答を返す。

        Parameters
        ----------
        user_prompt : str
            ユーザーからの指示・質問
        session_id : str, optional
            セッションID（省略時は自動生成）。再開時は同じIDを渡す。

        Returns
        -------
        str
            エージェントの最終テキスト応答
        """
        session_id = session_id or str(uuid.uuid4())
        root_span = self.obs.start_span("agent_run", {"session_id": session_id})

        # セッション開始フック
        self._fire_hook(HookContext(HookEvent.SESSION_START, session_id))

        # メモリから関連コンテキストを取得してシステムプロンプトに注入
        memory_ctx = self.memory.retrieve_relevant(user_prompt, top_k=5)
        system_prompt = self._build_system_prompt(memory_ctx)

        messages: list[dict] = [{"role": "user", "content": user_prompt}]
        tool_definitions = self.tools.get_definitions()

        try:
            for turn in range(self.config.max_turns):
                # コンテキストサイズの監視と自動コンパクション
                messages = self._maybe_compact(messages, session_id)

                # LLM呼び出し（リトライ＋サーキットブレーカー付き）
                response = self._call_llm_with_retry(
                    messages=messages,
                    system=system_prompt,
                    tools=tool_definitions,
                    span=root_span,
                )

                # 停止理由の分岐
                if response.stop_reason == "end_turn":
                    # ツール呼び出しなし → 最終応答
                    final_text = self._extract_text(response)
                    self.memory.store(user_prompt, final_text)
                    self.obs.end_span(root_span, {"turns": turn + 1, "status": "success"})
                    return final_text

                if response.stop_reason == "tool_use":
                    # ツール呼び出しを処理してメッセージ履歴に追加
                    messages = self._process_tool_calls(
                        response=response,
                        messages=messages,
                        session_id=session_id,
                    )
                    continue

                # その他の停止理由（max_tokens 等）
                logger.warning(f"[Turn {turn}] 予期しない stop_reason: {response.stop_reason}")
                break

            # 最大ターン数到達
            logger.error(f"最大ターン数 {self.config.max_turns} に到達しました")
            self.obs.end_span(root_span, {"status": "max_turns_exceeded"})
            return "[エラー] エージェントが最大ターン数に達しました。タスクを分割して再試行してください。"

        except Exception as exc:
            logger.exception(f"エージェントループで例外が発生: {exc}")
            self.obs.end_span(root_span, {"status": "error", "error": str(exc)})
            raise

    # ------------------------------------------------------------------
    # LLM呼び出し（リトライ＋サーキットブレーカー）
    # ------------------------------------------------------------------

    def _call_llm_with_retry(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        span: Span,
    ) -> Any:
        """
        指数バックオフ付きリトライ＋サーキットブレーカーで LLM を呼び出す。

        リトライ戦略:
          - 一時的障害（レートリミット・ネットワーク等）: 指数バックオフで再試行
          - 恒久的障害: サーキットブレーカーで早期失敗してシステムを保護
        """
        last_exc: Optional[Exception] = None

        for attempt in range(self.config.retry_max):
            if not self._circuit_breaker.allow_request():
                raise RuntimeError(
                    "サーキットブレーカーが OPEN 状態です。LLM API への呼び出しを一時停止中。"
                )

            try:
                llm_span = self.obs.start_span(
                    "llm_call",
                    {"model": self.config.model, "attempt": attempt},
                    parent=span,
                )

                # プロンプトキャッシュを有効化する場合はシステムプロンプトに
                # cache_control を付与（大きなシステムプロンプトのトークンコスト削減）
                system_param: Any = system
                if self.config.enable_prompt_cache:
                    system_param = [
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]

                response = self._client.messages.create(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    system=system_param,
                    messages=messages,
                    tools=tools,
                )

                self._circuit_breaker.record_success()
                self.obs.end_span(
                    llm_span,
                    {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "stop_reason": response.stop_reason,
                    },
                )
                return response

            except anthropic.RateLimitError as exc:
                # レートリミットは一時的障害 → リトライ対象
                last_exc = exc
                self._circuit_breaker.record_failure()
                delay = self.config.retry_base_delay * (2 ** attempt)
                logger.warning(f"[Retry {attempt+1}] レートリミット。{delay:.1f}秒後に再試行...")
                time.sleep(delay)

            except anthropic.APIConnectionError as exc:
                # ネットワーク障害 → リトライ対象
                last_exc = exc
                self._circuit_breaker.record_failure()
                delay = self.config.retry_base_delay * (2 ** attempt)
                logger.warning(f"[Retry {attempt+1}] 接続エラー。{delay:.1f}秒後に再試行...")
                time.sleep(delay)

            except anthropic.APIStatusError as exc:
                # 4xx 系クライアントエラーはリトライ不可
                self._circuit_breaker.record_failure()
                raise

        raise RuntimeError(
            f"LLM呼び出しが {self.config.retry_max} 回リトライ後も失敗しました"
        ) from last_exc

    # ------------------------------------------------------------------
    # ツール呼び出し処理
    # ------------------------------------------------------------------

    def _process_tool_calls(
        self,
        response: Any,
        messages: list[dict],
        session_id: str,
    ) -> list[dict]:
        """
        LLMからのツール呼び出しリクエストを処理し、結果をメッセージ履歴に追加する。

        セキュリティ: 外部データ（ツール結果）はすべて非信頼として扱い、
        フックがブロックできる設計になっている。
        """
        # アシスタントの応答をメッセージ履歴に追加
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []

        for content_block in response.content:
            if content_block.type != "tool_use":
                continue

            tool_name = content_block.name
            tool_input = content_block.input
            tool_use_id = content_block.id

            # PRE_TOOL_USE フック（監査ログ・ブロック・入力変換に使用）
            hook_ctx = self._fire_hook(
                HookContext(
                    event=HookEvent.PRE_TOOL_USE,
                    session_id=session_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                )
            )

            if hook_ctx.block:
                # フックがブロックした場合はエラー結果を返す
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "is_error": True,
                    "content": f"ツール '{tool_name}' はポリシーによりブロックされました: {hook_ctx.block_reason}",
                })
                continue

            # ツール実行
            tool_span = self.obs.start_span(
                "tool_call", {"tool": tool_name, "session": session_id}
            )
            try:
                result = self.tools.execute(tool_name, tool_input)
                is_error = False
            except Exception as exc:
                result = f"ツール実行エラー: {exc}"
                is_error = True
                logger.error(f"[Tool] '{tool_name}' 実行失敗: {exc}")

            self.obs.end_span(tool_span, {"is_error": is_error})

            # POST_TOOL_USE フック（結果の検証・変換・ログに使用）
            self._fire_hook(
                HookContext(
                    event=HookEvent.POST_TOOL_USE,
                    session_id=session_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_result=result,
                )
            )

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "is_error": is_error,
                "content": str(result),
            })

        messages.append({"role": "user", "content": tool_results})
        return messages

    # ------------------------------------------------------------------
    # コンテキスト管理
    # ------------------------------------------------------------------

    def _maybe_compact(self, messages: list[dict], session_id: str) -> list[dict]:
        """
        コンテキストウィンドウ使用量が閾値を超えた場合に自動コンパクション
        （古いメッセージを要約して圧縮）を実行する。

        コンパクションはコンテキスト腐敗（Context Rot）を防ぎ、
        長時間タスクの継続を可能にする。
        """
        estimated_tokens = self._estimate_token_count(messages)
        threshold_tokens = int(self.config.context_window_limit * self.config.compact_threshold)

        if estimated_tokens < threshold_tokens:
            return messages

        logger.info(
            f"[Compact] コンテキスト使用量 {estimated_tokens} トークンが閾値 "
            f"{threshold_tokens} を超過。コンパクションを実行します..."
        )
        self._fire_hook(HookContext(HookEvent.PRE_COMPACT, session_id))

        # 最初のユーザーメッセージと直近 N ターンは保持し、中間部分を要約で置換
        # （実際の要約ロジックはLLMを使用する — ここはスケルトン）
        compacted = self._summarize_old_messages(messages)

        self._fire_hook(HookContext(HookEvent.POST_COMPACT, session_id))
        logger.info(f"[Compact] 完了。{len(messages)} → {len(compacted)} メッセージに削減")
        return compacted

    def _summarize_old_messages(self, messages: list[dict]) -> list[dict]:
        """
        古いメッセージ群をLLMで要約し、コンテキストを削減する（スケルトン）。
        本番実装では別LLM呼び出しで要約テキストを生成すること。
        """
        if len(messages) <= 4:
            return messages

        # 最初のシステム設定メッセージ + 直近4ターンを保持
        keep_recent = 4
        old_messages = messages[:-keep_recent]
        recent_messages = messages[-keep_recent:]

        # TODO: 実際には LLM 呼び出しで old_messages を要約テキストに変換する
        summary_text = f"[会話履歴の要約: {len(old_messages)}件のメッセージが圧縮されました]"

        return [
            {"role": "user", "content": summary_text},
            {"role": "assistant", "content": "了解しました。要約された文脈を把握しました。"},
            *recent_messages,
        ]

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    def _build_system_prompt(self, memory_ctx: list[str]) -> str:
        """メモリコンテキストを注入したシステムプロンプトを構築する"""
        base = (
            "あなたは優秀なAIアシスタントです。"
            "与えられたツールを使って、ユーザーのタスクを段階的に解決してください。"
            "不確かな場合は確認してから行動し、破壊的な操作は慎重に行ってください。"
        )
        if memory_ctx:
            ctx_block = "\n".join(f"- {c}" for c in memory_ctx)
            return f"{base}\n\n## 関連する過去のコンテキスト\n{ctx_block}"
        return base

    def _extract_text(self, response: Any) -> str:
        """レスポンスからテキストコンテンツを抽出する"""
        parts = [
            block.text
            for block in response.content
            if hasattr(block, "text")
        ]
        return "\n".join(parts)

    def _estimate_token_count(self, messages: list[dict]) -> int:
        """メッセージリストのトークン数を概算する（4文字≒1トークン）"""
        total_chars = sum(
            len(str(msg.get("content", ""))) for msg in messages
        )
        return total_chars // 4


# ---------------------------------------------------------------------------
# 使用例
# ---------------------------------------------------------------------------

def example_audit_hook(ctx: HookContext) -> None:
    """
    監査ログフックの例。
    PRE_TOOL_USE に登録することで、全ツール呼び出しを記録できる。
    """
    if ctx.event == HookEvent.PRE_TOOL_USE:
        logger.info(f"[Audit] ツール呼び出し: {ctx.tool_name} | 入力: {ctx.tool_input}")


def example_block_hook(ctx: HookContext) -> None:
    """
    危険なコマンドをブロックするフックの例。
    rm -rf などの破壊的コマンドを実行前に遮断する。
    """
    DANGEROUS_COMMANDS = ["rm -rf", "DROP TABLE", "format c:", ":(){ :|:& };:"]
    if ctx.event == HookEvent.PRE_TOOL_USE and ctx.tool_name == "bash":
        command = str(ctx.tool_input or "")
        for dangerous in DANGEROUS_COMMANDS:
            if dangerous in command:
                ctx.block = True
                ctx.block_reason = f"危険なコマンドパターン '{dangerous}' が検出されました"
                return


if __name__ == "__main__":
    # 基本的な動作確認（APIキーは環境変数 ANTHROPIC_API_KEY に設定しておくこと）
    import os
    from tools import ToolRegistry, EchoTool

    logging.basicConfig(level=logging.INFO)

    # ツールレジストリのセットアップ
    registry = ToolRegistry()
    registry.register(EchoTool())

    # ハーネスの設定と初期化
    config = AgentConfig(
        model="claude-opus-4-7",
        max_turns=5,
        enable_prompt_cache=True,
    )
    harness = AgentHarness(config=config, tools=registry)

    # フックの登録（監査ログ＋危険コマンドブロック）
    harness.add_hook(HookEvent.PRE_TOOL_USE, example_audit_hook)
    harness.add_hook(HookEvent.PRE_TOOL_USE, example_block_hook)

    # エージェント実行
    answer = harness.run("こんにちは！テストメッセージをエコーしてください。")
    print(f"\n=== エージェント応答 ===\n{answer}")
