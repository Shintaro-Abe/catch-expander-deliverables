# PoC品質 - IT開発原則（KISS・DRY・YAGNI）デモンストレーション
# このファイルは学習・説明目的のPoC（概念実証）コードです。本番利用には適しません。

"""
IT開発における3大原則のデモ
=============================
- KISS (Keep It Simple, Stupid): シンプルさを最優先にする
- DRY (Don't Repeat Yourself): 知識の重複を避ける
- YAGNI (You Aren't Gonna Need It): 今必要でないものは作らない
"""

from dataclasses import dataclass
from typing import Optional
import re


# =============================================================================
# SECTION 1: KISS 原則 (Keep It Simple, Stupid)
# =============================================================================
# 【定義】不必要な複雑性を避け、シンプルな設計を最優先にする原則
# 【よくある間違い】「愚かな設計を推奨している」ではなく、
#                   「設計そのものを愚直なほどシンプルに保て」という意味

print("=" * 60)
print("SECTION 1: KISS 原則")
print("=" * 60)


# --- KISS 違反例 (Over-Engineering / 過剰設計) ---
class ValidationStrategyFactory:
    """【KISS違反】シンプルなメール検証に不要な抽象化レイヤーを追加した例"""

    @staticmethod
    def create_strategy(strategy_type: str):
        strategies = {
            "regex": RegexEmailValidator(),
            "simple": SimpleEmailValidator(),
        }
        return strategies.get(strategy_type, SimpleEmailValidator())


class RegexEmailValidator:
    def validate(self, email: str) -> bool:
        pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        return bool(re.match(pattern, email))


class SimpleEmailValidator:
    def validate(self, email: str) -> bool:
        return "@" in email and "." in email


# 呼び出しが複雑になる（KISS違反）
factory = ValidationStrategyFactory()
validator = factory.create_strategy("simple")
result_bad = validator.validate("user@example.com")
print(f"\n【KISS違反】過剰設計のメール検証: {result_bad}")


# --- KISS 適用例 (シンプルな関数1つで解決) ---
def is_valid_email(email: str) -> bool:
    """
    【KISS適用】シンプルなメール検証。
    パターンマッチが必要になったら、そのときに拡張する。
    """
    return "@" in email and "." in email.split("@")[-1]


result_good = is_valid_email("user@example.com")
print(f"【KISS適用】シンプルなメール検証:   {result_good}")

print("""
▶ KISSの教訓:
  - 50行で解決できるものを500行の複雑なフレームワークで実装しない
  - 「まず機能させ、次に正しくし、必要に応じて最適化する」
  - シンプルさ = 可読性・保守性の確保
""")


# =============================================================================
# SECTION 2: DRY 原則 (Don't Repeat Yourself)
# =============================================================================
# 【定義】「システム内のあらゆる知識の断片は、単一の、曖昧さのない、
#          権威ある表現を持たなければならない」(Hunt & Thomas, 1999)
# 【重要】コードの見た目の重複だけでなく、「知識・概念の重複」を排除する

print("=" * 60)
print("SECTION 2: DRY 原則")
print("=" * 60)


# --- DRY 違反例 (WETコード: Write Everything Twice) ---
def get_user_discount_bad(user_type: str, price: float) -> float:
    """【DRY違反】割引ロジックが複数箇所に重複している"""
    if user_type == "premium":
        discount_rate = 0.20  # ← ここに割引率が直書き
        return price * (1 - discount_rate)
    return price


def calculate_order_total_bad(user_type: str, items: list) -> float:
    """【DRY違反】同じ割引ロジックが別の関数にもコピペされている"""
    total = sum(items)
    if user_type == "premium":
        discount_rate = 0.20  # ← 同じ知識が別の場所に重複！
        return total * (1 - discount_rate)  # 修正時に2か所変更が必要
    return total


print("\n【DRY違反】WETコード（割引率が複数箇所に重複）:")
print(f"  get_user_discount_bad('premium', 1000)    = {get_user_discount_bad('premium', 1000)}")
print(f"  calculate_order_total_bad('premium', [500, 500]) = {calculate_order_total_bad('premium', [500, 500])}")
print("  → 割引率を変更する際に2か所（以上）を修正する必要があり、修正漏れのリスク大")


# --- DRY 適用例 (単一の真実のソース: Single Source of Truth) ---

# 割引率の知識を1か所に集約（Single Source of Truth）
DISCOUNT_RATES = {
    "premium": 0.20,   # プレミアム会員: 20%割引
    "standard": 0.05,  # 標準会員:       5%割引
    "guest": 0.00,     # ゲスト:         割引なし
}


def apply_discount(price: float, user_type: str) -> float:
    """
    【DRY適用】割引ロジックを1か所に集約。
    DISCOUNT_RATESを変更するだけで全箇所に反映される。
    """
    rate = DISCOUNT_RATES.get(user_type, 0.00)
    return price * (1 - rate)


def get_user_discount_good(user_type: str, price: float) -> float:
    return apply_discount(price, user_type)


def calculate_order_total_good(user_type: str, items: list) -> float:
    total = sum(items)
    return apply_discount(total, user_type)


print("\n【DRY適用】Single Source of Truth（割引率を1か所で管理）:")
print(f"  get_user_discount_good('premium', 1000)    = {get_user_discount_good('premium', 1000)}")
print(f"  calculate_order_total_good('premium', [500, 500]) = {calculate_order_total_good('premium', [500, 500])}")
print("  → DISCOUNT_RATESを1か所変更するだけで全関数に反映される")

print("""
▶ DRYの教訓:
  - 「知識・概念の重複」を排除する（コードの見た目の類似とは異なる）
  - 要件変更時の修正は1か所で済む → 修正漏れリスク激減
  - 注意: 過剰なDRY適用（Premature Abstraction）は密結合を生む
    → AHA原則: "Avoid Hasty Abstractions"（早まった抽象化を避けよ）
""")


# =============================================================================
# SECTION 3: YAGNI 原則 (You Aren't Gonna Need It)
# =============================================================================
# 【定義】「必要になるまで、機能を実装するな」(Kent Beck, XP)
# 【背景】XP(エクストリームプログラミング)から生まれた原則
# 【4種のコスト】Build・Delay・Carry・Repair (Martin Fowler)

print("=" * 60)
print("SECTION 3: YAGNI 原則")
print("=" * 60)


# --- YAGNI 違反例 (Premature Generalization / 時期尚早な汎用化) ---
class CommentSystemOverEngineered:
    """
    【YAGNI違反】要求は「写真へのコメント追加」のみなのに、
    将来使うかもしれない機能を先行実装した例
    """

    def add_comment_to_photo(self, photo_id: int, comment: str) -> dict:
        """要求された機能"""
        return {"target": "photo", "id": photo_id, "comment": comment}

    # 以下はすべてYAGNI違反（未要求・未使用）
    def add_comment_to_album(self, album_id: int, comment: str) -> dict:
        return {"target": "album", "id": album_id, "comment": comment}

    def add_comment_to_video(self, video_id: int, comment: str) -> dict:
        return {"target": "video", "id": video_id, "comment": comment}

    def add_comment_with_sentiment_analysis(self, target_id: int, comment: str) -> dict:
        # 感情分析APIの統合（まだ必要ない！）
        return {"id": target_id, "comment": comment, "sentiment": "positive"}

    def add_threaded_reply(self, comment_id: int, reply: str) -> dict:
        # スレッド返信機能（まだ必要ない！）
        return {"comment_id": comment_id, "reply": reply}


# --- YAGNI 適用例 (今必要なものだけを実装) ---
class CommentSystem:
    """
    【YAGNI適用】今の要求（写真へのコメント追加）のみを実装。
    アルバムや動画へのコメントが必要になったら、そのときに追加する。
    """

    def add_comment_to_photo(self, photo_id: int, comment: str) -> dict:
        """現時点で唯一要求されている機能"""
        if not comment.strip():
            raise ValueError("コメントが空です")
        return {"target": "photo", "id": photo_id, "comment": comment}


print("\n【YAGNI違反】過剰に実装されたコメントシステム:")
over_system = CommentSystemOverEngineered()
print(f"  実装メソッド数: 5個（要求は1個のみ）")
print("  未使用メソッドがコードベースの複雑性を増し、保守コストが継続的に増大")

print("\n【YAGNI適用】必要最小限のコメントシステム:")
lean_system = CommentSystem()
result = lean_system.add_comment_to_photo(42, "素晴らしい写真です！")
print(f"  実装メソッド数: 1個（要求通り）")
print(f"  実行結果: {result}")

print("""
▶ YAGNIの教訓（Martin Fowlerの4コスト）:
  1. Build コスト: 不要機能の分析・実装・テストに費やした工数
  2. Delay コスト: その時間で本来必要な機能が遅延（機会損失）
  3. Carry コスト: 余剰コードが複雑性を増し、以後の開発を継続的に遅延
  4. Repair コスト: 「的外れだった」と判明した場合の修正費用

  統計: Microsoft研究(Kohavi et al.)によると、
  精緻な事前分析を経た機能でも3分の2は設計目標を達成できなかった

  注意: YAGNI ≠ 品質軽視
  → 継続的リファクタリング・自動テスト・CIとセットで初めて健全に機能する
""")

if __name__ == "__main__":
    print("=" * 60)
    print("全デモ完了。anti_patterns.py と agile_lean_tracker.py も確認してください。")
    print("=" * 60)
