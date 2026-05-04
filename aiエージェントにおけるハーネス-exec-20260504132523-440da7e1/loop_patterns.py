# PoC品質: 本番利用前に認証・エラー処理・ビジネスロジックの追加が必要です

"""
エージェントループパターン集（Loop Patterns）
=============================================
用途や要件に応じて使い分けられる 3 つのループパターンを実装します。

■ 実装パターン
  1. ReActLoop         - 即時対話向け（シンプル・高速）
  2. PlanAndExecute    - 複雑な多段階タスク向け（コスト効率◎）
  3. ReflectionLoop    - 品質重視タスク向け（生成 → 批評 → 改善）

■ パターン選択の目安
  ┌────────────────────┬──────────┬──────────┬──────────────────┐
  │ 要件               │ ReAct    │Plan+Exec │ Reflection       │
  ├────────────────────┼──────────┼──────────┼──────────────────┤
  │ リアルタイム対話   │ ◎        │ △        │ ×                │
  │ 複雑な多段階処理   │ △        │ ◎        │ △                │
  │ 出力品質の最大化   │ △        │ △        │ ◎                │
  │ 実装の容易さ       │ ◎        │ ○        │ ○                │
  │ コスト効率         │ ◎        │ ○        │ △                │
  └────────────────────┴──────────┴──────────┴──────────────────┘
"""

import os
from dataclasses import dataclass, field
from typing import Any

import anthropic


# ---------------------------------------------------------------------------
# 共通: Anthropicクライアントの初期化
# ---------------------------------------------------------------------------
def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# 1. ReAct ループ（Reasoning + Acting）
#    考える → ツールを使う → 観察する を繰り返す最もシンプルなパターン
# ---------------------------------------------------------------------------
class ReActLoop:
    """
    ReAct パターンの独立実装。
    Yao et al. (2022) が提案したパターンで、
    「Thought（思考）→ Action（行動）→ Observation（観察）」を繰り返します。

    agent_harness.py の AgentHarness と同じ考え方ですが、
    ここでは思考トレースを明示的に表示するデモ用実装です。
    """

    SYSTEM_PROMPT = """あなたは ReAct パターンで動作するエージェントです。
各ステップで以下の形式で思考を示してください:

Thought: （次のアクションを決める前の思考過程）
Action: （実行するツール名と理由）
Observation: （ツールの結果から得た情報）

最終的な答えが出たら "Final Answer:" で始まる文章で回答してください。"""

    def __init__(self, tools: list[dict], tool_executor: Any) -> None:
        self.client = get_client()
        self.tools = tools
        self.tool_executor = tool_executor  # ToolRegistry.execute と互換なオブジェクト

    def run(self, task: str, max_steps: int = 10) -> str:
        """タスクを受け取り ReAct ループで解決して最終回答を返す。"""
        messages = [{"role": "user", "content": task}]
        step = 0

        while step < max_steps:
            step += 1
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=self.SYSTEM_PROMPT,
                tools=self.tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_called = False
            for block in response.content:
                if block.type == "text":
                    print(f"[ステップ {step}]\n{block.text}\n")
                    # "Final Answer:" が含まれたら終了
                    if "Final Answer:" in block.text:
                        return block.text
                elif block.type == "tool_use":
                    tool_called = True
                    result = str(self.tool_executor(block.name, block.input))
                    print(f"  → ツール '{block.name}': {result[:100]}")
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": block.id, "content": result}],
                    })

            if not tool_called and response.stop_reason == "end_turn":
                # テキストのみで終了した場合（ツール不要と判断）
                final_texts = [b.text for b in response.content if b.type == "text"]
                return "\n".join(final_texts)

        return f"[警告] max_steps ({max_steps}) に達しました。部分的な結果を返します。"


# ---------------------------------------------------------------------------
# 2. Plan-and-Execute パターン
#    まず全体計画を立て、次に各ステップを実行する 2 フェーズ構成
# ---------------------------------------------------------------------------
@dataclass
class ExecutionStep:
    """実行計画の1ステップを表すデータクラス。"""
    index: int
    description: str
    status: str = "pending"   # pending / running / done / failed
    result: str = ""


class PlanAndExecute:
    """
    Plan-and-Execute パターン実装。

    フェーズ1（Planning）: 大規模モデルがタスク全体の多段ステップ計画を生成
    フェーズ2（Execution）: 各ステップを順次実行（エグゼキューターは軽量モデルも可）

    利点: 実行フェーズに安いモデルを使えるのでコスト削減になります。
    注意: 計画が固定されるため、予期しない中間結果へのリプランが必要な場合があります。
    """

    PLANNER_SYSTEM = """あなたはタスク計画エキスパートです。
与えられたタスクを実行可能な具体的なステップに分解してください。
出力は必ず以下の JSON 形式で返してください:
{
  "steps": [
    "ステップ1の説明",
    "ステップ2の説明",
    ...
  ]
}"""

    EXECUTOR_SYSTEM = """あなたはタスク実行エージェントです。
指示されたステップを利用可能なツールを使って実行し、
結果を簡潔に日本語で報告してください。"""

    def __init__(self, tools: list[dict], tool_executor: Any) -> None:
        self.client = get_client()
        self.tools = tools
        self.tool_executor = tool_executor

    def _plan(self, task: str) -> list[ExecutionStep]:
        """タスクを分析してステップリストを生成する（プランニングフェーズ）。"""
        import json as json_module

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=self.PLANNER_SYSTEM,
            messages=[{"role": "user", "content": f"次のタスクを計画してください:\n{task}"}],
        )
        text = response.content[0].text
        try:
            # JSON 部分を抽出してパース
            start = text.find("{")
            end = text.rfind("}") + 1
            data = json_module.loads(text[start:end])
            return [ExecutionStep(i + 1, desc) for i, desc in enumerate(data["steps"])]
        except Exception:
            # パース失敗時は1ステップとして扱う
            return [ExecutionStep(1, task)]

    def _execute_step(self, step: ExecutionStep, context: str) -> str:
        """1 ステップを実行する（エグゼキューションフェーズ）。"""
        step.status = "running"
        messages = [{
            "role": "user",
            "content": f"前のステップの結果:\n{context}\n\n今のステップ:\n{step.description}",
        }]

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=self.EXECUTOR_SYSTEM,
            tools=self.tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        # ツール呼び出しがある場合は処理
        for block in response.content:
            if block.type == "tool_use":
                result = str(self.tool_executor(block.name, block.input))
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": block.id, "content": result}],
                })
                # ツール結果を踏まえて最終応答を取得
                final = self.client.messages.create(
                    model=MODEL,
                    max_tokens=1024,
                    system=self.EXECUTOR_SYSTEM,
                    tools=self.tools,
                    messages=messages,
                )
                step.status = "done"
                step.result = next((b.text for b in final.content if b.type == "text"), "")
                return step.result

        step.status = "done"
        step.result = next((b.text for b in response.content if b.type == "text"), "")
        return step.result

    def run(self, task: str) -> dict:
        """
        タスクを受け取り、計画 → 実行の 2 フェーズで処理して結果を返す。

        Returns:
            dict: {"plan": [steps], "results": [results], "final": "最終まとめ"}
        """
        print(f"\n[Plan-and-Execute] タスク: {task}")

        # フェーズ1: 計画
        steps = self._plan(task)
        print(f"\n[計画] {len(steps)} ステップで実行します:")
        for step in steps:
            print(f"  {step.index}. {step.description}")

        # フェーズ2: 実行
        context = ""
        for step in steps:
            print(f"\n[実行] ステップ {step.index}: {step.description}")
            context = self._execute_step(step, context)
            print(f"  結果: {step.result[:100]}...")

        return {
            "plan": [s.description for s in steps],
            "results": [s.result for s in steps],
            "final": context,
        }


# ---------------------------------------------------------------------------
# 3. Reflection ループ（自己批評によるイテレーティブ改善）
#    生成 → 批評 → 改善 を繰り返して出力品質を高める
# ---------------------------------------------------------------------------
class ReflectionLoop:
    """
    Reflection パターン実装（Andrew Ng の Agentic Design Patterns より）。

    「生成（Generate）→ 批評（Reflect）→ 改善（Refine）」を繰り返し、
    再学習なしで出力品質を段階的に向上させます。

    用途: コード生成、文書作成、複雑な推論タスクの品質向上
    注意: LLM を複数回呼び出すためコスト・レイテンシが増加します。
          max_iterations と quality_threshold で制御してください。
    """

    GENERATOR_SYSTEM = """あなたはタスクを実行する生成エージェントです。
与えられた要件に基づいて高品質な出力を生成してください。
前回の批評がある場合はそれを踏まえて改善してください。"""

    CRITIC_SYSTEM = """あなたは厳格な品質評価者です。
生成された出力を以下の観点で評価してください:
1. 正確性（要件を満たしているか）
2. 完全性（必要な要素が揃っているか）
3. 明確性（わかりやすいか）

評価は以下の JSON 形式で返してください:
{
  "score": 1〜10の整数,
  "issues": ["問題点1", "問題点2"],
  "suggestions": ["改善提案1", "改善提案2"]
}"""

    def __init__(self, quality_threshold: int = 8, max_iterations: int = 3) -> None:
        self.client = get_client()
        self.quality_threshold = quality_threshold  # この点数以上で終了
        self.max_iterations = max_iterations

    def _generate(self, task: str, critique: str = "") -> str:
        """タスクを実行して出力を生成する。"""
        user_content = task
        if critique:
            user_content += f"\n\n前回の批評:\n{critique}"

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=self.GENERATOR_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        return response.content[0].text

    def _reflect(self, task: str, output: str) -> tuple[int, str]:
        """
        出力を批評して品質スコアと改善提案を返す。
        Returns: (score, critique_text)
        """
        import json as json_module

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=self.CRITIC_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"元のタスク:\n{task}\n\n生成された出力:\n{output}\n\n評価してください。",
            }],
        )
        text = response.content[0].text
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            data = json_module.loads(text[start:end])
            score = data.get("score", 5)
            issues = "\n".join(data.get("issues", []))
            suggestions = "\n".join(data.get("suggestions", []))
            critique = f"問題点:\n{issues}\n\n改善提案:\n{suggestions}"
            return score, critique
        except Exception:
            return 5, text  # パース失敗時はテキストをそのまま批評として使う

    def run(self, task: str) -> dict:
        """
        タスクを受け取り、生成→批評→改善ループを回して最高品質の出力を返す。

        Returns:
            dict: {
                "output": "最終出力",
                "iterations": 実行回数,
                "final_score": 最終スコア,
                "history": [{"output": ..., "score": ...}]
            }
        """
        print(f"\n[Reflection Loop] タスク: {task[:80]}...")
        history = []
        critique = ""
        best_output = ""
        best_score = 0

        for i in range(self.max_iterations):
            print(f"\n[イテレーション {i+1}/{self.max_iterations}] 生成中...")

            # ステップ1: 生成
            output = self._generate(task, critique)
            print(f"  出力 ({len(output)} 文字)")

            # ステップ2: 批評
            score, critique = self._reflect(task, output)
            print(f"  品質スコア: {score}/10")

            history.append({"iteration": i + 1, "output": output, "score": score, "critique": critique})

            if score > best_score:
                best_score = score
                best_output = output

            # 収束判定: 閾値以上のスコアが出たらループ終了
            if score >= self.quality_threshold:
                print(f"  ✓ 品質閾値 ({self.quality_threshold}) 達成。ループ終了。")
                break
        else:
            print(f"  [警告] max_iterations ({self.max_iterations}) 到達。最良の出力を返します。")

        return {
            "output": best_output,
            "iterations": len(history),
            "final_score": best_score,
            "history": history,
        }


# ---------------------------------------------------------------------------
# デモ実行
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("エラー: ANTHROPIC_API_KEY を設定してください")
        raise SystemExit(1)

    from tools import default_registry

    tools = default_registry.to_anthropic_schema()

    def executor(name: str, tool_input: dict) -> str:
        return default_registry.execute(name, tool_input)

    # Reflection ループのデモ
    reflector = ReflectionLoop(quality_threshold=8, max_iterations=3)
    result = reflector.run(
        "Pythonで素数を判定する関数を書いてください。コメントと型ヒントを含めてください。"
    )
    print(f"\n最終スコア: {result['final_score']}/10")
    print(f"イテレーション数: {result['iterations']}")
    print(f"\n最終出力:\n{result['output']}")
