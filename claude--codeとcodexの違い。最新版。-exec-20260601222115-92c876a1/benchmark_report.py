# PoC品質: 概念実証用スケルトンです。ベンチマーク数値は2026年6月時点の公開情報に基づきます。
"""
Claude Code vs Codex CLI ベンチマークレポート生成ツール
- 2026年6月最新のSWE-bench / Terminal-Bench / OSWorld データを表形式で出力
- richライブラリがあればカラー表示、なければプレーンテキストにフォールバック
"""

from dataclasses import dataclass, field
from typing import Optional

# richがない環境でも動くようにフォールバック
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ---- データ定義 ----------------------------------------------------------

@dataclass
class BenchmarkEntry:
    model: str
    provider: str
    swe_bench_verified: Optional[float]  # パーセント
    swe_bench_pro: Optional[float]
    terminal_bench: Optional[float]
    osworld: Optional[float]
    context_window_k: int                # Kトークン
    note: str = ""


BENCHMARK_DATA: list[BenchmarkEntry] = [
    # ---- Claude系 --------------------------------------------------------
    BenchmarkEntry(
        model="Claude Opus 4.7",
        provider="Anthropic",
        swe_bench_verified=87.6,
        swe_bench_pro=64.3,
        terminal_bench=None,
        osworld=None,
        context_window_k=1_000,
        note="2026年フラッグシップ・最高精度",
    ),
    BenchmarkEntry(
        model="Claude Sonnet 4.6",
        provider="Anthropic",
        swe_bench_verified=79.6,
        swe_bench_pro=None,
        terminal_bench=59.1,
        osworld=72.5,
        context_window_k=1_000,
        note="Claude Code 推奨モデル・コスパ◎",
    ),
    BenchmarkEntry(
        model="Claude Sonnet 4.5",
        provider="Anthropic",
        swe_bench_verified=77.2,
        swe_bench_pro=43.6,
        terminal_bench=None,
        osworld=None,
        context_window_k=200,
        note="旧世代・2026/06/15 廃止予定",
    ),
    # ---- OpenAI Codex系 --------------------------------------------------
    BenchmarkEntry(
        model="GPT-5.3-Codex",
        provider="OpenAI",
        swe_bench_verified=80.0,
        swe_bench_pro=56.8,
        terminal_bench=77.3,
        osworld=64.7,
        context_window_k=400,
        note="Codex CLI 2026年前半デフォルト・25%高速化",
    ),
    BenchmarkEntry(
        model="GPT-5.2-Codex",
        provider="OpenAI",
        swe_bench_verified=80.0,
        swe_bench_pro=55.6,
        terminal_bench=None,
        osworld=None,
        context_window_k=400,
        note="GPT-5.3-Codexの前世代",
    ),
    BenchmarkEntry(
        model="GPT-5.4",
        provider="OpenAI",
        swe_bench_verified=None,
        swe_bench_pro=57.7,
        terminal_bench=None,
        osworld=None,
        context_window_k=1_000,
        note="1Mコンテキスト対応",
    ),
    BenchmarkEntry(
        model="o4-mini",
        provider="OpenAI",
        swe_bench_verified=68.1,
        swe_bench_pro=None,
        terminal_bench=None,
        osworld=None,
        context_window_k=192,
        note="低コスト・高速・CI/CD向き",
    ),
    BenchmarkEntry(
        model="o3",
        provider="OpenAI",
        swe_bench_verified=69.1,
        swe_bench_pro=None,
        terminal_bench=None,
        osworld=None,
        context_window_k=200,
        note="Codex CLI 初期搭載モデル",
    ),
]


@dataclass
class AgentSystemEntry:
    agent: str
    base_model: str
    swe_bench_pro: float
    note: str = ""


AGENT_SYSTEM_DATA: list[AgentSystemEntry] = [
    AgentSystemEntry("GPT-5.3-Codex CLI", "GPT-5.3-Codex", 57.0, "Codex CLI スキャフォールド"),
    AgentSystemEntry("Claude Code",        "Opus 4.5",       55.4, "Claude Code エージェント"),
    AgentSystemEntry("Cursor",             "Opus 4.5",       50.2, "同モデルでもスキャフォールドで5%差"),
]


# ---- 出力関数 ------------------------------------------------------------

def _fmt(val: Optional[float], suffix: str = "%") -> str:
    return f"{val:.1f}{suffix}" if val is not None else "—"


def print_rich_report() -> None:
    console = Console()

    console.print(Panel.fit(
        "[bold cyan]Claude Code vs OpenAI Codex CLI[/bold cyan]\n"
        "[dim]ベンチマーク比較レポート（2026年6月時点）[/dim]",
        box=box.DOUBLE_EDGE,
    ))

    # ---- ベンチマーク比較テーブル ----------------------------------------
    table = Table(
        title="モデル別ベンチマーク比較",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("モデル",             style="bold", width=20)
    table.add_column("提供元",             style="cyan",  width=12)
    table.add_column("SWE-bench\nVerified", justify="right", width=12)
    table.add_column("SWE-bench\nPro",      justify="right", width=11)
    table.add_column("Terminal\nBench 2.0", justify="right", width=12)
    table.add_column("OSWorld\nVerified",   justify="right", width=11)
    table.add_column("Context\n(Kトークン)", justify="right", width=12)
    table.add_column("備考",               width=28)

    for e in BENCHMARK_DATA:
        color = "green" if e.provider == "Anthropic" else "yellow"
        table.add_row(
            f"[{color}]{e.model}[/{color}]",
            e.provider,
            _fmt(e.swe_bench_verified),
            _fmt(e.swe_bench_pro),
            _fmt(e.terminal_bench),
            _fmt(e.osworld),
            f"{e.context_window_k:,}K",
            e.note,
        )
    console.print(table)

    # ---- エージェントシステム比較 ----------------------------------------
    agent_table = Table(
        title="エージェントシステム別 SWE-bench Pro（同一モデルでもスキャフォールドで差が出る）",
        box=box.ROUNDED,
    )
    agent_table.add_column("エージェント", style="bold", width=22)
    agent_table.add_column("ベースモデル",               width=18)
    agent_table.add_column("SWE-bench Pro", justify="right", width=14)
    agent_table.add_column("備考",                        width=35)

    for a in AGENT_SYSTEM_DATA:
        agent_table.add_row(a.agent, a.base_model, _fmt(a.swe_bench_pro), a.note)
    console.print(agent_table)

    # ---- インサイト -------------------------------------------------------
    console.print(Panel(
        "[bold]主要インサイト[/bold]\n\n"
        "• [green]SWE-bench Verified[/green]: Claude Opus 4.7 が 87.6% で最高。"
        "GPT-5.3-Codex と Sonnet 4.6 は ~80% でほぼ同等\n"
        "• [yellow]ターミナルデバッグ[/yellow]: GPT-5.3-Codex が 77.3% でClaude Sonnet 4.6 の 59.1% を上回る\n"
        "• [cyan]UI操作[/cyan]: Claude Sonnet 4.6 が OSWorld 72.5% でGPT-5.3-Codex の 64.7% を上回る\n"
        "• [magenta]スキャフォールド効果[/magenta]: 同一モデル（Opus 4.5）でもエージェントにより最大5%のスコア差\n"
        "• [red]飽和ベンチマーク[/red]: HumanEval は両者とも95%以上で差別化不能。SWE-bench Pro が実用指標",
        title="考察",
        box=box.ROUNDED,
    ))


def print_plain_report() -> None:
    """richなし環境向けのプレーンテキスト出力"""
    print("\n" + "=" * 80)
    print("Claude Code vs OpenAI Codex CLI ベンチマークレポート（2026年6月）")
    print("=" * 80)

    headers = ["モデル", "提供元", "SWE-Verified", "SWE-Pro", "Terminal", "OSWorld", "Context"]
    widths  = [22, 12, 13, 10, 10, 9, 10]
    header_line = "".join(h.ljust(w) for h, w in zip(headers, widths))
    print(header_line)
    print("-" * 80)

    for e in BENCHMARK_DATA:
        row = [
            e.model[:21],
            e.provider[:11],
            _fmt(e.swe_bench_verified),
            _fmt(e.swe_bench_pro),
            _fmt(e.terminal_bench),
            _fmt(e.osworld),
            f"{e.context_window_k}K",
        ]
        print("".join(v.ljust(w) for v, w in zip(row, widths)))

    print("\n【エージェントシステム別 SWE-bench Pro】")
    print("-" * 50)
    for a in AGENT_SYSTEM_DATA:
        print(f"  {a.agent:<22} {a.base_model:<18} {_fmt(a.swe_bench_pro)}")
    print("=" * 80)


# ---- エントリポイント ---------------------------------------------------

def main() -> None:
    if HAS_RICH:
        print_rich_report()
    else:
        print("[INFO] richライブラリが未インストールのためプレーンテキスト出力します")
        print("[INFO] pip install rich でカラー表示が有効になります")
        print_plain_report()


if __name__ == "__main__":
    main()
