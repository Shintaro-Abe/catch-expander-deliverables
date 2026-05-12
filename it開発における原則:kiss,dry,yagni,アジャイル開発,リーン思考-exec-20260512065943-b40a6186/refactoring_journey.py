# PoC品質 - リファクタリングの旅: 悪いコードから良いコードへ
# このファイルは学習・説明目的のPoC（概念実証）コードです。本番利用には適しません。

"""
リファクタリングの旅: KISS・DRY・YAGNI の段階的適用
====================================================
同じ「ユーザー登録機能」を3つのバージョンで実装し、
各原則を適用することでコードがどう改善されるかを示します。

用語解説:
- リファクタリング: 外部から見た振る舞いを変えずにコード構造を改善すること
- 技術的負債: 今は動くが、将来の開発速度や品質を下げる設計上の問題
- MVP: Minimum Viable Product（実用最小限の製品）
"""

from dataclasses import dataclass
from typing import Optional
import re
import hashlib


# =============================================================================
# バージョン 1: 原則違反の「典型的な悪いコード」
# =============================================================================
# 問題点:
#   - KISS違反: 不要な複雑性（過剰な抽象化レイヤー）
#   - DRY違反: バリデーションロジックが重複
#   - YAGNI違反: 未要求の機能（SNS認証・多言語・プレミアムプラン）が含まれる

class UserRepositoryInterface:
    """【KISS違反】シンプルなメモリストレージに不要なインターフェース"""
    def save(self, user) -> bool:
        raise NotImplementedError

    def find_by_email(self, email: str) -> Optional[dict]:
        raise NotImplementedError


class InMemoryUserRepository(UserRepositoryInterface):
    """PoC用のインメモリストレージ（本来はDB接続）"""
    def __init__(self):
        self._storage: dict = {}

    def save(self, user: dict) -> bool:
        self._storage[user["email"]] = user
        return True

    def find_by_email(self, email: str) -> Optional[dict]:
        return self._storage.get(email)


class UserValidatorFactory:
    """【KISS違反】バリデーターを生成するだけのファクトリ（過剰設計）"""
    @staticmethod
    def create():
        return UserValidator_v1()


class UserValidator_v1:
    """【DRY違反】各メソッドに重複したバリデーションロジック"""

    def validate_for_registration(self, email: str, password: str, username: str) -> tuple:
        errors = []
        # メール検証（この検証ロジックは他の場所にもコピペされている）
        if not email or not email.strip():   # ← 重複①
            errors.append("メールアドレスが空です")
        elif "@" not in email:               # ← 重複②
            errors.append("メールアドレスの形式が無効です")

        # パスワード検証
        if not password or len(password) < 8:
            errors.append("パスワードは8文字以上必要です")

        # ユーザー名検証（この検証ロジックも他の場所にコピペされている）
        if not username or not username.strip():   # ← 重複①（コピペ）
            errors.append("ユーザー名が空です")
        elif len(username) < 3:
            errors.append("ユーザー名は3文字以上必要です")

        return len(errors) == 0, errors

    def validate_for_profile_update(self, email: str, username: str) -> tuple:
        errors = []
        # 【DRY違反】上と同じバリデーションがコピペされている
        if not email or not email.strip():   # ← 重複①（コピペ）
            errors.append("メールアドレスが空です")
        elif "@" not in email:               # ← 重複②（コピペ）
            errors.append("メールアドレスの形式が無効です")

        if not username or not username.strip():   # ← 重複①（コピペ）
            errors.append("ユーザー名が空です")

        return len(errors) == 0, errors


class UserService_v1:
    """
    【YAGNI違反】現在要求されているのは「メール+パスワード登録」のみ。
    SNS認証・多言語・プレミアムプランは未要求なのに先行実装されている。
    """

    def __init__(self):
        self._repo = InMemoryUserRepository()
        self._validator_factory = UserValidatorFactory()

    def register_with_email(self, email: str, password: str, username: str,
                             language: str = "ja",           # YAGNI違反: 未要求
                             plan: str = "free",             # YAGNI違反: 未要求
                             referral_code: Optional[str] = None) -> dict:  # YAGNI違反: 未要求
        validator = self._validator_factory.create()
        is_valid, errors = validator.validate_for_registration(email, password, username)
        if not is_valid:
            return {"success": False, "errors": errors}

        # 【YAGNI違反】未要求の機能が含まれる
        user = {
            "email": email.strip().lower(),
            "username": username.strip(),
            "password_hash": hashlib.sha256(password.encode()).hexdigest(),
            "language": language,          # YAGNI違反: 未要求
            "plan": plan,                  # YAGNI違反: 未要求
            "referral_code": referral_code # YAGNI違反: 未要求
        }
        self._repo.save(user)
        return {"success": True, "user": user}

    def register_with_google(self, google_token: str) -> dict:
        """【YAGNI違反】Google認証は未要求なのに実装されている"""
        # 実際のGoogle OAuth検証（PoC品質: ここでは省略）
        return {"success": False, "errors": ["Google認証はまだ実装されていません"]}

    def register_with_twitter(self, twitter_token: str) -> dict:
        """【YAGNI違反】Twitter認証は未要求なのに実装されている"""
        return {"success": False, "errors": ["Twitter認証はまだ実装されていません"]}


# =============================================================================
# バージョン 2: 各原則を段階的に適用した改善版
# =============================================================================
# 改善点:
#   ✅ KISS適用: 不要な抽象化レイヤーを削除
#   ✅ DRY適用: バリデーションロジックを共通関数に集約
#   ✅ YAGNI適用: 未要求の機能（SNS認証・多言語・プレミアムプラン）を削除

# --- DRY: バリデーションロジックを単一の場所に集約 ---
def _validate_email(email: str) -> Optional[str]:
    """メールアドレスのバリデーション（単一の真実のソース）"""
    if not email or not email.strip():
        return "メールアドレスが空です"
    if "@" not in email or "." not in email.split("@")[-1]:
        return "メールアドレスの形式が無効です（例: user@example.com）"
    return None  # エラーなし


def _validate_username(username: str) -> Optional[str]:
    """ユーザー名のバリデーション（単一の真実のソース）"""
    if not username or not username.strip():
        return "ユーザー名が空です"
    if len(username.strip()) < 3:
        return "ユーザー名は3文字以上必要です"
    return None  # エラーなし


def _validate_password(password: str) -> Optional[str]:
    """パスワードのバリデーション（単一の真実のソース）"""
    if not password or len(password) < 8:
        return "パスワードは8文字以上必要です"
    return None  # エラーなし


# --- KISS: シンプルなストレージ（インターフェース層を排除） ---
_user_storage: dict = {}  # PoC用インメモリDB（本番ではRDBMSを使用）


def _hash_password(password: str) -> str:
    """パスワードのハッシュ化（SHA-256: PoC品質、本番ではbcryptを推奨）"""
    return hashlib.sha256(password.encode()).hexdigest()


@dataclass
class RegisterResult:
    """登録結果を表す型（KISS: シンプルなデータクラス）"""
    success: bool
    user_id: Optional[str] = None
    errors: list = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def register_user(email: str, password: str, username: str) -> RegisterResult:
    """
    【原則適用後】シンプルで明確なユーザー登録関数

    KISS: 必要な処理だけを直線的に実行
    DRY:  バリデーション関数を再利用（重複なし）
    YAGNI: SNS認証・多言語・紹介コードは今は不要なので含めない
    """
    # バリデーション（DRY: 共通関数を再利用）
    errors = [
        err for err in [
            _validate_email(email),
            _validate_password(password),
            _validate_username(username),
        ]
        if err is not None
    ]
    if errors:
        return RegisterResult(success=False, errors=errors)

    # 重複チェック
    normalized_email = email.strip().lower()
    if normalized_email in _user_storage:
        return RegisterResult(success=False, errors=["このメールアドレスは既に登録されています"])

    # ユーザー作成（YAGNI: 現在必要なフィールドのみ）
    user_id = f"user_{len(_user_storage) + 1}"
    _user_storage[normalized_email] = {
        "id": user_id,
        "email": normalized_email,
        "username": username.strip(),
        "password_hash": _hash_password(password),
    }
    return RegisterResult(success=True, user_id=user_id)


def update_user_profile(email: str, new_username: str) -> RegisterResult:
    """
    プロフィール更新（DRY: バリデーション関数を再利用）
    バリデーションロジックを重複なく適用できる。
    """
    errors = [
        err for err in [
            _validate_email(email),
            _validate_username(new_username),
        ]
        if err is not None
    ]
    if errors:
        return RegisterResult(success=False, errors=errors)

    normalized_email = email.strip().lower()
    if normalized_email not in _user_storage:
        return RegisterResult(success=False, errors=["ユーザーが見つかりません"])

    _user_storage[normalized_email]["username"] = new_username.strip()
    return RegisterResult(success=True, user_id=_user_storage[normalized_email]["id"])


# =============================================================================
# 比較デモ実行
# =============================================================================

def run_comparison():
    print("=" * 60)
    print("リファクタリングの旅: バージョン比較デモ")
    print("=" * 60)

    print("\n【バージョン1】原則違反のコード")
    print("-" * 40)
    service_v1 = UserService_v1()
    result = service_v1.register_with_email(
        "alice@example.com", "securepass123", "alice"
    )
    print(f"登録結果: success={result['success']}")
    print(f"問題点:")
    print(f"  - メソッド数: 3個（register_with_google, register_with_twitterは未使用）")
    print(f"  - 未使用フィールド: language, plan, referral_code（YAGNI違反）")
    print(f"  - バリデーションが2か所に重複（DRY違反）")
    print(f"  - 不要なFactoryクラスとInterfaceクラス（KISS違反）")

    print("\n【バージョン2】原則適用後のコード")
    print("-" * 40)
    result2 = register_user("bob@example.com", "securepass456", "bob")
    print(f"登録結果: success={result2.success}, user_id={result2.user_id}")

    # バリデーションエラーのテスト
    result_invalid = register_user("", "short", "a")
    print(f"\nバリデーションエラーテスト:")
    for error in result_invalid.errors:
        print(f"  - {error}")

    # 重複登録テスト
    result_dup = register_user("bob@example.com", "anotherpass", "bob2")
    print(f"\n重複登録テスト: {result_dup.errors}")

    # プロフィール更新（DRYの恩恵: バリデーション関数を再利用）
    result_update = update_user_profile("bob@example.com", "bobby")
    print(f"\nプロフィール更新: success={result_update.success}")

    print("\n改善点まとめ:")
    print("""
  ┌─────────────────┬──────────────────────────┬────────────────────────────┐
  │ 観点             │ バージョン1（原則違反）    │ バージョン2（原則適用）     │
  ├─────────────────┼──────────────────────────┼────────────────────────────┤
  │ KISS             │ 5クラス + 1インターフェース│ 2関数 + 1データクラス       │
  │ DRY              │ バリデーション重複2か所   │ バリデーション関数1か所     │
  │ YAGNI            │ 未使用フィールド3個       │ 必要なフィールドのみ        │
  │                  │ 未使用メソッド2個         │ 必要なメソッドのみ          │
  │ 可読性           │ 5クラスを追う必要あり     │ 1ファイルで理解できる       │
  │ テスト容易性     │ モックが複雑             │ 関数単位でテスト可能        │
  └─────────────────┴──────────────────────────┴────────────────────────────┘
  """)


if __name__ == "__main__":
    run_comparison()
