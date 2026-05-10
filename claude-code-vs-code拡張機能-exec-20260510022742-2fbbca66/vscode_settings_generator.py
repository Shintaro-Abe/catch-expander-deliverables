# PoC品質: このコードは概念実証(Proof of Concept)として提供されています。
# 本番環境での使用には追加の検証と調整が必要です。

"""
Claude Code VS Code拡張機能 — settings.json 設定ファイルジェネレーター

Claude Code の設定ファイル(.claude/settings.json)を対話的に生成するツールです。

【設定ファイルの優先度（高い順）】
1. MDM管理設定 (IT管理者用)
2. .claude/settings.local.json (個人用・Gitにコミットしない)
3. .claude/settings.json       (プロジェクト共有・Gitにコミット)
4. ~/.claude/settings.json     (ユーザー全体設定)
"""

import json
import os
from pathlib import Path
from typing import Any


# ==============================
# 設定テンプレート定義
# ==============================

# 利用可能なClaudeモデル（2026年5月時点）
AVAILABLE_MODELS = {
    "claude-sonnet-4-6": "推奨: 速度と性能のバランスが最良、Extended Thinking対応",
    "claude-opus-4-7": "最高性能モデル、Adaptive Thinking対応（コスト高）",
    "claude-haiku-4-5-20251001": "高速・低コスト、シンプルなタスク向け",
}

# 権限モード説明
PERMISSION_MODES = {
    "default": "アクションごとに都度確認（最も安全・デフォルト）",
    "plan": "実行前に計画をMarkdownで提示し、承認後に実行",
    "acceptEdits": "ファイル編集は自動承認（コマンド実行は確認あり）",
    "bypassPermissions": "全操作を即時実行（サンドボックス環境専用・危険）",
}

# よく使う許可コマンドのプリセット
ALLOW_PRESETS = {
    "nodejs": [
        "Bash(npm run lint)",
        "Bash(npm run test *)",
        "Bash(npm run build)",
        "Bash(npm install *)",
        "Bash(npx *)",
    ],
    "python": [
        "Bash(python -m pytest *)",
        "Bash(pip install *)",
        "Bash(ruff check *)",
        "Bash(mypy *)",
        "Bash(black *)",
    ],
    "git": [
        "Bash(git status)",
        "Bash(git diff *)",
        "Bash(git add *)",
        "Bash(git commit *)",
        "Bash(git log *)",
    ],
    "docker": [
        "Bash(docker build *)",
        "Bash(docker-compose up *)",
        "Bash(docker ps)",
    ],
}

# 絶対に拒否すべき危険コマンド（セキュリティのベストプラクティス）
DENY_PRESETS = {
    "dangerous": [
        "Bash(rm -rf *)",       # 再帰的削除（危険）
        "Bash(sudo *)",          # 管理者権限コマンド
        "Bash(chmod 777 *)",     # 全権限付与
        "Bash(curl * | bash)",   # ダウンロード即実行（インジェクションリスク）
        "Read(.env)",            # 環境変数ファイルの読み取り防止
        "Read(./secrets/**)",    # シークレットディレクトリの読み取り防止
    ],
}


class SettingsGenerator:
    """
    Claude Code settings.json を生成・管理するクラス。

    VS Code拡張での設定UIに相当する操作をPythonコードで実現します。
    """

    def __init__(self):
        self.config: dict[str, Any] = {
            "$schema": "https://json.schemastore.org/claude-code-settings.json",
        }

    def set_model(self, model: str = "claude-sonnet-4-6") -> "SettingsGenerator":
        """使用するClaudeモデルを設定します。"""
        if model not in AVAILABLE_MODELS:
            available = ", ".join(AVAILABLE_MODELS.keys())
            raise ValueError(f"無効なモデルです。利用可能: {available}")
        self.config["model"] = model
        return self  # メソッドチェーンを可能にする

    def set_permission_mode(self, mode: str = "default") -> "SettingsGenerator":
        """
        デフォルトのパーミッションモードを設定します。

        【モード別の安全性】
        - default: 最も安全（推奨）
        - plan: 安全（実行前確認あり）
        - acceptEdits: 中程度（ファイル編集は自動）
        - bypassPermissions: 危険（確認なし・本番環境禁止）
        """
        if mode not in PERMISSION_MODES:
            available = ", ".join(PERMISSION_MODES.keys())
            raise ValueError(f"無効なモードです。利用可能: {available}")

        if "permissions" not in self.config:
            self.config["permissions"] = {}
        self.config["permissions"]["defaultMode"] = mode
        return self

    def allow_commands(self, commands: list[str]) -> "SettingsGenerator":
        """
        実行を許可するコマンドパターンを追加します。
        ワイルドカード(*) が使えます（例: "Bash(npm *)"）。
        """
        if "permissions" not in self.config:
            self.config["permissions"] = {}
        existing = self.config["permissions"].get("allow", [])
        # 重複を避けて追加
        self.config["permissions"]["allow"] = list(set(existing + commands))
        return self

    def deny_commands(self, commands: list[str]) -> "SettingsGenerator":
        """
        実行を禁止するコマンドパターンを追加します。
        denyはallowより優先されます（安全側に倒す設計）。
        """
        if "permissions" not in self.config:
            self.config["permissions"] = {}
        existing = self.config["permissions"].get("deny", [])
        self.config["permissions"]["deny"] = list(set(existing + commands))
        return self

    def add_hook(
        self,
        event: str,
        matcher: str,
        command: str,
    ) -> "SettingsGenerator":
        """
        フック（自動実行コマンド）を追加します。

        VS Code拡張のフック機能: ファイル編集後に自動でLint/Formatを実行するなど。

        Args:
            event: フックイベント。
                   "PreToolUse"  — ツール実行前
                   "PostToolUse" — ツール実行後（最も一般的）
                   "Stop"        — Claudeが応答を停止した時
            matcher: 対象ツール名（例: "Edit", "Bash", "Write"）。
            command: 実行するシェルコマンド。
                     $FILE 変数で編集されたファイルパスを参照可能。

        使用例:
            # ファイル編集後に自動でESLintを実行
            generator.add_hook("PostToolUse", "Edit", "eslint $FILE --fix")

            # コミット前にテストを実行
            generator.add_hook("PreToolUse", "Bash(git commit *)", "npm test")
        """
        if "hooks" not in self.config:
            self.config["hooks"] = {}
        if event not in self.config["hooks"]:
            self.config["hooks"][event] = []

        self.config["hooks"][event].append({
            "matcher": matcher,
            "hooks": [{"type": "command", "command": command}],
        })
        return self

    def set_env(self, key: str, value: str) -> "SettingsGenerator":
        """
        環境変数を設定します。

        【重要】APIキーなどの機密情報は settings.local.json にのみ記載し、
        settings.json（Git共有）には絶対に書かないこと。
        """
        if "env" not in self.config:
            self.config["env"] = {}
        self.config["env"][key] = value
        return self

    def apply_preset(self, preset_type: str) -> "SettingsGenerator":
        """
        用途別のプリセット設定を適用します。

        Args:
            preset_type: "nodejs" | "python" | "git" | "docker" | "secure"
        """
        if preset_type in ALLOW_PRESETS:
            self.allow_commands(ALLOW_PRESETS[preset_type])
        if preset_type == "secure":
            self.deny_commands(DENY_PRESETS["dangerous"])
        return self

    def build(self) -> dict:
        """設定辞書を返します。"""
        return self.config

    def save(self, output_path: str = ".claude/settings.json") -> str:
        """
        設定ファイルを指定パスに書き出します。

        Args:
            output_path: 出力先ファイルパス。
                         チーム共有: ".claude/settings.json"
                         個人専用:   ".claude/settings.local.json"

        Returns:
            書き出したファイルのパス。
        """
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

        return str(path.resolve())

    @staticmethod
    def load(settings_path: str) -> dict:
        """既存の設定ファイルを読み込みます。"""
        with open(settings_path, encoding="utf-8") as f:
            return json.load(f)

    def show_summary(self) -> None:
        """現在の設定内容をわかりやすく表示します。"""
        print("=" * 50)
        print("Claude Code 設定サマリー")
        print("=" * 50)

        model = self.config.get("model", "未設定（デフォルト: sonnet）")
        print(f"モデル: {model}")
        if model in AVAILABLE_MODELS:
            print(f"  → {AVAILABLE_MODELS[model]}")

        perms = self.config.get("permissions", {})
        mode = perms.get("defaultMode", "default")
        print(f"\nパーミッションモード: {mode}")
        print(f"  → {PERMISSION_MODES.get(mode, '不明')}")

        allow_list = perms.get("allow", [])
        if allow_list:
            print(f"\n許可コマンド ({len(allow_list)}件):")
            for cmd in allow_list:
                print(f"  ✅ {cmd}")

        deny_list = perms.get("deny", [])
        if deny_list:
            print(f"\n拒否コマンド ({len(deny_list)}件):")
            for cmd in deny_list:
                print(f"  ❌ {cmd}")

        hooks = self.config.get("hooks", {})
        if hooks:
            print(f"\nフック ({sum(len(v) for v in hooks.values())}件):")
            for event, hook_list in hooks.items():
                for hook in hook_list:
                    cmd = hook["hooks"][0]["command"]
                    print(f"  🔗 [{event}] {hook['matcher']} → {cmd}")

        print("=" * 50)


# ==============================
# 使用例
# ==============================

if __name__ == "__main__":
    # --- Pythonプロジェクト向け設定生成例 ---
    print("Pythonプロジェクト用 Claude Code 設定を生成します...\n")

    generator = (
        SettingsGenerator()
        .set_model("claude-sonnet-4-6")
        .set_permission_mode("default")          # 安全のためデフォルト確認モード
        .apply_preset("python")                  # Python関連コマンドを許可
        .apply_preset("git")                     # Gitコマンドを許可
        .apply_preset("secure")                  # 危険コマンドを拒否
        .add_hook(
            "PostToolUse",                       # ファイル編集後に自動実行
            "Edit",                              # 対象: ファイル編集ツール
            "ruff check $FILE --fix",            # Pythonリンター自動修正
        )
        .add_hook(
            "PostToolUse",
            "Edit",
            "black $FILE",                       # Pythonフォーマッター自動適用
        )
        .set_env("DISABLE_TELEMETRY", "1")       # テレメトリ無効化（プライバシー設定）
    )

    # 設定内容を表示
    generator.show_summary()

    # ファイルに書き出し（実際のプロジェクトで使用する場合はコメントを外す）
    # output_path = generator.save(".claude/settings.json")
    # print(f"\n✅ 設定を書き出しました: {output_path}")

    # JSON形式でも確認できる
    print("\n生成されるJSON:")
    print(json.dumps(generator.build(), indent=2, ensure_ascii=False))
