# PoC品質: このファイルは学習・デモ用のスケルトンコードです。本番環境での使用は想定していません。
"""
IT開発原則デモ: KISS・DRY・YAGNI
======================================
各原則について「違反例（Bad）」と「適用例（Good）」を並べて示します。

【用語解説】
- KISS : Keep It Simple, Stupid — シンプルに保て
- DRY  : Don't Repeat Yourself — 繰り返すな
- YAGNI: You Aren't Gonna Need It — 今必要でないものは作るな
"""

# ============================================================
# セクション 1: DRY（Don't Repeat Yourself）
# ============================================================
# 【原則の意味】
#   同じロジック・知識を複数箇所に書かない。
#   変更するとき「1箇所だけ直せばいい」状態を目指す。
#
# 【メリット】
#   ✅ バグ修正・仕様変更の影響範囲が小さい
#   ✅ コードの意図が明確になる
#   ✅ テスト箇所が集約されて品質が上がる
#
# 【デメリット・注意点】
#   ❌ 過剰な抽象化（早すぎる共通化）が密結合を生む
#   ❌ 「コードが似ている」≠「知識が同じ」— 概念が異なれば分けるべき
#   ❌ ユニットテストでは意図的に重複させることが推奨される場面もある
#
# 【実践指針: Rule of Three】
#   1回目: そのまま書く
#   2回目: コピーしてもよい
#   3回目: ここで初めて抽象化を検討する

import sqlite3
from contextlib import contextmanager


# --- DRY 違反例 ---
class BadShapeCalculator:
    """同じ出力ロジックが各メソッドに散在している（DRY違反）"""

    def rectangle_area(self, width: float, height: float) -> float:
        area = width * height
        # ❌ 出力フォーマットが3箇所に重複 → 変更時に全箇所修正が必要
        print(f"長方形の面積は {area} です")
        return area

    def square_area(self, side: float) -> float:
        area = side * side
        print(f"正方形の面積は {area} です")  # ❌ 重複
        return area

    def triangle_area(self, base: float, height: float) -> float:
        area = 0.5 * base * height
        print(f"三角形の面積は {area} です")  # ❌ 重複
        return area


# --- DRY 適用例 ---
class GoodShapeCalculator:
    """出力ロジックを1箇所に集約（DRY適用）"""

    def _print_area(self, shape_name: str, area: float) -> None:
        # ✅ 出力形式の変更はここだけ直せばよい
        print(f"{shape_name}の面積は {area:.2f} です")

    def rectangle_area(self, width: float, height: float) -> float:
        area = width * height
        self._print_area("長方形", area)
        return area

    def square_area(self, side: float) -> float:
        area = side ** 2
        self._print_area("正方形", area)
        return area

    def triangle_area(self, base: float, height: float) -> float:
        area = 0.5 * base * height
        self._print_area("三角形", area)
        return area


# --- DRY: デコレータによる横断的関心事の共通化 ---
# 【用語解説】横断的関心事 = ログ・認証など、複数の処理に共通で必要な処理
import functools
import time


def log_execution(func):
    """関数呼び出しのログを共通化するデコレータ（DRY）"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # ✅ ログ処理を1箇所に集約
        print(f"[実行開始] {func.__name__}")
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        print(f"[実行完了] {func.__name__} ({elapsed:.3f}秒)")
        return result
    return wrapper


@log_execution
def process_order(order_id: int) -> str:
    # ✅ 本処理だけに集中できる
    return f"注文 {order_id} を処理しました"


@log_execution
def send_notification(user_id: int) -> str:
    return f"ユーザー {user_id} に通知を送信しました"


# --- DRY: コンテキストマネージャでDB接続を共通化 ---
# 【用語解説】コンテキストマネージャ = with文で使える「開始・終了処理をまとめた仕組み」
@contextmanager
def get_db_connection(db_path: str = ":memory:"):
    """DB接続のオープン/クローズを1箇所に集約（DRY）"""
    conn = sqlite3.connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# セクション 2: KISS（Keep It Simple, Stupid）
# ============================================================
# 【原則の意味】
#   コードをできるだけシンプルに保て。
#   複雑さは「本質的複雑さ（問題自体の難しさ）」と
#   「偶発的複雑さ（設計の不必要な難しさ）」に分かれる。
#   KISSが対処するのは後者。
#
# 【メリット】
#   ✅ 読み解くのが速い（オンボーディングコスト低下）
#   ✅ バグの発見・修正が容易
#   ✅ 変更の影響範囲が予測しやすい
#
# 【デメリット・注意点】
#   ❌ 「シンプル」の定義は主観的で、経験により異なる
#   ❌ シンプルなコードを書くには設計の手間がかかる（簡単ではない）
#   ❌ 過度なシンプル化は拡張性を犠牲にすることがある


# --- KISS 違反例: 不要なif/else ---
def is_even_bad(num: int) -> bool:
    # ❌ 条件式自体がboolを返すのに、if/elseで包み直している（冗長）
    if num % 2 == 0:
        return True
    else:
        return False


# --- KISS 適用例 ---
def is_even_good(num: int) -> bool:
    # ✅ 条件式をそのまま返す
    return num % 2 == 0


# --- KISS 違反例: 難解な1行 ---
def complex_math_bad(a: float, b: float) -> float:
    # ❌ 三項演算子のネストと複数演算を1行に詰め込み、意図が読めない
    return ((a**2)*(b+5))-((a+b)/2) if a > 10 else ((a*b)-(b/2)+a**b)


# --- KISS 適用例 ---
def complex_math_good(a: float, b: float) -> float:
    # ✅ 論理ステップを明示的に分離
    if a > 10:
        result = (a**2) * (b + 5) - (a + b) / 2
    else:
        result = (a * b) - (b / 2) + a**b
    return result


# --- KISS 違反例: 過剰なクラス設計 ---
class BadSalesAnalyzer:
    """
    ❌ 単純な集計にクラス・scipy統計検定・複数メソッドを使い過剰設計
    """
    def __init__(self, data: list):
        self.data = data
        self._processed = False

    def preprocess(self):
        # 外部ライブラリ依存が増え、依存管理コストが高い
        self._processed = True

    def calculate_stats(self):
        if not self._processed:
            raise RuntimeError("先にpreprocessを呼んでください")
        # ...複雑な処理...

    def perform_t_test(self, group1: str, group2: str):
        # 現在の要件に統計検定は含まれていない可能性
        pass

    def generate_report(self):
        self.preprocess()
        stats = self.calculate_stats()
        return stats


# --- KISS 適用例 ---
def analyze_sales(data: list[dict]) -> dict:
    """
    ✅ 必要な集計だけをシンプルな関数で実装
    クラス不要、外部ライブラリ最小限
    """
    total = sum(row.get("sales", 0) for row in data)
    count = len(data)
    average = total / count if count > 0 else 0

    return {
        "total_sales": total,
        "average_sales": average,
        "record_count": count,
    }


# ============================================================
# セクション 3: YAGNI（You Aren't Gonna Need It）
# ============================================================
# 【原則の意味】
#   現在の要件に必要でない機能は実装しない。
#   ケント・ベックのXP（エクストリームプログラミング）が起源。
#
# 【推測的機能のコスト — Martin Fowler の分類】
#   1. 構築コスト : 分析・実装・テストに費やすリソース
#   2. 遅延コスト : 本当に必要な機能開発の機会損失
#   3. 維持コスト : 不要な複雑性が全後続開発を遅らせる
#   4. 修正コスト : 後で要件が変わった際の作り直しコスト
#
# 【重要な実証データ】
#   Microsoftの研究(Kohavi et al.)によると、
#   慎重に分析された機能のうち価値を生んだのは1/3のみ。
#
# 【デメリット・注意点】
#   ❌ リファクタリング習慣なしには技術的負債を生む
#   ❌ セキュリティ機能への適用は禁物（後付けが困難）
#   ❌ アーキテクチャの「考える」ことまで禁じるわけではない
#      → 「考えること（thinking ahead）」と「作ること（building ahead）」は別


# --- YAGNI 違反例 ---
class BadTask:
    """
    ❌ 「いつか必要かも」で多くのフィールド・メソッドを先行実装
    現在の要件: タスクの作成と完了管理だけ
    """
    def __init__(self, title: str, description: str):
        self.title = title
        self.description = description
        self.completed = False
        # ❌ 以下は現在不要
        self.priority = "medium"        # 優先度管理は要件にない
        self.tags: list = []            # タグ機能は要件にない
        self.subtasks: list = []        # サブタスクは要件にない
        self.assigned_to = None         # アサイン機能は要件にない
        self.due_date = None            # 期限管理は要件にない
        self.recurring = False          # 繰り返しタスクは要件にない
        self.recurrence_interval = None

    def complete(self): self.completed = True
    def set_priority(self, p): self.priority = p          # ❌ 未使用
    def add_tag(self, tag): self.tags.append(tag)         # ❌ 未使用
    def add_subtask(self, st): self.subtasks.append(st)   # ❌ 未使用
    def assign(self, person): self.assigned_to = person   # ❌ 未使用
    def set_due_date(self, d): self.due_date = d          # ❌ 未使用
    def set_recurring(self, interval):                     # ❌ 未使用
        self.recurring = True
        self.recurrence_interval = interval


# --- YAGNI 適用例 ---
class GoodTask:
    """
    ✅ 現在の要件（作成・完了管理）だけを実装
    将来の機能拡張は、必要になったときに追加する
    """
    def __init__(self, title: str, description: str):
        self.title = title
        self.description = description
        self.completed = False

    def complete(self) -> None:
        self.completed = True

    def __repr__(self) -> str:
        status = "完了" if self.completed else "未完了"
        return f"Task('{self.title}', {status})"


# --- YAGNI 違反例: 支払い処理 ---
class BadPaymentProcessor:
    """❌ 現在未使用の決済方法を先行実装"""

    def process_credit_card(self, amount: float) -> bool:
        print(f"クレジットカードで {amount}円 を処理")
        return True

    def process_bitcoin(self, amount: float) -> bool:
        # ❌ Bitcoin決済は現在の要件にない — テスト・保守コストだけが発生
        print(f"Bitcoinで {amount}円 を処理")
        return True

    def process_qr_code(self, amount: float) -> bool:
        # ❌ QRコード決済も要件にない
        print(f"QRコードで {amount}円 を処理")
        return True


# --- YAGNI 適用例 ---
class GoodPaymentProcessor:
    """✅ 現在必要なクレジットカード決済のみ実装"""

    def process_credit_card(self, amount: float) -> bool:
        print(f"クレジットカードで {amount}円 を処理")
        return True
    # 将来 Bitcoin や QR が必要になったらここに追加する


# ============================================================
# セクション 4: 原則間のトレードオフ
# ============================================================
# 【DRY vs KISS】
#   DRYを追求すると複雑な抽象化が生まれKISSに違反することがある
#   → 競合時は KISS 優先が推奨される
#
# 【DRY vs YAGNI】
#   DRYは将来の変更を見越した抽象化を推奨するが、
#   YAGNIは現在不要な実装を禁じる
#   → 「今必要な重複を排除する」という形で両立させる
#
# 【テストにおける特例】
#   ユニットテスト（単体テスト）では隔離性のため
#   意図的にDRYを破ることが推奨される


def demonstrate_tradeoff():
    """
    DRY と KISS のトレードオフ例:
    過剰なDRY適用 → 複雑な抽象化 → KISSに違反
    """
    # ❌ 過剰DRY: 引数の多い「万能関数」はKISSに反する
    def send_message_over_dry(
        recipient,
        content,
        channel="email",
        format="html",
        priority="normal",
        retry=3,
        cc_list=None,
        template_id=None,
    ):
        # 複雑な条件分岐が増殖...
        pass

    # ✅ KISS優先: 用途ごとにシンプルな関数を用意（多少の重複を許容）
    def send_email(recipient: str, content: str) -> None:
        print(f"メール送信: {recipient}")

    def send_sms(recipient: str, content: str) -> None:
        print(f"SMS送信: {recipient}")

    # 3回以上同じパターンが現れたら共通化を検討（Rule of Three）


# ============================================================
# メイン実行（動作確認）
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("KISS・DRY・YAGNI 原則 デモ")
    print("=" * 50)

    # DRY デモ
    print("\n--- DRY: 面積計算 ---")
    calc = GoodShapeCalculator()
    calc.rectangle_area(5, 3)
    calc.square_area(4)
    calc.triangle_area(6, 4)

    # DRY: デコレータ
    print("\n--- DRY: ログデコレータ ---")
    process_order(1001)
    send_notification(42)

    # KISS デモ
    print("\n--- KISS: is_even ---")
    print(f"4は偶数? {is_even_good(4)}")
    print(f"7は偶数? {is_even_good(7)}")

    print("\n--- KISS: 売上分析 ---")
    sample_data = [
        {"product": "A", "sales": 1500},
        {"product": "B", "sales": 3200},
        {"product": "C", "sales": 800},
    ]
    result = analyze_sales(sample_data)
    for key, value in result.items():
        print(f"  {key}: {value}")

    # YAGNI デモ
    print("\n--- YAGNI: タスク管理 ---")
    task = GoodTask("資料作成", "週次報告書を作成する")
    print(f"作成直後: {task}")
    task.complete()
    print(f"完了後  : {task}")

    print("\n--- YAGNI: 支払い処理 ---")
    processor = GoodPaymentProcessor()
    processor.process_credit_card(5000)

    print("\n✅ デモ完了")
