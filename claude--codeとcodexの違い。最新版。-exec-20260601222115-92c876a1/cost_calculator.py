# PoC品質: 概念実証用スケルトンです。価格は2026年6月時点の公開情報に基づきます。変動の可能性あり。
"""
Claude Code vs Codex CLI コスト試算CLIツール
- トークン数からリアルタイムでコストを計算
- サブスクリプション vs API従量課金の損益分岐点も算出
- 月次利用シナリオ別のコスト比較表を出力
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Optional

# ---- 価格データ（2026年6月時点）-----------------------------------------

@dataclass
class ModelPrice:
    name: str
    provider: str
    input_per_mtok: float         # $/1Mトークン（通常）
    output_per_mtok: float        # $/1Mトークン（通常）
    cache_write_per_mtok: float   # キャッシュ書込
    cache_read_per_mtok: float    # キャッシュ読取（大幅割引）
    batch_discount: float = 0.0  # バッチ割引率（0〜1）


PRICES: dict[str, ModelPrice] = {
    # ---- Anthropic Claude ------------------------------------------------
    "claude-opus-4-7": ModelPrice(
        name="Claude Opus 4.7",
        provider="Anthropic",
        input_per_mtok=5.00,
        output_per_mtok=25.00,
        cache_write_per_mtok=6.25,
        cache_read_per_mtok=0.50,
        batch_discount=0.50,
    ),
    "claude-sonnet-4-6": ModelPrice(
        name="Claude Sonnet 4.6",
        provider="Anthropic",
        input_per_mtok=3.00,
        output_per_mtok=15.00,
        cache_write_per_mtok=3.75,
        cache_read_per_mtok=0.30,
        batch_discount=0.50,
    ),
    "claude-haiku-4-5": ModelPrice(
        name="Claude Haiku 4.5",
        provider="Anthropic",
        input_per_mtok=1.00,
        output_per_mtok=5.00,
        cache_write_per_mtok=1.25,
        cache_read_per_mtok=0.10,
        batch_discount=0.50,
    ),
    # ---- OpenAI / Codex --------------------------------------------------
    "o4-mini": ModelPrice(
        name="o4-mini",
        provider="OpenAI",
        input_per_mtok=1.10,
        output_per_mtok=4.40,
        cache_write_per_mtok=0.00,
        cache_read_per_mtok=0.00,
        batch_discount=0.00,
    ),
    "gpt-5-3-codex": ModelPrice(
        name="GPT-5.3-Codex",
        provider="OpenAI",
        input_per_mtok=1.75,
        output_per_mtok=14.00,
        cache_write_per_mtok=0.00,
        cache_read_per_mtok=0.00,
        batch_discount=0.00,
    ),
    "gpt-5-4": ModelPrice(
        name="GPT-5.4",
        provider="OpenAI",
        input_per_mtok=2.50,
        output_per_mtok=15.00,
        cache_write_per_mtok=0.25,
        cache_read_per_mtok=0.00,
        batch_discount=0.00,
    ),
}

# ---- サブスクリプションプラン -------------------------------------------

@dataclass
class SubscriptionPlan:
    name: str
    provider: str
    monthly_cost: float
    description: str


SUBSCRIPTION_PLANS: list[SubscriptionPlan] = [
    # Claude Code
    SubscriptionPlan("Pro",           "Anthropic", 20,  "Claude Code含む・~44Kトークン/5時間窓"),
    SubscriptionPlan("Max 5x",        "Anthropic", 100, "Proの5倍使用量"),
    SubscriptionPlan("Max 20x",       "Anthropic", 200, "Proの20倍使用量"),
    SubscriptionPlan("Team Premium",  "Anthropic", 100, "チーム向け（1席あたり、最小5席）"),
    # Codex (ChatGPT Plus/Pro)
    SubscriptionPlan("ChatGPT Plus",  "OpenAI",    20,  "Codex CLI含む・~20-100メッセージ/5時間窓"),
    SubscriptionPlan("ChatGPT Pro 5x","OpenAI",    100, "Plusの5倍上限"),
    SubscriptionPlan("ChatGPT Pro 20x","OpenAI",   200, "Plusの20倍上限"),
    SubscriptionPlan("Business",      "OpenAI",    25,  "チーム向け（1席あたり、管理コンソール付き）"),
]

# ---- 月次利用シナリオ ---------------------------------------------------

@dataclass
class UsageScenario:
    label: str
    input_tokens_per_month: int   # トークン数
    output_tokens_per_month: int
    cache_read_ratio: float = 0.0  # キャッシュヒット率（0〜1）
    is_batch: bool = False


SCENARIOS: list[UsageScenario] = [
    UsageScenario("ライト（週数回）",    input_tokens_per_month=3_000_000,  output_tokens_per_month=500_000,   cache_read_ratio=0.3),
    UsageScenario("ミドル（週3〜4日）",  input_tokens_per_month=15_000_000, output_tokens_per_month=2_000_000, cache_read_ratio=0.5),
    UsageScenario("ヘビー（毎日）",      input_tokens_per_month=50_000_000, output_tokens_per_month=8_000_000, cache_read_ratio=0.6),
    UsageScenario("超ヘビー/CI自動化",   input_tokens_per_month=150_000_000,output_tokens_per_month=20_000_000,cache_read_ratio=0.7, is_batch=True),
]

# ---- コスト計算関数 -----------------------------------------------------

def calc_api_cost(
    price: ModelPrice,
    input_tokens: int,
    output_tokens: int,
    cache_read_ratio: float = 0.0,
    is_batch: bool = False,
) -> float:
    """APIコストを計算して返す（USD）"""
    # キャッシュを考慮した実効入力コスト
    cached_input = int(input_tokens * cache_read_ratio)
    normal_input = input_tokens - cached_input

    cost = (
        normal_input  / 1_000_000 * price.input_per_mtok
        + cached_input / 1_000_000 * price.cache_read_per_mtok
        + output_tokens / 1_000_000 * price.output_per_mtok
    )

    if is_batch and price.batch_discount > 0:
        cost *= (1 - price.batch_discount)

    return cost


def calc_breakeven_tokens(price: ModelPrice, subscription_cost: float) -> dict[str, int]:
    """サブスクリプションとAPI直接利用の損益分岐点トークン数を返す"""
    # 出力:入力 = 1:5 と仮定（実測値に近い比率）
    output_ratio = 0.2  # output = input * 0.2
    cache_ratio = 0.5   # 50%キャッシュヒット仮定

    # 1トークンあたりのコスト（入力換算）
    effective_cost_per_input_tok = (
        (1 - cache_ratio) * price.input_per_mtok / 1_000_000
        + cache_ratio * price.cache_read_per_mtok / 1_000_000
        + output_ratio * price.output_per_mtok / 1_000_000
    )

    if effective_cost_per_input_tok <= 0:
        return {"breakeven_input_tokens": -1}

    breakeven = int(subscription_cost / effective_cost_per_input_tok)
    return {"breakeven_input_tokens": breakeven}


# ---- 出力関数 -----------------------------------------------------------

def print_scenario_comparison() -> None:
    """月次シナリオ別コスト比較テーブルを出力"""
    target_models = ["claude-sonnet-4-6", "claude-opus-4-7", "o4-mini", "gpt-5-3-codex"]

    print("\n" + "=" * 90)
    print("月次コスト試算：利用シナリオ別（USD）")
    print("=" * 90)

    col_width = 18
    header = f"{'シナリオ':<22}" + "".join(
        PRICES[m].name[:col_width - 1].ljust(col_width) for m in target_models
    )
    print(header)
    print("-" * 90)

    for scenario in SCENARIOS:
        row = f"{scenario.label:<22}"
        for model_key in target_models:
            price = PRICES[model_key]
            cost = calc_api_cost(
                price,
                scenario.input_tokens_per_month,
                scenario.output_tokens_per_month,
                scenario.cache_read_ratio,
                scenario.is_batch,
            )
            row += f"${cost:>7.2f}{'(batch)' if scenario.is_batch and price.batch_discount > 0 else '       '}"[:col_width].ljust(col_width)
        print(row)

    print("=" * 90)
    print("※ キャッシュヒット率はシナリオ別に設定（ライト:30%、ミドル:50%、ヘビー:60%、超ヘビー:70%）")
    print("※ Claude は batch_discount=50%、OpenAI は非適用")


def print_breakeven_analysis() -> None:
    """損益分岐点分析を出力"""
    print("\n" + "=" * 70)
    print("損益分岐点分析：サブスクリプション vs API直接利用")
    print("（月あたりこのトークン数を超えるとAPIの方が割高になる）")
    print("=" * 70)

    checks = [
        ("claude-sonnet-4-6", "Pro ($20/月)",      20),
        ("claude-sonnet-4-6", "Max 5x ($100/月)",  100),
        ("claude-sonnet-4-6", "Max 20x ($200/月)", 200),
        ("o4-mini",           "Plus ($20/月)",      20),
        ("o4-mini",           "Pro ($100/月)",     100),
    ]

    for model_key, plan_name, plan_cost in checks:
        price = PRICES[model_key]
        result = calc_breakeven_tokens(price, plan_cost)
        be = result["breakeven_input_tokens"]
        print(f"  {price.name:<22} × {plan_name:<20} → 損益分岐: {be / 1_000_000:.1f}M トークン/月")

    print("=" * 70)
    print("※ キャッシュヒット50%・出力:入力=1:5 を仮定")


def print_single_calc(model_key: str, input_tokens: int, output_tokens: int,
                      cache_read_ratio: float = 0.0, is_batch: bool = False) -> None:
    """単一モデルのコストを計算して出力"""
    if model_key not in PRICES:
        print(f"[ERROR] モデルキーが見つかりません: {model_key}")
        print(f"利用可能: {', '.join(PRICES.keys())}")
        return

    price = PRICES[model_key]
    cost = calc_api_cost(price, input_tokens, output_tokens, cache_read_ratio, is_batch)

    print("\n" + "=" * 50)
    print(f"コスト試算: {price.name}")
    print("=" * 50)
    print(f"  入力トークン  : {input_tokens:>12,}")
    print(f"  出力トークン  : {output_tokens:>12,}")
    print(f"  キャッシュ率  : {cache_read_ratio * 100:.0f}%")
    print(f"  バッチ割引    : {'あり' if is_batch and price.batch_discount > 0 else 'なし'}")
    print(f"  推定コスト    : ${cost:.4f}")
    print("=" * 50)


# ---- CLI エントリポイント -----------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Claude Code / Codex コスト試算ツール (PoC)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--model",         default=None, help=f"モデルキー（省略時は全シナリオ比較）\n選択肢: {', '.join(PRICES.keys())}")
    parser.add_argument("--input-tokens",  type=int, default=5_000_000, help="入力トークン数（デフォルト: 5M）")
    parser.add_argument("--output-tokens", type=int, default=1_000_000, help="出力トークン数（デフォルト: 1M）")
    parser.add_argument("--cache-ratio",   type=float, default=0.5, help="キャッシュヒット率 0〜1（デフォルト: 0.5）")
    parser.add_argument("--batch",         action="store_true", help="バッチ割引を適用（Claudeのみ有効）")
    args = parser.parse_args()

    if args.model:
        print_single_calc(
            model_key=args.model,
            input_tokens=args.input_tokens,
            output_tokens=args.output_tokens,
            cache_read_ratio=args.cache_ratio,
            is_batch=args.batch,
        )
    else:
        print_scenario_comparison()
        print_breakeven_analysis()

        print("\n【サブスクリプションプラン一覧】")
        print("-" * 60)
        for p in SUBSCRIPTION_PLANS:
            print(f"  [{p.provider:>10}] {p.name:<20} ${p.monthly_cost:>5}/月  {p.description}")


if __name__ == "__main__":
    main()
