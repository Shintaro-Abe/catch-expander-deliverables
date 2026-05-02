# PoC品質 - このコードは概念実証用です。本番環境での利用前に十分なテストと改修を行ってください。
"""
エラーハンドリング（Error Handler）モジュール

AIエージェントハーネスのリトライ・フォールバック・サーキットブレーカーを実装します。

【推奨レイヤード設計】
リクエスト入力
  └─ [1] リトライ（指数バックオフ + ジッター）    ← 一時エラー
       └─ [2] フォールバック（代替モデル/プロバイダー） ← リトライ枯渇
            └─ [3] サーキットブレーカー            ← 持続的障害
                 └─ [4] 人間エスカレーション / 最終フォールバックレスポンス

【初学者向け補足】
- リトライ: 「少し待ってもう一度試す」 → 一時的な通信障害に有効
- フォールバック: 「別の手段に切り替える」 → サービス障害時に継続可能
- サーキットブレーカー: 「電気回路のブレーカー」のように障害時に自動遮断し、
  無駄なリトライの嵐（thundering herd）を防ぐ
"""

from __future__ import annotations

import enum
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")  # 汎用型変数


# --------------------------------------------------------------------------- #
# エラー分類
# --------------------------------------------------------------------------- #

class ErrorKind(enum.Enum):
    """
    エラーの種別分類。

    TRANSIENT（一時的エラー）: リトライで回復する可能性がある
    PERMANENT（永続的エラー）: コードまたは設定の修正が必要
    CONTEXT_OVERFLOW        : プロンプトの短縮・分割が必要
    UNKNOWN                 : 未分類のエラー
    """
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    CONTEXT_OVERFLOW = "context_overflow"
    UNKNOWN = "unknown"


# HTTP ステータスコードとエラー種別のマッピング
HTTP_ERROR_MAP: Dict[int, ErrorKind] = {
    400: ErrorKind.PERMANENT,
    401: ErrorKind.PERMANENT,
    403: ErrorKind.PERMANENT,
    404: ErrorKind.PERMANENT,
    429: ErrorKind.TRANSIENT,   # レート制限 → リトライ対象
    500: ErrorKind.TRANSIENT,   # サーバーエラー → リトライ対象
    502: ErrorKind.TRANSIENT,
    503: ErrorKind.TRANSIENT,
    504: ErrorKind.TRANSIENT,
}


def classify_error(exc: Exception, http_status: Optional[int] = None) -> ErrorKind:
    """
    例外とHTTPステータスコードからエラー種別を分類する。

    Args:
        exc        : 発生した例外
        http_status: HTTPレスポンスのステータスコード（ある場合）
    Returns:
        ErrorKind 列挙値
    """
    if http_status is not None:
        if http_status == 400 and "context" in str(exc).lower():
            return ErrorKind.CONTEXT_OVERFLOW
        return HTTP_ERROR_MAP.get(http_status, ErrorKind.UNKNOWN)

    # 例外メッセージからの推測（簡易実装）
    msg = str(exc).lower()
    if any(kw in msg for kw in ["rate limit", "throttl", "too many"]):
        return ErrorKind.TRANSIENT
    if any(kw in msg for kw in ["context length", "token limit", "max_tokens"]):
        return ErrorKind.CONTEXT_OVERFLOW
    if any(kw in msg for kw in ["auth", "credential", "permission", "forbidden"]):
        return ErrorKind.PERMANENT
    return ErrorKind.UNKNOWN


# --------------------------------------------------------------------------- #
# リトライ戦略（指数バックオフ + ジッター）
# --------------------------------------------------------------------------- #

@dataclass
class RetryConfig:
    """
    リトライの設定パラメータ。

    Attributes:
        max_attempts  : 最大試行回数（初回試行を含む）
        base_delay_sec: 最初の待機秒数
        max_delay_sec : 最大待機秒数（指数増加の上限）
        jitter        : True の場合ランダムジッターを追加（thundering herd 防止）
    """
    max_attempts: int = 5
    base_delay_sec: float = 1.0
    max_delay_sec: float = 60.0
    jitter: bool = True


def compute_backoff(attempt: int, config: RetryConfig) -> float:
    """
    指数バックオフ + ジッターで待機時間を計算する。

    計算式: min(base * 2^attempt, max) + random(0, jitter_range)

    Args:
        attempt: 0-indexed の試行回数（0=初回失敗後）
    Returns:
        待機秒数
    """
    delay = min(config.base_delay_sec * (2 ** attempt), config.max_delay_sec)
    if config.jitter:
        # Full Jitter: [0, delay] のランダム値を加算
        delay = delay * random.random()
    return delay


def with_retry(
    func: Callable[[], T],
    config: Optional[RetryConfig] = None,
    retryable_errors: Optional[Tuple[type, ...]] = None,
) -> T:
    """
    指定された関数をリトライ付きで実行する。

    一時的エラー（TRANSIENT）の場合のみリトライし、
    永続的エラー（PERMANENT）は即座に例外を再送出します。

    Args:
        func            : 実行する関数（引数なし callable）
        config          : リトライ設定（None の場合デフォルト値を使用）
        retryable_errors: リトライ対象の例外クラスタプル

    Raises:
        Exception: max_attempts 回試行してもすべて失敗した場合

    使い方:
        result = with_retry(lambda: api_client.call(prompt), config=RetryConfig(max_attempts=3))
    """
    cfg = config or RetryConfig()
    retryable = retryable_errors or (Exception,)

    last_exc: Optional[Exception] = None
    for attempt in range(cfg.max_attempts):
        try:
            return func()
        except retryable as exc:
            last_exc = exc
            error_kind = classify_error(exc)

            if error_kind == ErrorKind.PERMANENT:
                logger.error("永続的エラーのためリトライ不可: %s", exc)
                raise

            if attempt < cfg.max_attempts - 1:
                wait = compute_backoff(attempt, cfg)
                logger.warning(
                    "試行 %d/%d 失敗 (%s)。%.2f 秒後にリトライ: %s",
                    attempt + 1,
                    cfg.max_attempts,
                    error_kind.value,
                    wait,
                    exc,
                )
                time.sleep(wait)
            else:
                logger.error("最大試行回数 (%d) に達しました: %s", cfg.max_attempts, exc)

    assert last_exc is not None  # 型チェッカー向け
    raise last_exc


# --------------------------------------------------------------------------- #
# フォールバック戦略（プロバイダーチェーン）
# --------------------------------------------------------------------------- #

@dataclass
class FallbackProvider:
    """
    フォールバック先プロバイダーの定義。

    Attributes:
        name    : プロバイダー名（ログ・デバッグ用）
        func    : 呼び出す関数
        priority: 小さいほど優先度が高い（0 = プライマリ）
    """
    name: str
    func: Callable[[], Any]
    priority: int = 0


def with_fallback(
    providers: List[FallbackProvider],
    retry_config: Optional[RetryConfig] = None,
) -> Any:
    """
    プロバイダーチェーンを使ったフォールバック付き実行。

    Primary → Fallback 1 → Fallback 2 の順で試行します。
    各プロバイダーに対してリトライを実施し、すべて枯渇した場合のみ次へ移行します。

    注意: 認証失敗・不正リクエスト（PERMANENT エラー）ではフォールバックしません。
    同一の失敗を繰り返すだけのため、即座に例外を送出します。

    Args:
        providers    : 優先度順にソートされたプロバイダーリスト
        retry_config : 各プロバイダーに適用するリトライ設定

    Raises:
        Exception: すべてのプロバイダーが失敗した場合
    """
    sorted_providers = sorted(providers, key=lambda p: p.priority)
    last_exc: Optional[Exception] = None

    for provider in sorted_providers:
        try:
            logger.info("プロバイダー試行: %s", provider.name)
            result = with_retry(provider.func, config=retry_config)
            if provider.priority > 0:
                logger.warning("フォールバック成功: %s", provider.name)
            return result
        except Exception as exc:
            error_kind = classify_error(exc)
            if error_kind == ErrorKind.PERMANENT:
                logger.error("永続的エラー。フォールバック中止: %s", exc)
                raise
            last_exc = exc
            logger.warning("プロバイダー %s が失敗。次へフォールバック: %s", provider.name, exc)

    assert last_exc is not None
    raise last_exc


# --------------------------------------------------------------------------- #
# サーキットブレーカー
# --------------------------------------------------------------------------- #

class CircuitState(enum.Enum):
    """
    サーキットブレーカーの状態。

    CLOSED  : 通常動作。リクエストを通す。
    OPEN    : 障害検知。リクエストをブロック（即座に失敗）。
    HALF_OPEN: 回復テスト中。1リクエストのみ通して成否を確認。
    """
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """
    サーキットブレーカーの設定パラメータ。

    Attributes:
        failure_threshold    : CLOSED → OPEN に遷移する連続失敗回数
        success_threshold    : HALF_OPEN → CLOSED に戻る連続成功回数
        open_duration_sec    : OPEN 状態を維持する秒数（経過後 HALF_OPEN へ）
        rolling_window_sec   : 失敗率を計算するローリングウィンドウ（秒）
    """
    failure_threshold: int = 5
    success_threshold: int = 2
    open_duration_sec: float = 30.0
    rolling_window_sec: float = 60.0


class CircuitBreakerOpenError(Exception):
    """サーキットブレーカーが OPEN 状態のときに発生する例外。"""
    pass


class CircuitBreaker:
    """
    個別障害はリトライで対処し、システム障害はサーキットブレーカーで対処します。
    （リトライとサーキットブレーカーは補完関係にあります）

    動作:
    1. 通常時（CLOSED）: 全リクエストを通過させ、失敗をカウント
    2. 失敗閾値到達（OPEN）: リクエストをブロックし即座に失敗を返す
    3. 一定時間後（HALF_OPEN）: 1リクエストのみ通してテスト
    4. テスト成功（CLOSED に戻る）: 通常動作を再開
    """

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0.0
        self._failure_timestamps: List[float] = []

    @property
    def state(self) -> CircuitState:
        """現在のサーキットブレーカー状態（副作用: OPEN → HALF_OPEN 遷移を確認）。"""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self.config.open_duration_sec:
                logger.info("[%s] OPEN → HALF_OPEN 遷移（経過時間: %.1f秒）", self.name, elapsed)
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
        return self._state

    def call(self, func: Callable[[], T]) -> T:
        """
        サーキットブレーカーを通じて関数を実行する。

        Args:
            func: 実行する関数
        Raises:
            CircuitBreakerOpenError: OPEN 状態でリクエストをブロックした場合
        """
        current_state = self.state

        if current_state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(
                f"サーキットブレーカー [{self.name}] が OPEN 状態です。"
                f"リクエストをブロックします。"
            )

        try:
            result = func()
            self._on_success()
            return result
        except CircuitBreakerOpenError:
            raise
        except Exception as exc:
            self._on_failure()
            raise exc

    def _on_success(self) -> None:
        """成功時のカウンター更新。"""
        now = time.time()
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.config.success_threshold:
                logger.info("[%s] HALF_OPEN → CLOSED 遷移（成功 %d 回）", self.name, self._success_count)
                self._reset()
        else:
            # CLOSED 状態では失敗ウィンドウをリセット
            self._failure_timestamps = [
                t for t in self._failure_timestamps
                if now - t < self.config.rolling_window_sec
            ]

    def _on_failure(self) -> None:
        """失敗時のカウンター更新と状態遷移チェック。"""
        now = time.time()
        self._last_failure_time = now
        self._failure_timestamps.append(now)

        # ローリングウィンドウ外の古い失敗を除外
        self._failure_timestamps = [
            t for t in self._failure_timestamps
            if now - t < self.config.rolling_window_sec
        ]

        recent_failures = len(self._failure_timestamps)

        if self._state == CircuitState.HALF_OPEN:
            logger.warning("[%s] HALF_OPEN → OPEN 遷移（テスト失敗）", self.name)
            self._state = CircuitState.OPEN
        elif recent_failures >= self.config.failure_threshold:
            logger.error(
                "[%s] CLOSED → OPEN 遷移（%d 回失敗 / ウィンドウ内）",
                self.name,
                recent_failures,
            )
            self._state = CircuitState.OPEN

    def _reset(self) -> None:
        """サーキットブレーカーを初期状態（CLOSED）にリセットする。"""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._failure_timestamps.clear()

    def get_status(self) -> Dict[str, Any]:
        """デバッグ・監視用のステータス情報を返す。"""
        return {
            "name": self.name,
            "state": self.state.value,
            "recent_failures": len(self._failure_timestamps),
            "last_failure_time": self._last_failure_time,
        }


# --------------------------------------------------------------------------- #
# 統合エラーハンドラー（リトライ + フォールバック + サーキットブレーカー）
# --------------------------------------------------------------------------- #

class AgentErrorHandler:
    """
    エージェントハーネス用の統合エラーハンドラー。

    リトライ・フォールバック・サーキットブレーカーを組み合わせた
    レイヤード設計を1クラスで提供します。

    使い方:
        handler = AgentErrorHandler()
        result = handler.execute_with_protection(
            primary_func=lambda: call_claude_opus(prompt),
            fallback_funcs=[
                ("claude-sonnet", lambda: call_claude_sonnet(prompt)),
                ("claude-haiku",  lambda: call_claude_haiku(prompt)),
            ],
        )
    """

    def __init__(
        self,
        retry_config: Optional[RetryConfig] = None,
        circuit_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        self.retry_config = retry_config or RetryConfig()
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._circuit_config = circuit_config or CircuitBreakerConfig()

    def _get_circuit(self, name: str) -> CircuitBreaker:
        """名前でサーキットブレーカーを取得または新規作成する。"""
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = CircuitBreaker(name, self._circuit_config)
        return self._circuit_breakers[name]

    def execute_with_protection(
        self,
        primary_func: Callable[[], T],
        fallback_funcs: Optional[List[Tuple[str, Callable[[], T]]]] = None,
        provider_name: str = "primary",
    ) -> T:
        """
        保護されたエージェント呼び出しを実行する。

        レイヤード保護:
        1. サーキットブレーカー（OPEN 時は即座に失敗）
        2. リトライ（指数バックオフ + ジッター）
        3. フォールバック（代替プロバイダー）

        Args:
            primary_func    : プライマリ呼び出し関数
            fallback_funcs  : (名前, 関数) のリスト（優先度順）
            provider_name   : サーキットブレーカーの識別名
        """
        providers: List[FallbackProvider] = [
            FallbackProvider(name=provider_name, func=primary_func, priority=0)
        ]
        for i, (name, func) in enumerate(fallback_funcs or [], start=1):
            providers.append(FallbackProvider(name=name, func=func, priority=i))

        def _wrapped_call(provider: FallbackProvider) -> T:
            circuit = self._get_circuit(provider.name)
            return circuit.call(lambda: with_retry(provider.func, self.retry_config))

        last_exc: Optional[Exception] = None
        for provider in sorted(providers, key=lambda p: p.priority):
            try:
                return _wrapped_call(provider)
            except CircuitBreakerOpenError as exc:
                logger.warning("サーキットブレーカー OPEN: %s → 次プロバイダーへ", provider.name)
                last_exc = exc
            except Exception as exc:
                if classify_error(exc) == ErrorKind.PERMANENT:
                    raise
                last_exc = exc
                logger.warning("プロバイダー %s 失敗 → 次へ: %s", provider.name, exc)

        assert last_exc is not None
        raise last_exc

    def get_all_circuit_status(self) -> List[Dict[str, Any]]:
        """全サーキットブレーカーのステータスを返す（監視・デバッグ用）。"""
        return [cb.get_status() for cb in self._circuit_breakers.values()]


# --------------------------------------------------------------------------- #
# 動作確認エントリポイント
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("=== エラーハンドリング動作確認 ===\n")

    # 1. リトライのデモ（意図的に3回失敗させてから成功）
    print("[1] リトライデモ（3回失敗後に成功）:")
    call_count = 0

    def flaky_api() -> str:
        global call_count
        call_count += 1
        if call_count <= 3:
            raise ConnectionError(f"一時的な接続エラー（{call_count}回目）")
        return "API 成功！"

    try:
        config = RetryConfig(max_attempts=5, base_delay_sec=0.1, max_delay_sec=1.0)
        result = with_retry(flaky_api, config=config)
        print(f"  結果: {result} （{call_count}回試行）")
    except Exception as e:
        print(f"  失敗: {e}")

    # 2. フォールバックのデモ
    print("\n[2] フォールバックデモ:")
    call_count = 0

    def primary_model() -> str:
        raise ConnectionError("プライマリモデル障害")

    def fallback_model() -> str:
        return "フォールバックモデルの応答"

    providers = [
        FallbackProvider("claude-opus", primary_model, priority=0),
        FallbackProvider("claude-sonnet", fallback_model, priority=1),
    ]
    try:
        fb_config = RetryConfig(max_attempts=2, base_delay_sec=0.05)
        result = with_fallback(providers, retry_config=fb_config)
        print(f"  結果: {result}")
    except Exception as e:
        print(f"  失敗: {e}")

    # 3. サーキットブレーカーのデモ
    print("\n[3] サーキットブレーカーデモ:")
    cb = CircuitBreaker("test-service", CircuitBreakerConfig(failure_threshold=3, open_duration_sec=2.0))

    def always_fail() -> str:
        raise RuntimeError("サービス障害")

    # 3回失敗させてサーキットブレーカーを OPEN にする
    for i in range(3):
        try:
            cb.call(always_fail)
        except RuntimeError:
            pass

    print(f"  状態: {cb.get_status()['state']}")  # open になるはず

    # OPEN 中のリクエストはブロックされる
    try:
        cb.call(lambda: "これは実行されない")
    except CircuitBreakerOpenError as e:
        print(f"  ブロック確認: {e}")

    # 4. エラー分類のデモ
    print("\n[4] エラー分類:")
    test_cases = [
        (ConnectionError("rate limit exceeded"), None),
        (PermissionError("auth failed"), None),
        (ValueError("context length exceeded"), 400),
        (RuntimeError("server error"), 500),
    ]
    for exc, status in test_cases:
        kind = classify_error(exc, status)
        print(f"  {type(exc).__name__}({str(exc)[:30]}) → {kind.value}")
