# PoC品質 - アンチパターンと修正例のデモンストレーション
# このファイルは学習・説明目的のPoC（概念実証）コードです。本番利用には適しません。

"""
KISS・DRY・YAGNI 違反アンチパターン集
=======================================
各原則に違反した「悪い例」と、原則を適用した「良い例」を対比します。
実際の開発現場で見られるパターンを基にした教育用コードです。

用語解説:
- アンチパターン: 一見よさそうに見えるが、実際には問題を引き起こす設計パターン
- リファクタリング: 外部から見た振る舞いを変えずに、内部のコード構造を改善すること
- 技術的負債: 今は動くが、将来の開発スピードや品質を下げる設計上の問題の蓄積
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import date


# =============================================================================
# アンチパターン 1: Lasagna Architecture（KISS違反）
# =============================================================================
# 【概要】シンプルな処理に対して、不必要なレイヤーが積み重なった設計
# 【影響】コードの追跡が困難になり、バグの場所を特定するのに多大な時間を要する

print("=" * 60)
print("アンチパターン 1: Lasagna Architecture（KISS違反）")
print("=" * 60)


# --- 悪い例: シンプルな「在庫確認」に不要なレイヤーが8層 ---
class InventoryRepositoryInterface:
    def get_stock(self, item_id: int) -> int:
        raise NotImplementedError


class InventoryRepository(InventoryRepositoryInterface):
    _mock_data = {1: 50, 2: 0, 3: 20}

    def get_stock(self, item_id: int) -> int:
        return self._mock_data.get(item_id, 0)


class InventoryDataAccessObject:
    def __init__(self):
        self._repo = InventoryRepository()

    def fetch_stock(self, item_id: int) -> int:
        return self._repo.get_stock(item_id)  # 何もしていないレイヤー


class InventoryServiceLayer:
    def __init__(self):
        self._dao = InventoryDataAccessObject()

    def check_availability(self, item_id: int) -> int:
        return self._dao.fetch_stock(item_id)  # 何もしていないレイヤー


class InventoryFacade:
    def __init__(self):
        self._service = InventoryServiceLayer()

    def is_available(self, item_id: int) -> bool:
        return self._service.check_availability(item_id) > 0  # ようやく判定


# 呼び出し側は実装の詳細を知るためにレイヤーをすべて追う必要がある（KISS違反）
facade = InventoryFacade()
print(f"\n【KISS違反】在庫確認（5レイヤー経由）: item_id=1 → {facade.is_available(1)}")
print("  問題: デバッグ時に InventoryFacade → InventoryServiceLayer → InventoryDataAccessObject")
print("         → InventoryRepository → InventoryRepositoryInterface を追う必要がある")


# --- 良い例: シンプルな辞書で十分 ---
STOCK_DATA = {1: 50, 2: 0, 3: 20}  # 実際はDBや外部APIから取得


def is_item_available(item_id: int) -> bool:
    """【KISS適用】1行で解決。本当に必要になったらその時に拡張する。"""
    return STOCK_DATA.get(item_id, 0) > 0


print(f"\n【KISS適用】在庫確認（直接実装）:  item_id=1 → {is_item_available(1)}")
print("  利点: コードが1行で読め、デバッグが即座に完了する\n")


# =============================================================================
# アンチパターン 2: Copy-Paste Programming（DRY違反）
# =============================================================================
# 【概要】同じロジックをコピーして複数箇所に貼り付けた状態（WETコード）
# 【WET】= Write Everything Twice / Waste Everyone's Time
# 【影響】修正が必要になると複数箇所を変更する必要があり、修正漏れでバグが発生

print("=" * 60)
print("アンチパターン 2: Copy-Paste Programming（DRY違反）")
print("=" * 60)


@dataclass
class Product:
    name: str
    price: float
    stock: int


@dataclass
class Order:
    product_name: str
    quantity: int
    unit_price: float


# --- 悪い例: 同じバリデーションロジックが3か所にコピペされている ---
def create_product_bad(name: str, price: float, stock: int) -> Optional[Product]:
    """【DRY違反】バリデーションロジックが重複"""
    if not name or not name.strip():     # ← バリデーション重複①
        print("エラー: 名前が空です")
        return None
    if price <= 0:                        # ← バリデーション重複②
        print("エラー: 価格は正の値である必要があります")
        return None
    if stock < 0:
        print("エラー: 在庫数は0以上である必要があります")
        return None
    return Product(name.strip(), price, stock)


def create_order_bad(product_name: str, quantity: int, unit_price: float) -> Optional[Order]:
    """【DRY違反】バリデーションロジックが重複（上とほぼ同じ）"""
    if not product_name or not product_name.strip():   # ← バリデーション重複①（コピペ）
        print("エラー: 商品名が空です")
        return None
    if unit_price <= 0:                                # ← バリデーション重複②（コピペ）
        print("エラー: 単価は正の値である必要があります")
        return None
    if quantity <= 0:
        print("エラー: 数量は1以上である必要があります")
        return None
    return Order(product_name.strip(), quantity, unit_price)


# --- 良い例: バリデーション関数を共通化 ---
def validate_name(name: str, field_label: str = "名前") -> str:
    """【DRY適用】名前バリデーションを単一関数に集約"""
    if not name or not name.strip():
        raise ValueError(f"{field_label}が空です")
    return name.strip()


def validate_positive_number(value: float, field_label: str) -> float:
    """【DRY適用】正数バリデーションを単一関数に集約"""
    if value <= 0:
        raise ValueError(f"{field_label}は正の値である必要があります（受け取った値: {value}）")
    return value


def create_product_good(name: str, price: float, stock: int) -> Product:
    """【DRY適用】共通バリデーション関数を呼び出すだけ"""
    validated_name = validate_name(name, "商品名")
    validated_price = validate_positive_number(price, "価格")
    if stock < 0:
        raise ValueError(f"在庫数は0以上である必要があります（受け取った値: {stock}）")
    return Product(validated_name, validated_price, stock)


def create_order_good(product_name: str, quantity: int, unit_price: float) -> Order:
    """【DRY適用】共通バリデーション関数を呼び出すだけ"""
    validated_name = validate_name(product_name, "商品名")
    validated_qty = validate_positive_number(quantity, "数量")
    validated_price = validate_positive_number(unit_price, "単価")
    return Order(validated_name, int(validated_qty), validated_price)


print("\n【DRY適用】共通バリデーション関数のテスト:")
try:
    p = create_product_good("コーヒー豆", 1200.0, 50)
    print(f"  商品作成成功: {p}")
    o = create_order_good("コーヒー豆", 3, 1200.0)
    print(f"  注文作成成功: {o}")
    print(f"  合計金額: ¥{o.quantity * o.unit_price:,.0f}")
except ValueError as e:
    print(f"  バリデーションエラー: {e}")

try:
    invalid = create_product_good("", -100.0, 10)
except ValueError as e:
    print(f"  期待通りエラー検出: {e}")

print("""
  利点: バリデーションルール変更時は validate_name / validate_positive_number
        の1か所だけ修正すれば、全関数に自動的に反映される
""")


# =============================================================================
# アンチパターン 3: Gold Plating（YAGNI違反）
# =============================================================================
# 【概要】顧客が要求していない機能を「将来必要になるかもしれない」と先行実装
# 【Gold Plating】= 金メッキ加工（必要以上の装飾を施すこと）
# 【影響】開発コストの増大・リリース遅延・使われない機能による保守コストの継続的発生

print("=" * 60)
print("アンチパターン 3: Gold Plating（YAGNI違反）")
print("=" * 60)


@dataclass
class TaskGoldPlated:
    """
    【YAGNI違反】要求は「タイトルと期日を持つタスク」のみ。
    将来使うかもしれない多数のフィールドを先行実装している。
    """
    title: str
    due_date: date
    # 以下は全てYAGNI違反（未要求フィールド）
    priority: int = 3                    # 未要求
    tags: list = field(default_factory=list)  # 未要求
    assignee: Optional[str] = None       # 未要求
    estimated_hours: Optional[float] = None   # 未要求
    actual_hours: Optional[float] = None      # 未要求
    parent_task_id: Optional[int] = None      # 未要求（階層構造も未要求）
    sprint_id: Optional[int] = None           # 未要求
    story_points: Optional[int] = None        # 未要求
    acceptance_criteria: list = field(default_factory=list)  # 未要求
    attachments: list = field(default_factory=list)          # 未要求


@dataclass
class Task:
    """
    【YAGNI適用】現在の要求（タイトルと期日）のみを実装。
    優先度・担当者機能が必要になったら、そのときにフィールドを追加する。
    """
    title: str
    due_date: date


print(f"\n【YAGNI違反】Gold Platted Task のフィールド数: {len(TaskGoldPlated.__dataclass_fields__)}")
print(f"【YAGNI適用】シンプルな Task のフィールド数:    {len(Task.__dataclass_fields__)}")

simple_task = Task(title="READMEを更新する", due_date=date(2026, 5, 20))
print(f"\n【YAGNI適用】タスク作成: {simple_task}")

print("""
▶ YAGNI違反のコスト（Martin Fowlerの分類）:
  ┌──────────────┬─────────────────────────────────────────────┐
  │ コスト種別    │ 内容                                         │
  ├──────────────┼─────────────────────────────────────────────┤
  │ Build コスト  │ 不要機能の分析・実装・テストに費やした工数    │
  │ Delay コスト  │ 本来必要な機能が遅延した機会損失              │
  │ Carry コスト  │ 複雑化したコードが以後の全開発を継続的に遅延  │
  │ Repair コスト │ 的外れと判明した場合のリファクタリング費用    │
  └──────────────┴─────────────────────────────────────────────┘
""")


if __name__ == "__main__":
    print("=" * 60)
    print("アンチパターンデモ完了")
    print("次: agile_lean_tracker.py でアジャイル・リーンの実践例を確認")
    print("=" * 60)
