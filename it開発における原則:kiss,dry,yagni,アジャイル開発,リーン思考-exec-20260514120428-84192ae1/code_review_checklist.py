# PoC品質: このファイルは学習・デモ用のスケルトンコードです。本番環境での使用は想定していません。
"""
コードレビュー チェックリストツール
=====================================
KISS・DRY・YAGNIの観点からコードレビューをサポートするツール。
チームへの原則導入ステップとペアプログラミング推奨事項も含む。

【このツールの使い方】
  1. ReviewChecklist を使ってチームのレビュー観点を標準化する
  2. PairProgrammingSession でペアプロのガイドラインを確認する
  3. TeamOnboarding でチームへの原則導入ステップを把握する
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable
import datetime


# ============================================================
# コードレビュー チェックリスト
# ============================================================
# 【チーム導入のステップ（研究調査結果より）】
#
# Step 1: 教育・認識共有（全員が「なぜ」を理解する）
# Step 2: 小さな実験からスタート（1チーム・1スプリント）
# Step 3: 既存プロセスへの組み込み（スプリントプランニング・レトロ）
# Step 4: 共有標準の確立（コーディング規約・オンボーディング）

class ReviewSeverity(Enum):
    """レビュー指摘の重大度"""
    BLOCKER = "🔴 ブロッカー"    # マージ前に必ず修正
    MAJOR = "🟠 メジャー"        # 強く推奨する修正
    MINOR = "🟡 マイナー"        # あれば良い改善
    INFO = "🔵 情報"             # 参考情報・提案


class Principle(Enum):
    """設計原則"""
    DRY = "DRY（繰り返すな）"
    KISS = "KISS（シンプルに保て）"
    YAGNI = "YAGNI（今必要でないものは作るな）"
    GENERAL = "一般"


@dataclass
class ReviewItem:
    """レビュー指摘事項"""
    principle: Principle
    severity: ReviewSeverity
    question: str           # レビュアーが自問する問い
    description: str        # 何を確認するか
    good_example: str       # 良い例（ヒント）
    bad_example: str        # 悪い例（アンチパターン）

    def display(self) -> None:
        print(f"\n  {self.severity.value} [{self.principle.value}]")
        print(f"  問い  : {self.question}")
        print(f"  確認  : {self.description}")
        print(f"  ❌ 悪例: {self.bad_example}")
        print(f"  ✅ 良例: {self.good_example}")


# デフォルトのレビューチェックリスト（チームで共有・カスタマイズ可能）
DEFAULT_REVIEW_ITEMS: list[ReviewItem] = [

    # --- DRY チェック ---
    ReviewItem(
        principle=Principle.DRY,
        severity=ReviewSeverity.MAJOR,
        question="同じロジック・知識が複数箇所に重複していないか？",
        description=(
            "同一のビジネスルール、バリデーション、計算式が"
            "2箇所以上に存在する場合は共通化を検討する。"
            "ただし「コードが似ている」≠「知識が同じ」に注意。"
        ),
        bad_example=(
            "3つの関数それぞれに"
            "`if age < 0 or age > 120: raise ValueError` が存在"
        ),
        good_example=(
            "`def validate_age(age)` として一元化し、"
            "各関数から呼び出す"
        ),
    ),
    ReviewItem(
        principle=Principle.DRY,
        severity=ReviewSeverity.MINOR,
        question="デコレータ・コンテキストマネージャで横断的関心事を共通化できないか？",
        description=(
            "ログ、認証チェック、DB接続など複数箇所に現れる"
            "ボイラープレートコードは共通化の候補。"
        ),
        bad_example="各関数の冒頭に `logger.info(f'Calling {name}')` を手動で記述",
        good_example="@log_callデコレータを定義して各関数に適用",
    ),
    ReviewItem(
        principle=Principle.DRY,
        severity=ReviewSeverity.BLOCKER,
        question="誤った抽象化（Wrong Abstraction）が生まれていないか？",
        description=(
            "DRY目的で共通化した結果、無関係なモジュールが結合していないか確認。"
            "Sandi Metz: '複製は誤った抽象化よりはるかに安い'"
        ),
        bad_example=(
            "メール送信とSMS送信を同一の`Notifier`クラスに統合 "
            "→ 要件の乖離とともに条件分岐が肥大化"
        ),
        good_example=(
            "3回以上重複した時点で抽象化を検討（Rule of Three）。"
            "それまでは重複を許容する"
        ),
    ),

    # --- KISS チェック ---
    ReviewItem(
        principle=Principle.KISS,
        severity=ReviewSeverity.MAJOR,
        question="第三者が5分以内に理解できるコードか？",
        description=(
            "命名、構造、制御フローを見て、"
            "チームの新規メンバーが即座に意図を把握できるか確認。"
        ),
        bad_example=(
            "`return ((a**2)*(b+5))-((a+b)/2) if a>10 else "
            "((a*b)-(b/2)+a**b)` の1行"
        ),
        good_example="条件分岐・計算ステップを複数行に分けて命名で意図を表現",
    ),
    ReviewItem(
        principle=Principle.KISS,
        severity=ReviewSeverity.MAJOR,
        question="深い継承階層・過剰な抽象化が生まれていないか？",
        description=(
            "クラス継承は2〜3層まで。"
            "それ以上はコンポジション（組み合わせ）を検討する。"
            "クラスよりシンプルな関数で表現できないか問う。"
        ),
        bad_example="BaseNotifier → AbstractEmailSender → GmailSenderという3層継承",
        good_example="send_email(provider='gmail', ...)のシンプルな関数",
    ),
    ReviewItem(
        principle=Principle.KISS,
        severity=ReviewSeverity.MINOR,
        question="不要なelse、冗長な条件式がないか？",
        description="ブール式を直接返せる場合にif/elseで包んでいないか。",
        bad_example="`if x > 0: return True else: return False`",
        good_example="`return x > 0`",
    ),

    # --- YAGNI チェック ---
    ReviewItem(
        principle=Principle.YAGNI,
        severity=ReviewSeverity.BLOCKER,
        question="このコード・フィールド・メソッドは現在のスプリントの要件に含まれるか？",
        description=(
            "「将来必要になりそう」という推測だけで実装されたコードがないか確認。"
            "YAGNIのコスト: 構築・遅延・維持・修正の4コストが発生する。"
        ),
        bad_example=(
            "Taskクラスに priority/tags/subtasks/recurring を先行実装 "
            "（現在の要件: 作成と完了管理のみ）"
        ),
        good_example="現在必要な title/description/completed だけを実装",
    ),
    ReviewItem(
        principle=Principle.YAGNI,
        severity=ReviewSeverity.MAJOR,
        question="設定フラグ・拡張ポイントが「今」必要か？",
        description=(
            "feature_flag、enable_xxx、future_use などの"
            "現在使われていない拡張ポイントは YAGNI 違反の典型。"
        ),
        bad_example=(
            "`def process(data, enable_v2=False, use_cache=False, "
            "legacy_mode=False)` — 全フラグが未使用"
        ),
        good_example="`def process(data)` — 必要になったときにパラメータを追加",
    ),
    ReviewItem(
        principle=Principle.YAGNI,
        severity=ReviewSeverity.INFO,
        question="セキュリティ機能にYAGNIを適用していないか？",
        description=(
            "認証・認可・入力検証・暗号化はYAGNI対象外。"
            "セキュリティは後付けが困難なためプロアクティブに実装する。"
        ),
        bad_example="「認証は後で追加しよう」と未実装のままAPIを公開",
        good_example="認証・認可は設計初期から組み込む",
    ),
]


# ============================================================
# レビューセッション
# ============================================================

@dataclass
class ReviewFinding:
    """レビューで発見した問題"""
    item: ReviewItem
    file_path: str
    line_number: int
    comment: str
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.now)


class ReviewChecklist:
    """
    コードレビュー用チェックリスト管理ツール
    チームで共有してレビュー観点を標準化する
    """

    def __init__(self, items: list[ReviewItem] | None = None):
        self.items = items or DEFAULT_REVIEW_ITEMS
        self.findings: list[ReviewFinding] = []

    def run_checklist(self, pr_title: str) -> None:
        """チェックリストを表示してレビューをガイドする"""
        print(f"\n{'='*55}")
        print(f"コードレビュー チェックリスト")
        print(f"PR: {pr_title}")
        print(f"{'='*55}")

        for principle in Principle:
            items = [i for i in self.items if i.principle == principle]
            if not items:
                continue
            print(f"\n## {principle.value}")
            for item in items:
                item.display()

    def add_finding(
        self,
        item: ReviewItem,
        file_path: str,
        line_number: int,
        comment: str,
    ) -> None:
        """レビュー指摘を記録"""
        finding = ReviewFinding(item, file_path, line_number, comment)
        self.findings.append(finding)
        print(f"\n  📝 指摘追加: [{item.principle.value}] {file_path}:{line_number}")

    def summary(self) -> None:
        """レビュー結果のサマリーを表示"""
        print(f"\n{'='*55}")
        print("レビューサマリー")
        print(f"{'='*55}")
        print(f"総指摘数: {len(self.findings)}")

        by_severity: dict[ReviewSeverity, list[ReviewFinding]] = {}
        for finding in self.findings:
            by_severity.setdefault(finding.item.severity, []).append(finding)

        for severity in ReviewSeverity:
            items = by_severity.get(severity, [])
            if items:
                print(f"\n{severity.value} ({len(items)}件)")
                for f in items:
                    print(f"  - {f.file_path}:{f.line_number} — {f.comment}")


# ============================================================
# ペアプログラミング ガイド
# ============================================================
# 【ペアプロが原則定着に効果的な理由】
#   - ドライバー（実装）がKISSを考えながらコードを書く
#   - ナビゲーター（方針）がYAGNI・DRY違反をリアルタイムで指摘
#   - コードレビューより即時フィードバックが得られる
#   - ペアのローテーションで原則理解がチーム全体に浸透
#
# 【推奨ペース（ポモドーロ）】
#   25分集中 + 5分休憩、1日最大6時間
# 【用語解説】ポモドーロ = 25分作業+5分休憩のタイムマネジメント技法

class PairingRole(Enum):
    """ペアプログラミングの役割"""
    DRIVER = "ドライバー（実装担当）"
    NAVIGATOR = "ナビゲーター（方針・品質担当）"


@dataclass
class PairProgrammingSession:
    """ペアプログラミングセッションの記録とガイド"""
    driver: str
    navigator: str
    task_description: str
    session_start: datetime.datetime = field(default_factory=datetime.datetime.now)

    # ポモドーロパラメータ
    pomodoro_minutes: int = 25
    break_minutes: int = 5
    max_hours_per_day: int = 6

    def checklist_for_navigator(self) -> None:
        """ナビゲーター用チェックリスト（リアルタイムレビュー）"""
        print(f"\n{'='*50}")
        print(f"ペアプログラミング: ナビゲーターチェックリスト")
        print(f"ドライバー : {self.driver}")
        print(f"ナビゲーター: {self.navigator}")
        print(f"タスク     : {self.task_description}")
        print(f"{'='*50}")

        checks = [
            ("YAGNI", "このコードは現在のタスクに必要か？"),
            ("KISS",  "よりシンプルな書き方はないか？"),
            ("DRY",   "同じロジックがすでに別の場所にないか？"),
            ("KISS",  "命名から意図が一目でわかるか？"),
            ("YAGNI", "将来の要件を推測して実装していないか？"),
            ("DRY",   "3回目の重複が出たら抽象化を提案したか？"),
        ]

        print("\n【ナビゲーターが常に問うべき5つの問い】")
        for i, (principle, question) in enumerate(checks, 1):
            print(f"  {i}. [{principle}] {question}")

    def switch_roles(self) -> PairProgrammingSession:
        """
        役割交代（25分ごとに推奨）
        知識のサイロ化（一人だけが知っている状態）を防ぐ
        """
        print(f"\n🔄 役割交代: {self.driver} ↔ {self.navigator}")
        # 新しいセッションで役割を入れ替え
        return PairProgrammingSession(
            driver=self.navigator,
            navigator=self.driver,
            task_description=self.task_description,
        )


# ============================================================
# チームへの導入ロードマップ
# ============================================================

class TeamOnboarding:
    """
    チームへのKISS・DRY・YAGNI導入ロードマップ

    【メリット】
      ✅ 設計原則が明文化されチーム全体の品質基準が揃う
      ✅ コードレビューの観点が標準化され指摘の属人化が減る
      ✅ 新規メンバーが参加しやすい（オンボーディングコスト低下）

    【デメリット・課題】
      ❌ 導入初期は心理的安全性の確保が最重要
      ❌ 原則を「ルール」として機械的適用するとチームの柔軟性が損なわれる
      ❌ XP的プラクティスの熟達には3〜6ヶ月を要する
    """

    STEPS = [
        {
            "phase": "Phase 1: 認識共有（1〜2週間）",
            "actions": [
                "ワークショップで実際のコードを使い原則違反と改善例を比較",
                "「なぜこの原則が価値を生むか」まで説明（標語にしない）",
                "心理的安全性の醸成: 知識ギャップを見せることへの抵抗感を取り除く",
            ],
            "pitfalls": [
                "「とりあえず規約として制定」すると形骸化する",
                "マネージャーへの説明なしに進めると支援が得られない",
            ],
        },
        {
            "phase": "Phase 2: 限定実験（1スプリント）",
            "actions": [
                "1チーム・1スプリントの限定スコープで試験的に導入",
                "スプリントプランニングに原則チェック（YAGNI: 今必要か？）を追加",
                "コードレビューにDRY・KISS・YAGNIチェックリストを試験適用",
            ],
            "pitfalls": [
                "全プロジェクト一斉導入はリスクが高い",
                "原則適用の成否を測る指標を事前に決めておく",
            ],
        },
        {
            "phase": "Phase 3: プロセス組み込み（1〜3ヶ月）",
            "actions": [
                "レトロスペクティブに原則違反ケースの振り返りを追加",
                "ペアプログラミングを週に一度実施（心理的安全性を先に確保）",
                "TDDと組み合わせてYAGNIを自然に強制する",
                "リファクタリングデーを月1回スプリントに組み込む",
            ],
            "pitfalls": [
                "ペアプロは強制すると逆効果: 自発的参加から始める",
                "「いつかDRY化する」TODOは実行されない: 即座に直すかチケット化",
            ],
        },
        {
            "phase": "Phase 4: 標準化・横展開（3〜6ヶ月）",
            "actions": [
                "チーム内コーディング規約として原則を明文化",
                "新規メンバーオンボーディングに原則学習を組み込む",
                "定期的な勉強会（実コードのコードレビュー形式）を開催",
                "成功事例・失敗事例をチームwikiに蓄積",
            ],
            "pitfalls": [
                "原則を「ルール」として機械的適用 → 文脈無視で悪化することがある",
                "「AHA（Avoid Hasty Abstractions）」を意識: 早すぎる抽象化は害",
            ],
        },
    ]

    @classmethod
    def print_roadmap(cls) -> None:
        """導入ロードマップを表示"""
        print(f"\n{'='*55}")
        print("チーム導入ロードマップ: KISS・DRY・YAGNI")
        print(f"{'='*55}")

        for step in cls.STEPS:
            print(f"\n### {step['phase']}")
            print("  【アクション】")
            for action in step["actions"]:
                print(f"    ✅ {action}")
            print("  【落とし穴】")
            for pitfall in step["pitfalls"]:
                print(f"    ⚠️  {pitfall}")


# ============================================================
# メイン実行
# ============================================================
if __name__ == "__main__":
    # --- チェックリストのデモ ---
    checklist = ReviewChecklist()
    checklist.run_checklist("feat: タスク管理機能の追加")

    # --- 指摘の記録デモ ---
    print("\n\n--- レビュー指摘の記録デモ ---")
    yagni_item = next(
        i for i in DEFAULT_REVIEW_ITEMS
        if i.principle == Principle.YAGNI and i.severity == ReviewSeverity.BLOCKER
    )
    checklist.add_finding(
        item=yagni_item,
        file_path="task_manager.py",
        line_number=42,
        comment="TagsとSubtasksは現スプリントの要件外。削除を推奨",
    )

    dry_item = next(
        i for i in DEFAULT_REVIEW_ITEMS
        if i.principle == Principle.DRY and i.severity == ReviewSeverity.MAJOR
    )
    checklist.add_finding(
        item=dry_item,
        file_path="validators.py",
        line_number=15,
        comment="年齢バリデーションが3箇所に重複。validate_age()に集約を推奨",
    )

    checklist.summary()

    # --- ペアプロガイド ---
    print("\n\n--- ペアプログラミング ガイド ---")
    session = PairProgrammingSession(
        driver="田中（実装担当）",
        navigator="鈴木（品質担当）",
        task_description="US-001: ユーザー登録機能",
    )
    session.checklist_for_navigator()

    # --- 導入ロードマップ ---
    TeamOnboarding.print_roadmap()

    print("\n✅ チェックリストツール デモ完了")
