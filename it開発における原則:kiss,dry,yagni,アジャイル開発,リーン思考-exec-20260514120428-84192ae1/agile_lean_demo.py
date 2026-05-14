# PoC品質: このファイルは学習・デモ用のスケルトンコードです。本番環境での使用は想定していません。
"""
アジャイル開発・リーン思考 デモ
=====================================
スプリント管理とカンバンボードを模したシミュレーションで
アジャイル・リーン思想の核心を体験します。

【用語解説】
- スプリント     : アジャイル開発の短い開発サイクル（通常1〜4週間）
- バックログ     : 実装すべきタスクの優先順位つきリスト
- カンバン       : タスクの状態を可視化するボード（リーン由来）
- ウェイスト     : リーン思想における「無駄」— 価値を生まない作業
- WIP（仕掛品）  : Work In Progress — 着手済みで完了していない作業
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import datetime


# ============================================================
# 基本データ構造
# ============================================================

class Priority(Enum):
    """優先度（MoSCoW法: Must/Should/Could/Won't）"""
    MUST = 1    # 必須（今スプリントで完了しなければリリース不可）
    SHOULD = 2  # 重要（できれば今スプリントで）
    COULD = 3   # あれば良い（余力があれば）
    WONT = 4    # 今回はやらない（= YAGNIの実践）


class TaskStatus(Enum):
    """カンバンボードの列（状態）"""
    BACKLOG = "バックログ"
    TODO = "TODO"
    IN_PROGRESS = "進行中"
    REVIEW = "レビュー"
    DONE = "完了"


@dataclass
class UserStory:
    """
    ユーザーストーリー — アジャイルの作業単位
    【用語解説】ユーザーストーリー = 「〜として、〜したい、なぜなら〜」形式の要件記述
    """
    id: str
    title: str
    description: str
    story_points: int          # 作業量の相対的な見積もり値
    priority: Priority
    status: TaskStatus = TaskStatus.BACKLOG
    created_at: datetime.datetime = field(default_factory=datetime.datetime.now)
    completed_at: Optional[datetime.datetime] = None

    # YAGNI: 今必要な属性だけを持つ
    # （アサイン・サブタスク・繰り返し などは要件になったときに追加）

    def start(self) -> None:
        """着手（バックログ → 進行中）"""
        self.status = TaskStatus.IN_PROGRESS
        print(f"  [開始] {self.title}")

    def move_to_review(self) -> None:
        """レビュー依頼"""
        self.status = TaskStatus.REVIEW
        print(f"  [レビュー] {self.title}")

    def complete(self) -> None:
        """完了"""
        self.status = TaskStatus.DONE
        self.completed_at = datetime.datetime.now()
        print(f"  [完了] {self.title} ({self.story_points}pt)")

    @property
    def is_done(self) -> bool:
        return self.status == TaskStatus.DONE

    def __repr__(self) -> str:
        return (
            f"Story({self.id}, '{self.title}', "
            f"Priority.{self.priority.name}, {self.status.value})"
        )


# ============================================================
# スプリント（アジャイルの核心）
# ============================================================
# 【アジャイルマニフェスト 第10原則】
#   「シンプルさ――行う作業量を最大限に削減する技法――が本質的である。」
#
# 【スプリントがYAGNIを強制する仕組み】
#   スプリントには容量（velocity）の上限がある。
#   上限内で最も価値の高いストーリーだけを選ぶため、
#   自然と「今必要なもの」だけが実装される。

class Sprint:
    """
    スプリント — タイムボックス型の開発サイクル
    【用語解説】タイムボックス = 時間を固定して、その中でできる最善を尽くす手法
    """

    def __init__(self, sprint_number: int, velocity: int, duration_days: int = 14):
        """
        Parameters
        ----------
        sprint_number : スプリント番号
        velocity      : このスプリントで消化できるストーリーポイントの上限
        duration_days : スプリントの期間（日数）
        """
        self.sprint_number = sprint_number
        self.velocity = velocity
        self.duration_days = duration_days
        self.stories: list[UserStory] = []
        self.start_date = datetime.datetime.now()
        self.end_date = self.start_date + datetime.timedelta(days=duration_days)

    @property
    def committed_points(self) -> int:
        """コミット済みストーリーポイントの合計"""
        return sum(s.story_points for s in self.stories)

    @property
    def completed_points(self) -> int:
        """完了したストーリーポイントの合計"""
        return sum(s.story_points for s in self.stories if s.is_done)

    @property
    def remaining_capacity(self) -> int:
        """残り受け入れ可能ポイント"""
        return self.velocity - self.committed_points

    def can_add(self, story: UserStory) -> bool:
        """このスプリントにストーリーを追加できるか"""
        return story.story_points <= self.remaining_capacity

    def add_story(self, story: UserStory) -> bool:
        """ストーリーをスプリントバックログに追加"""
        if not self.can_add(story):
            print(f"  ⚠️  容量不足: '{story.title}' ({story.story_points}pt) "
                  f"は次スプリントへ")
            return False
        story.status = TaskStatus.TODO
        self.stories.append(story)
        print(f"  ✅ 追加: '{story.title}' ({story.story_points}pt)")
        return True

    def run(self) -> dict:
        """
        スプリントの実行シミュレーション
        （実際の開発では日々のスタンドアップ、ペアプロ、コードレビューが入る）
        """
        print(f"\n{'='*50}")
        print(f"🏃 スプリント {self.sprint_number} 開始")
        print(f"   容量: {self.velocity}pt | コミット: {self.committed_points}pt")
        print(f"{'='*50}")

        # Must → Should → Could の順で処理
        sorted_stories = sorted(self.stories, key=lambda s: s.priority.value)

        for story in sorted_stories:
            story.start()
            story.move_to_review()
            story.complete()

        return self.sprint_review()

    def sprint_review(self) -> dict:
        """
        スプリントレビュー — 成果の振り返り
        【用語解説】スプリントレビュー = スプリント終了時に動くソフトウェアをデモする会
        """
        velocity_achievement = (
            self.completed_points / self.velocity * 100
            if self.velocity > 0 else 0
        )

        result = {
            "sprint": self.sprint_number,
            "committed": self.committed_points,
            "completed": self.completed_points,
            "velocity_achievement": f"{velocity_achievement:.1f}%",
            "stories_done": [s.title for s in self.stories if s.is_done],
        }

        print(f"\n📊 スプリント {self.sprint_number} レビュー")
        print(f"   コミット: {result['committed']}pt")
        print(f"   完了    : {result['completed']}pt")
        print(f"   達成率  : {result['velocity_achievement']}")

        return result


# ============================================================
# プロダクトバックログ（リーン思想: Pull システム）
# ============================================================
# 【リーン「Just-in-Time（JIT）」との対応】
#   トヨタ生産方式の「必要なものを必要なときに必要なだけ」に対応。
#   チームが自分たちのキャパシティに合わせてバックログから作業を「引き取る」
#   プル型システム = YAGNI の実践そのもの。

class ProductBacklog:
    """
    プロダクトバックログ — 全ストーリーの優先順位つきリスト
    リーンの「プルシステム」を体現: スプリントが能動的に作業を引き取る
    """

    def __init__(self):
        self._stories: list[UserStory] = []

    def add(self, story: UserStory) -> None:
        """バックログにストーリーを追加"""
        self._stories.append(story)

    @property
    def prioritized(self) -> list[UserStory]:
        """優先度順（Must優先）でソートされたストーリー一覧"""
        return sorted(
            [s for s in self._stories if s.status == TaskStatus.BACKLOG],
            key=lambda s: (s.priority.value, -s.story_points),
        )

    def pull_for_sprint(self, sprint: Sprint) -> None:
        """
        スプリントの容量内でバックログからストーリーを引き取る（Pull）
        YAGNIの実践: 容量を超えた分は次スプリントへ
        """
        print(f"\n📋 スプリント {sprint.sprint_number} のプランニング")
        print(f"   スプリント容量: {sprint.velocity}pt")

        for story in self.prioritized:
            if sprint.remaining_capacity <= 0:
                print(f"  ⛔ 容量上限 — 残りは次スプリントへ")
                break
            sprint.add_story(story)

    def show_wont_items(self) -> None:
        """
        WONT（今回やらない）アイテムを表示
        YAGNI: 明示的に「やらないと決める」ことも重要な意思決定
        """
        wont_items = [s for s in self._stories if s.priority == Priority.WONT]
        if wont_items:
            print("\n🚫 今回スコープ外（YAGNI: 今は不要）")
            for item in wont_items:
                print(f"   - {item.title}")


# ============================================================
# リーン思想: 7つのムダの可視化
# ============================================================
# 【リーン7原則（ポッペンダイク）と設計原則の対応】
#
# | リーン原則              | 対応する設計原則 |
# |------------------------|----------------|
# | ①廃棄物の排除           | YAGNI・DRY・KISS |
# | ②学習の増幅             | DRY（知識の一元化）|
# | ③できる限り遅く決断する  | YAGNI           |
# | ④できる限り早く提供する  | YAGNI・KISS     |
# | ⑤チームへの権限委譲      | （3原則の副産物）|
# | ⑥品質を組み込む         | DRY・KISS      |
# | ⑦全体を最適化する       | DRY            |

class WasteType(Enum):
    """
    リーンソフトウェア開発における7つのムダ
    （製造業のムダをソフトウェア開発に翻訳したもの）
    """
    EXTRA_FEATURES = "余分な機能（YAGNI違反）"         # 過剰生産ムダ
    PARTIALLY_DONE = "仕掛かり（WIP過多）"              # 仕掛品ムダ
    TASK_SWITCHING = "コンテキストスイッチ"             # 動作ムダ
    HANDOFFS = "引き継ぎ・待ち時間"                     # 運搬ムダ
    DEFECTS = "欠陥・バグ"                             # 不良ムダ
    EXTRA_PROCESSING = "不必要な複雑性（KISS違反）"     # 加工ムダ
    CODE_DUPLICATION = "コード重複（DRY違反）"          # 在庫ムダ


@dataclass
class WasteRecord:
    """ムダの記録"""
    waste_type: WasteType
    description: str
    estimated_hours: float
    principle_violated: str


class WasteTracker:
    """
    チームのムダを追跡し、改善サイクルに活用するトラッカー
    【用語解説】レトロスペクティブ = スプリント終了後の振り返り会
    """

    def __init__(self):
        self._records: list[WasteRecord] = []

    def record(
        self,
        waste_type: WasteType,
        description: str,
        hours: float,
        principle: str,
    ) -> None:
        record = WasteRecord(waste_type, description, hours, principle)
        self._records.append(record)

    def retrospective_report(self) -> None:
        """
        レトロスペクティブ用レポート
        改善ポイントを原則違反と紐づけて可視化
        """
        print("\n" + "="*50)
        print("🔍 レトロスペクティブ: ムダの分析")
        print("="*50)

        total_waste_hours = sum(r.estimated_hours for r in self._records)
        print(f"\n総ムダ時間（推定）: {total_waste_hours:.1f}時間")

        # ムダのタイプ別集計
        by_type: dict[WasteType, float] = {}
        for r in self._records:
            by_type[r.waste_type] = by_type.get(r.waste_type, 0) + r.estimated_hours

        print("\n【ムダのタイプ別内訳】")
        for waste_type, hours in sorted(by_type.items(), key=lambda x: -x[1]):
            bar = "█" * int(hours * 2)
            print(f"  {waste_type.value:<30} {hours:5.1f}h {bar}")

        print("\n【改善アクション（次スプリントへ）】")
        principles_violated = set(r.principle_violated for r in self._records)
        action_map = {
            "YAGNI": "スプリントプランニングで'今必要か？'チェックを追加",
            "DRY": "コードレビューチェックリストに重複確認項目を追加",
            "KISS": "複雑度の高いPRには設計説明コメントを必須化",
        }
        for principle in sorted(principles_violated):
            action = action_map.get(principle, "原則の勉強会を開催")
            print(f"  [{principle}] {action}")


# ============================================================
# メイン実行: アジャイル開発サイクルのシミュレーション
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("アジャイル開発・リーン思考 シミュレーション")
    print("=" * 50)

    # --- プロダクトバックログの構築 ---
    backlog = ProductBacklog()

    stories = [
        UserStory("US-001", "ユーザー登録機能",
                  "メールアドレスとパスワードで登録できる",
                  story_points=5, priority=Priority.MUST),
        UserStory("US-002", "ログイン機能",
                  "登録済みユーザーがログインできる",
                  story_points=3, priority=Priority.MUST),
        UserStory("US-003", "パスワードリセット",
                  "メールでパスワードをリセットできる",
                  story_points=3, priority=Priority.SHOULD),
        UserStory("US-004", "プロフィール編集",
                  "ユーザーが自分の情報を更新できる",
                  story_points=2, priority=Priority.COULD),
        UserStory("US-005", "SNS連携ログイン",
                  "Google/GitHubアカウントでログインできる",
                  story_points=8, priority=Priority.WONT),  # YAGNI: 今は不要
        UserStory("US-006", "多言語対応",
                  "英語・日本語の切り替えができる",
                  story_points=13, priority=Priority.WONT), # YAGNI: 今は不要
    ]

    for story in stories:
        backlog.add(story)

    # YAGNIの実践: 今回スコープ外を明示
    backlog.show_wont_items()

    # --- スプリント 1（ベロシティ: 10pt） ---
    sprint1 = Sprint(sprint_number=1, velocity=10)
    backlog.pull_for_sprint(sprint1)
    sprint1_result = sprint1.run()

    # --- スプリント 2（ベロシティ: 8pt） ---
    sprint2 = Sprint(sprint_number=2, velocity=8)
    backlog.pull_for_sprint(sprint2)
    sprint2_result = sprint2.run()

    # --- ムダのトラッキング（レトロスペクティブ用） ---
    print("\n\n--- ムダの記録（スプリント中に発見） ---")
    tracker = WasteTracker()

    tracker.record(
        WasteType.EXTRA_FEATURES,
        "SNS連携の前調査を誰かが始めていた（要件外）",
        hours=4.0,
        principle="YAGNI",
    )
    tracker.record(
        WasteType.CODE_DUPLICATION,
        "バリデーションロジックが3箇所に重複",
        hours=2.5,
        principle="DRY",
    )
    tracker.record(
        WasteType.EXTRA_PROCESSING,
        "単純なCRUDに5層アーキテクチャを適用",
        hours=6.0,
        principle="KISS",
    )
    tracker.record(
        WasteType.DEFECTS,
        "重複バリデーションの不整合によるバグ修正",
        hours=3.0,
        principle="DRY",
    )

    tracker.retrospective_report()

    print("\n✅ シミュレーション完了")
    print("\n【学習ポイント】")
    print("  - スプリントの容量制限が自然にYAGNIを強制する")
    print("  - リーンのムダ追跡がDRY・KISS・YAGNI違反を可視化する")
    print("  - レトロスペクティブで原則違反を改善サイクルに乗せる")
