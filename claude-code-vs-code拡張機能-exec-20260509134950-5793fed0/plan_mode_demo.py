# PoC品質: このコードは動作確認用のスケルトンです。本番環境での使用には追加の検証が必要です。
#
# Plan Mode（プランモード）デモ実装
# Claude Code VS Code拡張の「Plan Mode」をPythonで再現したデモです。
#
# Plan Mode とは:
#   複数ファイルにわたる変更を実行する前に、Claudeが変更計画を提示し、
#   ユーザーが承認した後に初めてファイルへの書き込みを行うモードです。
#   「Shift+Tab」で切り替えられるVS Code拡張の主要機能の一つです。

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic


# ============================================================
# データモデル
# ============================================================

@dataclass
class FileChange:
    """ファイル変更を表すデータクラス"""
    path: str
    action: str          # "create" | "modify" | "delete"
    description: str     # 変更内容の説明
    content: str = ""    # 新しいファイル内容（createとmodifyの場合）
    diff_preview: str = ""  # 変更差分のプレビュー


@dataclass
class ExecutionPlan:
    """実行計画全体を表すデータクラス"""
    task_description: str
    steps: list[str] = field(default_factory=list)
    file_changes: list[FileChange] = field(default_factory=list)
    estimated_risk: str = "low"   # "low" | "medium" | "high"
    warnings: list[str] = field(default_factory=list)


# ============================================================
# Plan Mode実装
# ============================================================

class PlanModeAssistant:
    """
    Plan Mode アシスタント。

    Claude Code VS Code拡張のPlan Modeを模倣し、
    「計画作成 → ユーザー承認 → 実行」の安全なワークフローを実現する。

    メリット:
    - 破壊的な変更を実行前に確認できる
    - 複数ファイルの変更を俯瞰できる
    - 誤操作によるファイル破壊を防止できる

    デメリット:
    - 単純な変更でも承認ステップが必要になる
    - 計画生成に追加のAPIコストが発生する
    - 計画と実際の変更内容が完全に一致しない場合がある
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        """
        Args:
            api_key: AnthropicのAPIキー（Noneの場合は環境変数から取得）
        """
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.client = anthropic.Anthropic(api_key=key) if key else None
        self.model = "claude-sonnet-4-6"

    def create_plan(self, task: str, context_files: dict[str, str] | None = None) -> ExecutionPlan:
        """
        タスクの実行計画を作成する（実際のファイル変更は行わない）。

        Claude Code VS Code拡張では、Plan Modeで「何をどう変えるか」を
        Markdownドキュメントとして表示し、インラインコメントも追記できる。

        Args:
            task: 実行するタスクの説明
            context_files: コンテキストとして提供するファイル {パス: 内容}

        Returns:
            ExecutionPlan: 作成された実行計画
        """
        if not self.client:
            return self._mock_plan(task)

        # コンテキストファイルの整形
        context_section = ""
        if context_files:
            context_section = "\n\n**参照ファイル:**\n"
            for path, content in context_files.items():
                context_section += f"\n`{path}`:\n```\n{content[:500]}\n```\n"

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=(
                "あなたはClaude Code VS Code拡張のPlan Modeエージェントです。\n"
                "タスクの実行計画をJSON形式で返してください。\n"
                "ファイルの実際の変更は絶対に行わず、計画のみを作成してください。\n\n"
                "回答形式（JSON）:\n"
                "{\n"
                '  "steps": ["手順1", "手順2", ...],\n'
                '  "file_changes": [\n'
                '    {"path": "ファイルパス", "action": "create|modify|delete", '
                '"description": "変更説明", "diff_preview": "変更差分プレビュー"}\n'
                "  ],\n"
                '  "estimated_risk": "low|medium|high",\n'
                '  "warnings": ["注意点1", "注意点2"]\n'
                "}"
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"以下のタスクの実行計画を作成してください:\n\n{task}{context_section}",
                }
            ],
        )

        # JSON解析（実際の実装ではより堅牢なパース処理が必要）
        import json
        import re

        text = response.content[0].text
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return ExecutionPlan(
                    task_description=task,
                    steps=data.get("steps", []),
                    file_changes=[
                        FileChange(**fc) for fc in data.get("file_changes", [])
                    ],
                    estimated_risk=data.get("estimated_risk", "low"),
                    warnings=data.get("warnings", []),
                )
            except (json.JSONDecodeError, TypeError):
                pass

        # JSONパース失敗時のフォールバック
        return ExecutionPlan(
            task_description=task,
            steps=["計画の解析に失敗しました。タスクを詳細に記述して再試行してください。"],
        )

    def _mock_plan(self, task: str) -> ExecutionPlan:
        """APIキー未設定時のモック計画を返す"""
        return ExecutionPlan(
            task_description=task,
            steps=[
                "1. 対象ファイルの現状を確認する",
                "2. 変更箇所を特定する",
                "3. テストが壊れないことを確認する",
                "4. 変更を適用する",
                "5. テストを再実行して動作を確認する",
            ],
            file_changes=[
                FileChange(
                    path="src/example.py",
                    action="modify",
                    description="SQLインジェクション脆弱性をパラメータ化クエリで修正",
                    diff_preview="- f'SELECT * FROM users WHERE id = {user_id}'\n+ 'SELECT * FROM users WHERE id = ?', (user_id,)",
                ),
                FileChange(
                    path="tests/test_example.py",
                    action="create",
                    description="修正に対応するユニットテストを追加",
                    diff_preview="+ def test_get_user_prevents_sql_injection():\n+     ...",
                ),
            ],
            estimated_risk="medium",
            warnings=[
                "本番データベースのバックアップを事前に取得してください",
                "既存のSQLクエリキャッシュがある場合は無効化が必要です",
            ],
        )

    def display_plan(self, plan: ExecutionPlan) -> None:
        """
        実行計画をMarkdown形式で表示する。

        Claude Code VS Code拡張では、計画はMarkdownドキュメントとして
        エディタタブで開かれ、インラインコメントを追記できる。
        """
        risk_emoji = {"low": "🟢 低", "medium": "🟡 中", "high": "🔴 高"}.get(
            plan.estimated_risk, plan.estimated_risk
        )

        print("\n" + "=" * 60)
        print("【実行計画】Plan Mode")
        print("=" * 60)
        print(f"\n**タスク:** {plan.task_description}")
        print(f"**リスクレベル:** {risk_emoji}")

        if plan.warnings:
            print("\n**⚠️ 注意事項:**")
            for warning in plan.warnings:
                print(f"  - {warning}")

        print("\n**実行手順:**")
        for step in plan.steps:
            print(f"  {step}")

        print("\n**変更予定ファイル:**")
        for fc in plan.file_changes:
            action_label = {"create": "新規作成", "modify": "変更", "delete": "削除"}.get(
                fc.action, fc.action
            )
            print(f"\n  📄 `{fc.path}` [{action_label}]")
            print(f"     {fc.description}")
            if fc.diff_preview:
                print("     差分プレビュー:")
                for line in fc.diff_preview.split("\n"):
                    print(f"       {line}")

        print("\n" + "=" * 60)

    def request_approval(self, plan: ExecutionPlan) -> bool:
        """
        ユーザーに計画の承認を求める。

        Claude Code VS Code拡張では、計画のプレビュー後に
        「承認」または「拒否」の選択肢が表示される。

        Returns:
            bool: 承認された場合True、拒否された場合False
        """
        print("\n【承認確認】この計画を実行しますか？")
        print("  変更予定ファイル数:", len(plan.file_changes))
        print("  リスクレベル:", plan.estimated_risk)
        print("\n  [y] 承認して実行  [n] キャンセル  [e] 計画を編集")

        while True:
            choice = input("  選択 > ").strip().lower()
            if choice in ("y", "yes"):
                return True
            elif choice in ("n", "no"):
                print("  キャンセルされました。")
                return False
            elif choice == "e":
                print("  計画の編集機能はデモ版では未実装です。")
            else:
                print("  y / n / e を入力してください。")

    def execute_plan(self, plan: ExecutionPlan) -> None:
        """
        承認された計画を実行する（デモ版は実際の変更を行わない）。

        実際のClaude Code VS Code拡張では、承認後にファイルへの
        書き込みが行われ、インラインDiffビューで変更を確認できる。
        """
        print("\n【実行開始】")
        for i, fc in enumerate(plan.file_changes, 1):
            action_label = {"create": "新規作成", "modify": "変更", "delete": "削除"}.get(
                fc.action, fc.action
            )
            print(f"  [{i}/{len(plan.file_changes)}] {fc.path} を{action_label}中...")
            # デモ版: 実際のファイル変更は行わない
            print(f"    ✓ 完了（デモモードのため実際の変更はスキップ）")

        print("\n✅ 実行完了！")
        print("  ヒント: Claude Code VS Code拡張では、各変更後に")
        print("  「チェックポイント」が自動保存され、巻き戻しが可能です。")


# ============================================================
# メイン処理
# ============================================================

def main() -> None:
    """Plan Modeデモを実行する"""
    print("Plan Mode デモ - Claude Code VS Code拡張機能")
    print("=" * 60)
    print("「変更前に計画を確認」する安全な開発ワークフローを体験できます\n")

    assistant = PlanModeAssistant()

    # デモタスク
    task = "src/database.py のSQLクエリをパラメータ化クエリに変換し、対応するテストを追加してください"

    # コンテキストファイル（デモ用）
    context_files = {
        "src/database.py": """
def get_user(user_id):
    query = f"SELECT * FROM users WHERE id = {user_id}"  # 脆弱
    return db.execute(query)
""",
    }

    # Step 1: 計画作成
    print("Step 1: 実行計画を作成中...")
    plan = assistant.create_plan(task, context_files)

    # Step 2: 計画を表示
    assistant.display_plan(plan)

    # Step 3: 承認を求める（対話的UIが使用できない環境ではスキップ）
    if os.environ.get("PLAN_MODE_AUTO_APPROVE") == "1":
        print("\n（自動承認モード: PLAN_MODE_AUTO_APPROVE=1）")
        approved = True
    else:
        try:
            approved = assistant.request_approval(plan)
        except (EOFError, KeyboardInterrupt):
            print("\n（非対話的環境のため自動承認します）")
            approved = True

    # Step 4: 承認された場合のみ実行
    if approved:
        assistant.execute_plan(plan)
    else:
        print("\n操作をキャンセルしました。")


if __name__ == "__main__":
    main()
