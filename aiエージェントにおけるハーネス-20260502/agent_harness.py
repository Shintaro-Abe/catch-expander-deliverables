# PoC品質 - このコードは概念実証用です。本番環境での利用前に十分なテストと改修を行ってください。
"""
AIエージェントハーネス（Agent Harness）メインモジュール

このモジュールは、AIエージェントのコアループ（ReAct パターン）と
状態管理・ループ制御・観測性を統合したハーネス実装です。

【ハーネスとは？】
ハーネス = エージェントを動かすための「オフィス環境全体」

  ┌─────────────────────────────────────────────────┐
  │               AIエージェントハーネス             │
  │  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
  │  │ ツール    │  │ メモリ   │  │ エラー処理   │  │
  │  │ レジストリ│  │ マネージャ│  │（リトライ等）│  │
  │  └──────────┘  └──────────┘  └─────────────┘  │
  │         ↑              ↑              ↑          │
  │  ┌──────────────────────────────────────────┐   │
  │  │         ReAct ループ制御                  │   │
  │  │  思考(Thought) → 行動(Action) → 観察     │   │
  │  │  (Observation) → 思考 → ... → 完了       │   │
  │  └──────────────────────────────────────────┘   │
  │         ↑                                        │
  │  ┌──────────────────────────────────────────┐   │
  │  │         LLM（Amazon Bedrock Converse API）│   │
  │  └──────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────┘

【ReAct パターンとは？】
「Reasoning（推論）+ Acting（行動）」の略。
1. LLMが「次にすべき行動」を思考（Thought）
2. 特定のツールを呼び出す（Action）
3. ツール実行結果を受け取る（Observation）
4. Observation をコンテキストに追加し次のサイクルへ

この繰り返しにより、エージェントは複雑なタスクを段階的に解決します。

【AWS サービスとの関係】
このファイル単体は boto3 が不要なスタンドアロン実装です。
AWS Bedrock と接続する場合は BEDROCK_CLIENT_AVAILABLE フラグを True に変更し、
_call_llm メソッド内で boto3 の converse() API を呼び出してください。
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from error_handler import AgentErrorHandler, RetryConfig, CircuitBreakerConfig
from memory_manager import MemoryManager
from tool_registry import ToolRegistry, registry as default_registry

logger = logging.getLogger(__name__)

# boto3 が利用可能な環境では True に変更してください
BEDROCK_CLIENT_AVAILABLE = False


# --------------------------------------------------------------------------- #
# 状態管理（ステートマシン）
# --------------------------------------------------------------------------- #

class AgentState(enum.Enum):
    """
    エージェントのライフサイクル状態。

    【初学者向け補足】
    状態管理は「プロジェクトの進捗ボード」に相当します。
    今どの段階にいるか・何が完了したか・次は何をすべきかを常に把握し、
    途中で中断しても再開できるようにします。

    IDLE      → 待機中（タスク未開始）
    PLANNING  → タスクを計画中（LLM が方針を考えている）
    EXECUTING → ツールを実行中
    VERIFYING → 結果を検証中
    COMPLETED → タスク完了
    ERROR     → エラー発生
    ABORTED   → 上限・制限に達して中断
    """
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    ERROR = "error"
    ABORTED = "aborted"


@dataclass
class AgentStateRecord:
    """状態遷移の1レコード（監査ログ用）。"""
    from_state: AgentState
    to_state: AgentState
    reason: str
    timestamp: float = field(default_factory=time.time)
    turn_number: int = 0


# --------------------------------------------------------------------------- #
# バジェット管理（暴走実行防止）
# --------------------------------------------------------------------------- #

@dataclass
class BudgetConfig:
    """
    エージェントの実行上限設定。

    【重要】
    LLM エージェントは適切な上限がないと無限ループに陥る可能性があります。
    特に本番環境では必ずこれらの上限を設定してください。

    Attributes:
        max_turns          : 最大ターン数（1ターン = LLM呼び出し1回）
        max_tool_calls     : 最大ツール呼び出し回数（全ターン合計）
        max_tokens_total   : 最大トークン消費量（概算）
        global_timeout_sec : エージェント全体のタイムアウト秒数
    """
    max_turns: int = 20
    max_tool_calls: int = 50
    max_tokens_total: int = 100_000
    global_timeout_sec: float = 300.0  # 5分


# --------------------------------------------------------------------------- #
# ループ検出（同一状態の繰り返し防止）
# --------------------------------------------------------------------------- #

class LoopDetector:
    """
    同一アクションの繰り返しを検出してエージェントの無限ループを防ぐ。

    SHA256 ハッシュでコンテキストの状態をフィンガープリントし、
    同一ハッシュが consecutive_threshold 回連続した場合にループと判定します。
    """

    def __init__(self, consecutive_threshold: int = 3) -> None:
        self.threshold = consecutive_threshold
        self._hash_counts: Dict[str, int] = {}
        self._last_hash: Optional[str] = None

    def check(self, context_hash: str) -> bool:
        """
        ループを検出した場合 True を返す。

        Args:
            context_hash: 現在のコンテキストのハッシュ
        Returns:
            True の場合はループ検出 → エージェントを停止すべき
        """
        if context_hash == self._last_hash:
            self._hash_counts[context_hash] = self._hash_counts.get(context_hash, 1) + 1
        else:
            self._hash_counts[context_hash] = 1
            self._last_hash = context_hash

        return self._hash_counts[context_hash] >= self.threshold

    def reset(self) -> None:
        self._hash_counts.clear()
        self._last_hash = None


# --------------------------------------------------------------------------- #
# ハーネスの設定
# --------------------------------------------------------------------------- #

@dataclass
class HarnessConfig:
    """
    エージェントハーネスの全設定を保持するデータクラス。

    この設定オブジェクトをカスタマイズすることで、
    エージェントの動作を柔軟に制御できます。
    """
    model_id: str = "anthropic.claude-sonnet-4-5"
    system_prompt: str = "あなたは有能なAIアシスタントです。ツールを活用してタスクを解決してください。"
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    retry: RetryConfig = field(default_factory=lambda: RetryConfig(max_attempts=3, base_delay_sec=1.0))
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    loop_detect_threshold: int = 3
    allowed_tools: Optional[List[str]] = None  # None の場合は全ツールを許可
    verbose: bool = False  # True の場合は詳細ログを出力


# --------------------------------------------------------------------------- #
# メインハーネスクラス
# --------------------------------------------------------------------------- #

class AgentHarness:
    """
    AIエージェントハーネスのメインクラス。

    このクラスがエージェントのライフサイクル全体を管理します:
    - ReAct ループの制御（思考→行動→観察）
    - 状態機械による進捗管理
    - ツールレジストリとの統合
    - メモリ管理（短期・長期）
    - エラーハンドリング（リトライ・フォールバック・サーキットブレーカー）
    - ループ検出と暴走防止

    使い方:
        config = HarnessConfig(model_id="anthropic.claude-sonnet-4-5")
        harness = AgentHarness(config)
        result = harness.run("2 + 3 * 4 を計算してください")
        print(result)
    """

    def __init__(
        self,
        config: Optional[HarnessConfig] = None,
        tool_registry: Optional[ToolRegistry] = None,
        memory: Optional[MemoryManager] = None,
    ) -> None:
        self.config = config or HarnessConfig()
        self.tools = tool_registry or default_registry
        self.memory = memory or MemoryManager(
            max_context_tokens=self.config.budget.max_tokens_total // 4,
        )
        self.error_handler = AgentErrorHandler(
            retry_config=self.config.retry,
            circuit_config=self.config.circuit_breaker,
        )
        self.loop_detector = LoopDetector(self.config.loop_detect_threshold)

        # 状態管理
        self._state = AgentState.IDLE
        self._state_history: List[AgentStateRecord] = []

        # 実行統計
        self._turn_count = 0
        self._tool_call_count = 0
        self._total_tokens_est = 0
        self._start_time: float = 0.0

        # システムプロンプトをメモリに設定
        self.memory.working.set_system_prompt(self.config.system_prompt)

    # ---------------------------------------------------------------- #
    # メインエントリポイント
    # ---------------------------------------------------------------- #

    def run(self, user_input: str) -> str:
        """
        エージェントを実行してタスクを解決する。

        Args:
            user_input: ユーザーからの指示・質問

        Returns:
            エージェントの最終応答テキスト

        Raises:
            RuntimeError: エラー状態またはアボート状態で終了した場合
        """
        self._start_time = time.time()
        self._transition_state(AgentState.PLANNING, "新規タスク開始")

        # ユーザーメッセージをメモリに追加
        self.memory.add_user_message(user_input)

        logger.info("エージェント実行開始: input='%s'", user_input[:50])

        try:
            final_answer = self._react_loop()
            self._transition_state(AgentState.COMPLETED, "タスク完了")
            return final_answer
        except Exception as exc:
            self._transition_state(AgentState.ERROR, f"エラー: {exc}")
            logger.error("エージェント実行エラー: %s", exc, exc_info=True)
            raise
        finally:
            elapsed = time.time() - self._start_time
            logger.info(
                "実行完了: turns=%d tool_calls=%d elapsed=%.2fs state=%s",
                self._turn_count,
                self._tool_call_count,
                elapsed,
                self._state.value,
            )

    # ---------------------------------------------------------------- #
    # ReAct ループ
    # ---------------------------------------------------------------- #

    def _react_loop(self) -> str:
        """
        ReAct（Reasoning + Acting）ループの実装。

        ループ継続条件:
        1. stop_reason == "end_turn"  → LLM が最終応答を返した → ループ終了
        2. stop_reason == "tool_use"  → LLM がツール呼び出しを要求 → ツール実行→継続
        3. バジェット上限到達        → ABORTED 状態で中断
        4. ループ検出（同一状態繰り返し）→ ABORTED 状態で中断

        【重要な理解】
        モデルは「自律的に決定している」のではなく、
        ツール実行結果を含む完全な会話履歴（messages[]）を受け取り
        次のトークンを生成しているだけです。
        透明性が高くデバッグが容易なのはこのためです。
        """
        while True:
            # --- バジェットチェック ---
            if not self._check_budget():
                self._transition_state(AgentState.ABORTED, "バジェット上限到達")
                return "エージェントが実行上限に達したため処理を中断しました。"

            # --- ループ検出 ---
            context_hash = self.memory.content_hash()
            if self.loop_detector.check(context_hash):
                self._transition_state(AgentState.ABORTED, "無限ループ検出")
                logger.warning("無限ループを検出しました。エージェントを停止します。")
                return "エージェントが同じ操作を繰り返しているため処理を中断しました。"

            # --- LLM 呼び出し ---
            self._turn_count += 1
            self._transition_state(AgentState.PLANNING, f"ターン {self._turn_count}")

            response = self._call_llm_with_protection()
            stop_reason = response.get("stop_reason", "end_turn")
            content = response.get("content", [])

            if self.config.verbose:
                logger.debug("LLM応答: stop_reason=%s content_blocks=%d", stop_reason, len(content))

            # --- 停止条件: LLM が最終応答を返した ---
            if stop_reason == "end_turn":
                final_text = self._extract_text(content)
                self.memory.add_assistant_message(final_text)
                return final_text

            # --- ツール呼び出し要求: ツールを実行してコンテキストに追加 ---
            if stop_reason == "tool_use":
                self._transition_state(AgentState.EXECUTING, "ツール実行")
                tool_results = self._execute_tools(content)

                # アシスタントのターンをメモリに追加
                assistant_content = self._extract_text(content)
                if assistant_content:
                    self.memory.add_assistant_message(assistant_content)

                # ツール結果をメモリに追加（user ロールで返すのが Converse API の仕様）
                tool_result_text = json.dumps(tool_results, ensure_ascii=False)
                self.memory.add_user_message(f"[ツール実行結果]\n{tool_result_text}")

                self._transition_state(AgentState.VERIFYING, "ツール結果検証")
                continue

            # --- 未知の stop_reason ---
            logger.warning("未知の stop_reason: %s", stop_reason)
            return self._extract_text(content) or "処理を完了しました。"

    # ---------------------------------------------------------------- #
    # LLM 呼び出し（エラーハンドリング付き）
    # ---------------------------------------------------------------- #

    def _call_llm_with_protection(self) -> Dict[str, Any]:
        """
        エラーハンドリング（リトライ + サーキットブレーカー）付きの LLM 呼び出し。
        """
        messages = self.memory.get_context_messages()
        tool_schema = self.tools.get_schema(self.config.allowed_tools)

        def primary_call() -> Dict[str, Any]:
            return self._call_llm(messages, tool_schema)

        return self.error_handler.execute_with_protection(
            primary_func=primary_call,
            provider_name=self.config.model_id,
        )

    def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        tool_schema: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        LLM を実際に呼び出す低レベルメソッド。

        BEDROCK_CLIENT_AVAILABLE が True の場合は boto3 の Converse API を使用します。
        False の場合はスタブレスポンスを返します（テスト・デモ用）。

        【本番実装例（AWS Bedrock）】
        import boto3
        client = boto3.client("bedrock-runtime", region_name="us-west-2")
        response = client.converse(
            modelId=self.config.model_id,
            system=[{"text": self.config.system_prompt}],
            messages=messages,
            toolConfig={"tools": tool_schema} if tool_schema else {},
        )
        return {
            "stop_reason": response["stopReason"],
            "content": response["output"]["message"]["content"],
        }
        """
        if BEDROCK_CLIENT_AVAILABLE:
            import boto3  # type: ignore[import]
            client = boto3.client("bedrock-runtime", region_name="us-west-2")
            response = client.converse(
                modelId=self.config.model_id,
                system=[{"text": self.config.system_prompt}],
                messages=messages,
                toolConfig={"tools": tool_schema} if tool_schema else {},
            )
            return {
                "stop_reason": response["stopReason"],
                "content": response["output"]["message"]["content"],
            }

        # --- スタブ実装（boto3 未使用時のデモ用） ---
        return self._stub_llm_response(messages, tool_schema)

    def _stub_llm_response(
        self,
        messages: List[Dict[str, Any]],
        tool_schema: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        boto3 が不要なスタブ LLM 応答（デモ・テスト用）。

        最後のユーザーメッセージを解析し、ツールが利用可能な場合は
        calculator ツールの呼び出しを模倣します。
        """
        if not messages:
            return {"stop_reason": "end_turn", "content": [{"text": "メッセージがありません。"}]}

        last_msg = messages[-1]
        last_text = ""
        if isinstance(last_msg.get("content"), list):
            for block in last_msg["content"]:
                if "text" in block:
                    last_text = block["text"]
                    break

        # ツール結果が含まれている場合（tool_use ループの2回目以降）
        if "[ツール実行結果]" in last_text:
            try:
                result_json = last_text.replace("[ツール実行結果]\n", "")
                results = json.loads(result_json)
                if results:
                    tool_result = results[0].get("result", "不明")
                    return {
                        "stop_reason": "end_turn",
                        "content": [{"text": f"計算結果は {tool_result} です。"}],
                    }
            except Exception:
                pass
            return {"stop_reason": "end_turn", "content": [{"text": "ツールの実行が完了しました。"}]}

        # 計算式が含まれている場合 → calculator ツールを呼び出すスタブ
        available_tool_names = [s["toolSpec"]["name"] for s in tool_schema]
        if "calculator" in available_tool_names and any(
            op in last_text for op in ["+", "-", "*", "/", "計算", "calc"]
        ):
            import re
            expr_match = re.search(r"[\d\s\+\-\*\/\(\)\.]+", last_text)
            expression = expr_match.group(0).strip() if expr_match else "1 + 1"
            return {
                "stop_reason": "tool_use",
                "content": [
                    {"text": f"数式を検出しました。calculator ツールを呼び出します。"},
                    {
                        "toolUse": {
                            "toolUseId": f"tool_use_{int(time.time())}",
                            "name": "calculator",
                            "input": {"expression": expression},
                        }
                    },
                ],
            }

        # デフォルト: そのままテキストで応答
        return {
            "stop_reason": "end_turn",
            "content": [{"text": f"入力「{last_text[:100]}」を処理しました。（スタブ応答）"}],
        }

    # ---------------------------------------------------------------- #
    # ツール実行
    # ---------------------------------------------------------------- #

    def _execute_tools(self, content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        LLM レスポンスに含まれるすべてのツール呼び出しを実行する。

        Returns:
            ツール実行結果のリスト（Converse API の toolResult 形式）
        """
        results = []
        for block in content:
            tool_use = block.get("toolUse")
            if not tool_use:
                continue

            tool_name = tool_use["name"]
            tool_input = tool_use.get("input", {})
            tool_use_id = tool_use["toolUseId"]
            self._tool_call_count += 1

            logger.info("ツール実行: name=%s input=%s", tool_name, tool_input)

            try:
                result = self.tools.call(tool_name, **tool_input)
                results.append({
                    "toolUseId": tool_use_id,
                    "tool": tool_name,
                    "result": result,
                    "status": "success",
                })
                logger.debug("ツール成功: name=%s result=%s", tool_name, str(result)[:100])
            except Exception as exc:
                # ツール失敗はエージェントに通知し、再試行・代替手段を検討させる
                error_msg = f"ツール '{tool_name}' の実行中にエラーが発生しました: {exc}"
                results.append({
                    "toolUseId": tool_use_id,
                    "tool": tool_name,
                    "result": error_msg,
                    "status": "error",
                })
                logger.warning("ツールエラー: name=%s error=%s", tool_name, exc)

        return results

    # ---------------------------------------------------------------- #
    # 状態管理
    # ---------------------------------------------------------------- #

    def _transition_state(self, new_state: AgentState, reason: str = "") -> None:
        """状態遷移を記録し、ログに出力する。"""
        record = AgentStateRecord(
            from_state=self._state,
            to_state=new_state,
            reason=reason,
            turn_number=self._turn_count,
        )
        self._state_history.append(record)
        logger.debug(
            "状態遷移: %s → %s [理由: %s]",
            self._state.value,
            new_state.value,
            reason,
        )
        self._state = new_state

    def _check_budget(self) -> bool:
        """
        バジェット（実行上限）を確認する。

        Returns:
            True の場合は継続可能、False の場合は中断すべき
        """
        budget = self.config.budget
        elapsed = time.time() - self._start_time

        if self._turn_count >= budget.max_turns:
            logger.warning("最大ターン数 (%d) に達しました", budget.max_turns)
            return False
        if self._tool_call_count >= budget.max_tool_calls:
            logger.warning("最大ツール呼び出し数 (%d) に達しました", budget.max_tool_calls)
            return False
        if elapsed >= budget.global_timeout_sec:
            logger.warning("グローバルタイムアウト (%.1f秒) に達しました", budget.global_timeout_sec)
            return False

        return True

    # ---------------------------------------------------------------- #
    # ユーティリティ
    # ---------------------------------------------------------------- #

    @staticmethod
    def _extract_text(content: List[Dict[str, Any]]) -> str:
        """コンテンツブロックのリストからテキストを抽出する。"""
        texts = [block["text"] for block in content if "text" in block]
        return " ".join(texts)

    def get_stats(self) -> Dict[str, Any]:
        """実行統計情報を返す（監視・デバッグ用）。"""
        return {
            "state": self._state.value,
            "turn_count": self._turn_count,
            "tool_call_count": self._tool_call_count,
            "elapsed_sec": round(time.time() - self._start_time, 2) if self._start_time else 0,
            "circuit_breakers": self.error_handler.get_all_circuit_status(),
            "memory_state": self.memory.dump_state(),
        }

    def get_state_history(self) -> List[Dict[str, Any]]:
        """状態遷移の履歴を返す（デバッグ用）。"""
        return [
            {
                "from": r.from_state.value,
                "to": r.to_state.value,
                "reason": r.reason,
                "turn": r.turn_number,
            }
            for r in self._state_history
        ]


# --------------------------------------------------------------------------- #
# 動作確認エントリポイント
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 60)
    print("  AIエージェントハーネス 動作確認")
    print("=" * 60)
    print()

    # --- ハーネスの設定 ---
    config = HarnessConfig(
        model_id="anthropic.claude-sonnet-4-5",
        system_prompt="あなたは計算・時刻取得などのツールを使えるアシスタントです。",
        budget=BudgetConfig(max_turns=10, max_tool_calls=20, global_timeout_sec=60.0),
        retry=RetryConfig(max_attempts=2, base_delay_sec=0.5),
        verbose=True,
    )

    harness = AgentHarness(config)

    # --- テストケース1: 計算タスク ---
    print("[テスト1] 計算タスク:")
    try:
        result = harness.run("2 + 3 * 4 を計算してください")
        print(f"  応答: {result}")
    except Exception as e:
        print(f"  エラー: {e}")

    # 統計情報の表示
    stats = harness.get_stats()
    print(f"\n  実行統計:")
    print(f"    ターン数: {stats['turn_count']}")
    print(f"    ツール呼び出し数: {stats['tool_call_count']}")
    print(f"    経過時間: {stats['elapsed_sec']}秒")

    # --- テストケース2: 現在時刻取得 ---
    print("\n[テスト2] 時刻取得タスク:")
    harness2 = AgentHarness(config)
    try:
        result2 = harness2.run("現在の時刻を教えてください")
        print(f"  応答: {result2}")
    except Exception as e:
        print(f"  エラー: {e}")

    # --- 状態遷移履歴の表示 ---
    print("\n[状態遷移履歴]:")
    for record in harness.get_state_history():
        print(f"  ターン{record['turn']:2d}: {record['from']:10s} → {record['to']:10s}  ({record['reason']})")

    # --- メモリ状態の表示 ---
    print("\n[メモリ状態]:")
    print(json.dumps(harness.memory.dump_state(), ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print("  動作確認完了")
    print("=" * 60)
