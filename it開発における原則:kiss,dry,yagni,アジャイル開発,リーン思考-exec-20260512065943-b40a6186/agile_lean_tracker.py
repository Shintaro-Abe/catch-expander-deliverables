# PoC品質 - アジャイル・リーン思考 スプリント管理デモ
# このファイルは学習・説明目的のPoC（概念実証）コードです。本番利用には適しません。

"""
アジャイル開発とリーン思考のデモンストレーション
================================================
KISS・DRY・YAGNIの原則を活かしながら、実際のアジャイル開発の
プロセス（スプリント管理）をシンプルに実装したサンプルコードです。

用語解説:
- アジャイル開発: 短い反復サイクルで継続的に価値を届ける開発手法
- スプリント: アジャイルの1反復サイクル（通常1〜4週間）
- バックログ: 開発すべき機能・タスクの優先順位付きリスト
- リーン思考: トヨタ生産方式を起源とする「ムダの排除」を中心とした思考法
- ムダ(Waste): 価値を生まない活動や成果物（リーンで排除すべき対象）
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from datetime import date, timedelta


# =============================================================================
# ドメインモデル（KISS原則: 今必要なデータ構造のみ定義）
# =============================================================================

class Priority(Enum):
    """バックログアイテムの優先度"""
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class Status(Enum):
    """タスクの状態（かんばんボードの列に対応）"""
    TODO = "未着手"
    IN_PROGRESS = "進行中"
    DONE = "完了"


class WasteType(Enum):
    """
    リーン思考の7つのムダ（Poppendieck, 2003年）
    ソフトウェア開発における価値を生まない活動の分類
    """
    EXTRA_FEATURES = "余分な機能（YAGNI違反）"   # 最大のムダ
    RELEARNING = "再学習（DRY違反）"              # 重複知識による再理解コスト
    EXTRA_PROCESSES = "不要なプロセス（KISS違反）" # 過剰な手続きや承認フロー
    PARTIALLY_DONE = "未完成の作業"               # 完了しない中途半端な作業
    DEFECTS = "欠陥・バグ"                        # 不具合の発見・修正コスト
    WAITING = "待機"                              # レビュー待ち・承認待ちなど
    HANDOFF = "引き継ぎ"                          # 担当者間の知識移転コスト


@dataclass
class BacklogItem:
    """
    バックログアイテム（YAGNI適用: 今必要なフィールドのみ）

    設計上の判断:
    - story_points は現在のデモに必要なので含める
    - 担当者・スプリントIDなどは今は不要なので含めない
    """
    title: str
    description: str
    priority: Priority
    story_points: int          # 作業量の見積もり（1, 2, 3, 5, 8... フィボナッチ数列を使うことが多い）
    status: Status = Status.TODO
    is_yagni_violation: bool = False  # デモ用: YAGNI違反フラグ


@dataclass
class Sprint:
    """
    スプリント（アジャイルの1反復サイクル）
    DRY適用: スプリント情報を1か所に集約
    """
    sprint_number: int
    start_date: date
    end_date: date
    items: list = field(default_factory=list)

    @property
    def goal(self) -> str:
        """スプリントゴールはアイテムから導出（DRY: 別フィールドを持たない）"""
        high_priority = [i for i in self.items if i.priority == Priority.HIGH]
        if high_priority:
            return f"高優先度アイテム {len(high_priority)}件を完成させる"
        return "バックログアイテムを進める"

    @property
    def velocity(self) -> int:
        """ベロシティ = 完了したストーリーポイントの合計"""
        return sum(i.story_points for i in self.items if i.status == Status.DONE)

    @property
    def completion_rate(self) -> float:
        """完了率（%）"""
        if not self.items:
            return 0.0
        done = sum(1 for i in self.items if i.status == Status.DONE)
        return (done / len(self.items)) * 100


# =============================================================================
# バックログ管理（KISS・DRY・YAGNI の実践）
# =============================================================================

class ProductBacklog:
    """
    プロダクトバックログ（開発すべき機能の優先順位付きリスト）

    アジャイル原則10との対応:
    「シンプルさ──行わない作業量を最大化する技術──が本質」
    → YAGNI違反のアイテムを積極的に除外する
    """

    def __init__(self):
        self._items: list[BacklogItem] = []

    def add(self, item: BacklogItem) -> None:
        """DRY: アイテム追加の唯一の経路"""
        self._items.append(item)
        print(f"  + バックログ追加: [{item.priority.value}] {item.title} ({item.story_points}pt)")

    def remove_yagni_violations(self) -> list[BacklogItem]:
        """
        YAGNIバックログリファインメント:
        「今本当に必要か？」を問い、不要なアイテムを除外する。
        アジャイルの「行わない作業量を最大化する」原則の実践。
        """
        violations = [i for i in self._items if i.is_yagni_violation]
        self._items = [i for i in self._items if not i.is_yagni_violation]
        return violations

    def get_sprint_candidates(self, capacity: int) -> list[BacklogItem]:
        """
        スプリントキャパシティ内で高優先度アイテムを選択（KISS: シンプルなロジック）
        capacity: スプリントで消化できるストーリーポイントの上限
        """
        sorted_items = sorted(
            [i for i in self._items if i.status == Status.TODO],
            key=lambda x: (0 if x.priority == Priority.HIGH else
                           1 if x.priority == Priority.MEDIUM else 2)
        )
        selected = []
        remaining = capacity
        for item in sorted_items:
            if item.story_points <= remaining:
                selected.append(item)
                remaining -= item.story_points
        return selected

    @property
    def total_items(self) -> int:
        return len(self._items)

    @property
    def items(self) -> list[BacklogItem]:
        return list(self._items)


# =============================================================================
# ムダの検出器（リーン思考の実践）
# =============================================================================

class WasteDetector:
    """
    リーン思考: コードベースやバックログのムダを検出する

    Poppendieck (2003) のリーン7原則:
    ①ムダを排除する  ②学習を増幅する  ③できるだけ遅く決める
    ④できるだけ速く届ける  ⑤チームに権限を与える
    ⑥品質を組み込む  ⑦全体を最適化する
    """

    @staticmethod
    def detect_waste_in_backlog(backlog: ProductBacklog) -> dict:
        """バックログ内のムダを分析する（リーン: ①ムダを排除する）"""
        items = backlog.items
        total = len(items)
        if total == 0:
            return {"total": 0, "waste_ratio": 0.0, "findings": []}

        yagni_count = sum(1 for i in items if i.is_yagni_violation)
        low_priority_count = sum(1 for i in items if i.priority == Priority.LOW)
        waste_points = sum(i.story_points for i in items if i.is_yagni_violation)

        findings = []
        if yagni_count > 0:
            findings.append({
                "type": WasteType.EXTRA_FEATURES.value,
                "count": yagni_count,
                "wasted_points": waste_points,
                "recommendation": "YAGNI原則を適用し、現スプリントに不要なアイテムを除外する"
            })
        if low_priority_count > total * 0.4:
            findings.append({
                "type": WasteType.PARTIALLY_DONE.value,
                "count": low_priority_count,
                "recommendation": "低優先度アイテムが多すぎます。バックログを絞り込んでください（リーン③: できるだけ遅く決める）"
            })

        return {
            "total": total,
            "yagni_violations": yagni_count,
            "waste_ratio": (yagni_count / total) * 100 if total > 0 else 0.0,
            "wasted_story_points": waste_points,
            "findings": findings
        }

    @staticmethod
    def analyze_sprint_retrospective(sprint: Sprint) -> dict:
        """スプリントのふりかえり分析（リーン②: 学習を増幅する）"""
        return {
            "sprint": sprint.sprint_number,
            "velocity": sprint.velocity,
            "completion_rate": f"{sprint.completion_rate:.1f}%",
            "period": f"{sprint.start_date} 〜 {sprint.end_date}",
            "assessment": (
                "健全なスプリント: 高い完了率を維持" if sprint.completion_rate >= 80
                else "要改善: スコープの見直しかキャパシティの再評価が必要"
            )
        }


# =============================================================================
# デモ実行
# =============================================================================

def run_agile_lean_demo():
    print("=" * 60)
    print("アジャイル・リーン思考 デモ")
    print("シナリオ: ECサイトのMVP（最小限の製品）開発")
    print("=" * 60)

    # --- Step 1: バックログの構築 ---
    print("\n【Step 1】プロダクトバックログの構築")
    print("-" * 40)

    backlog = ProductBacklog()

    # 本当に必要なアイテム（MVP要件）
    backlog.add(BacklogItem(
        title="商品一覧ページ",
        description="ユーザーが商品を閲覧できる",
        priority=Priority.HIGH,
        story_points=5
    ))
    backlog.add(BacklogItem(
        title="ショッピングカート機能",
        description="商品をカートに追加・削除できる",
        priority=Priority.HIGH,
        story_points=8
    ))
    backlog.add(BacklogItem(
        title="決済フロー（クレジットカード）",
        description="クレジットカードで購入を完了できる",
        priority=Priority.HIGH,
        story_points=8
    ))
    backlog.add(BacklogItem(
        title="ユーザー登録・ログイン",
        description="メールアドレスとパスワードで登録・ログインできる",
        priority=Priority.HIGH,
        story_points=5
    ))

    # YAGNI違反アイテム（今は不要）
    backlog.add(BacklogItem(
        title="AIレコメンデーションエンジン",
        description="機械学習による商品推薦（MVPには不要）",
        priority=Priority.LOW,
        story_points=21,
        is_yagni_violation=True  # YAGNI違反: MVPに不要
    ))
    backlog.add(BacklogItem(
        title="マルチ通貨対応（20通貨）",
        description="国際展開のための通貨変換（現在は日本国内のみ）",
        priority=Priority.LOW,
        story_points=13,
        is_yagni_violation=True  # YAGNI違反: 現時点で国内展開のみ
    ))
    backlog.add(BacklogItem(
        title="SNSシェア機能（全15プラットフォーム）",
        description="各SNSへのシェアボタン（MVPには不要）",
        priority=Priority.LOW,
        story_points=8,
        is_yagni_violation=True  # YAGNI違反: まず製品が使われるかを確認すべき
    ))

    # --- Step 2: ムダの検出（リーン思考） ---
    print("\n【Step 2】リーン思考: バックログのムダ分析")
    print("-" * 40)
    waste_report = WasteDetector.detect_waste_in_backlog(backlog)
    print(f"  バックログアイテム総数:        {waste_report['total']}件")
    print(f"  YAGNI違反（ムダ）アイテム数:   {waste_report['yagni_violations']}件")
    print(f"  ムダの割合:                    {waste_report['waste_ratio']:.1f}%")
    print(f"  ムダなストーリーポイント:       {waste_report['wasted_story_points']}pt")

    for finding in waste_report["findings"]:
        print(f"\n  [ムダ検出] {finding['type']}")
        print(f"  推奨アクション: {finding['recommendation']}")

    # --- Step 3: YAGNIリファインメント ---
    print("\n【Step 3】YAGNIリファインメント: 不要アイテムの除外")
    print("-" * 40)
    removed = backlog.remove_yagni_violations()
    print(f"  除外したアイテム:")
    for item in removed:
        print(f"    - {item.title} ({item.story_points}pt) ← {WasteType.EXTRA_FEATURES.value}")
    print(f"\n  リファインメント後のバックログ: {backlog.total_items}件")
    print("  → アジャイル原則10: 「行わない作業量を最大化する」の実践")

    # --- Step 4: スプリント計画 ---
    print("\n【Step 4】スプリント1 計画（YAGNI・KISSを考慮）")
    print("-" * 40)
    SPRINT_CAPACITY = 20  # このスプリントで消化できるストーリーポイント

    today = date.today()
    sprint1 = Sprint(
        sprint_number=1,
        start_date=today,
        end_date=today + timedelta(weeks=2)
    )

    candidates = backlog.get_sprint_candidates(capacity=SPRINT_CAPACITY)
    sprint1.items = candidates

    print(f"  スプリント期間: {sprint1.start_date} 〜 {sprint1.end_date}")
    print(f"  スプリントゴール: {sprint1.goal}")
    print(f"  キャパシティ: {SPRINT_CAPACITY}pt")
    print(f"\n  選択されたアイテム:")
    total_pts = 0
    for item in sprint1.items:
        print(f"    [{item.priority.value}] {item.title} ({item.story_points}pt)")
        total_pts += item.story_points
    print(f"\n  合計ストーリーポイント: {total_pts}pt / {SPRINT_CAPACITY}pt")

    # --- Step 5: スプリント実行シミュレーション ---
    print("\n【Step 5】スプリント実行シミュレーション")
    print("-" * 40)
    # 商品一覧とユーザー登録を完了させる
    for item in sprint1.items:
        if item.title in ["商品一覧ページ", "ユーザー登録・ログイン"]:
            item.status = Status.DONE
            print(f"  ✓ 完了: {item.title}")
        elif item.title == "ショッピングカート機能":
            item.status = Status.IN_PROGRESS
            print(f"  → 進行中: {item.title}")
        else:
            print(f"  ○ 未着手: {item.title}")

    # --- Step 6: スプリントレトロスペクティブ（ふりかえり）---
    print("\n【Step 6】スプリントレトロスペクティブ（リーン②: 学習を増幅する）")
    print("-" * 40)
    retro = WasteDetector.analyze_sprint_retrospective(sprint1)
    print(f"  スプリント番号:    {retro['sprint']}")
    print(f"  期間:              {retro['period']}")
    print(f"  ベロシティ:        {retro['velocity']}pt")
    print(f"  完了率:            {retro['completion_rate']}")
    print(f"  評価:              {retro['assessment']}")

    # --- まとめ ---
    print("\n" + "=" * 60)
    print("【まとめ】アジャイル・リーンと3原則の連携")
    print("=" * 60)
    print("""
  原則              | アジャイル原則との対応          | リーン原則との対応
  ──────────────────|─────────────────────────────────|──────────────────────────
  KISS              | 原則9: 技術的卓越性への配慮     | ②学習増幅・⑥品質組込
  DRY               | 原則9: 良い設計への継続的配慮   | ①ムダ排除・⑦全体最適化
  YAGNI             | 原則10: 行わない作業を最大化 ★  | ①ムダ排除・③遅延決定

  ★ アジャイル宣言原則10は「行わない作業量を最大化する技術」という表現で
    YAGNIの思想を直接内包している

  リーン7つのムダとの対応:
  - 余分な機能 (Extra Features)    → YAGNI で防ぐ
  - 再学習 (Relearning)            → DRY で防ぐ（知識の重複を排除）
  - 不要プロセス (Extra Processes)  → KISS で防ぐ
  - 欠陥 (Defects)                 → 3原則すべてで予防

  重要: 3原則は「継続的リファクタリング・自動テスト・CI/CD」と
        セットで初めて健全に機能する（Martin Fowler）
    """)


if __name__ == "__main__":
    run_agile_lean_demo()
