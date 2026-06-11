# PoC品質 - 本番利用前に入力バリデーション・サニタイズ処理を追加すること

"""
Bolt の <boltAction> タグパーサー

Bolt.new では LLM が <boltAction> タグを生成し、専用パーサーが解析して
ファイル書き込み・シェルコマンド実行・サーバー起動などを実行する。
これを Python で再現した「暗黙的ツールコーリング」パターンの実装。

AI-DLC における位置づけ: コード生成 → 実行の自動化ブリッジ
"""

import re
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


# ---- データ構造 ----------------------------------------------------------

class ActionType(str, Enum):
    FILE   = "file"    # ファイル書き込み
    SHELL  = "shell"   # シェルコマンド実行
    START  = "start"   # 開発サーバー起動


@dataclass
class BoltAction:
    """Bolt の単一アクション（ファイル or シェルコマンド）"""
    action_type: ActionType
    content: str
    file_path: str | None = None  # type="file" のときのみ有効


@dataclass
class BoltArtifact:
    """Bolt の成果物（アクション群のコンテナ）"""
    artifact_id: str
    title: str
    actions: list[BoltAction] = field(default_factory=list)


# ---- パーサー実装 --------------------------------------------------------

class BoltActionParser:
    """
    LLM が生成した <boltArtifact> / <boltAction> タグを解析し、
    実行可能な BoltArtifact オブジェクトに変換するパーサー。

    Bolt の内部動作: LLM → タグ生成 → このパーサーで解析 → WebContainers実行
    Python版: LLM → タグ生成 → このパーサーで解析 → ローカルファイルシステム実行
    """

    # 正規表現パターン（グリーディ非マッチで入れ子タグに対応）
    _ARTIFACT_PATTERN = re.compile(
        r'<boltArtifact\s+id="(?P<id>[^"]+)"\s+title="(?P<title>[^"]+)">'
        r'(?P<body>.*?)</boltArtifact>',
        re.DOTALL,
    )
    _ACTION_PATTERN = re.compile(
        r'<boltAction\s+type="(?P<type>[^"]+)"(?:\s+filePath="(?P<path>[^"]+)")?>'
        r'(?P<content>.*?)</boltAction>',
        re.DOTALL,
    )

    def parse(self, llm_output: str) -> list[BoltArtifact]:
        """LLM の出力テキストからアーティファクトリストを抽出する"""
        artifacts: list[BoltArtifact] = []

        for art_match in self._ARTIFACT_PATTERN.finditer(llm_output):
            artifact = BoltArtifact(
                artifact_id=art_match.group("id"),
                title=art_match.group("title"),
            )
            body = art_match.group("body")

            for act_match in self._ACTION_PATTERN.finditer(body):
                try:
                    action_type = ActionType(act_match.group("type"))
                except ValueError:
                    continue  # 未知のアクションタイプはスキップ

                artifact.actions.append(
                    BoltAction(
                        action_type=action_type,
                        content=act_match.group("content").strip(),
                        file_path=act_match.group("path"),
                    )
                )
            artifacts.append(artifact)

        return artifacts


# ---- アクション実行エンジン ----------------------------------------------

class BoltActionExecutor:
    """
    パースされた BoltArtifact を実際のファイルシステム上で実行する。
    WebContainers (ブラウザ内Node.js) の Python 代替実装。

    注意: shell アクションは subprocess を使用するため、
    信頼できない LLM 出力の実行には必ずサンドボックスを設けること。
    """

    def __init__(self, output_dir: str = "./bolt_output") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, artifact: BoltArtifact, dry_run: bool = True) -> dict:
        """
        アーティファクトを実行する。
        dry_run=True (デフォルト) の場合はファイル書き込みのみ行い、
        シェルコマンドは出力するだけで実行しない。
        """
        results = {
            "artifact_id": artifact.artifact_id,
            "title": artifact.title,
            "files_written": [],
            "commands": [],
            "errors": [],
        }

        for action in artifact.actions:
            try:
                if action.action_type == ActionType.FILE:
                    self._write_file(action, results)
                elif action.action_type in (ActionType.SHELL, ActionType.START):
                    self._handle_shell(action, results, dry_run)
            except Exception as exc:
                results["errors"].append(str(exc))

        return results

    def _write_file(self, action: BoltAction, results: dict) -> None:
        if not action.file_path:
            return
        target = self.output_dir / action.file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(action.content, encoding="utf-8")
        results["files_written"].append(str(target))

    def _handle_shell(self, action: BoltAction, results: dict, dry_run: bool) -> None:
        cmd = action.content
        results["commands"].append(cmd)
        if not dry_run:
            import subprocess
            # セキュリティ注意: LLM 生成コマンドの実行は十分な検証の後に行うこと
            ret = subprocess.run(
                cmd, shell=True, cwd=self.output_dir, capture_output=True, text=True
            )
            if ret.returncode != 0:
                results["errors"].append(f"コマンド失敗: {cmd}\n{ret.stderr}")


# ---- 使用例 --------------------------------------------------------------

SAMPLE_LLM_OUTPUT = '''
以下のTODOアプリを生成しました。

<boltArtifact id="todo-app" title="シンプルTODOアプリ">
  <boltAction type="file" filePath="package.json">
{
  "name": "todo-app",
  "version": "1.0.0",
  "dependencies": {
    "react": "^18.0.0",
    "react-dom": "^18.0.0"
  }
}
  </boltAction>
  <boltAction type="shell">npm install</boltAction>
  <boltAction type="file" filePath="src/App.tsx">
import { useState } from "react";

export default function App() {
  const [todos, setTodos] = useState<string[]>([]);
  const [input, setInput] = useState("");

  const add = () => {
    if (input.trim()) {
      setTodos([...todos, input.trim()]);
      setInput("");
    }
  };

  return (
    <div className="p-4">
      <h1 className="text-2xl font-bold mb-4">TODO</h1>
      <div className="flex gap-2 mb-4">
        <input
          className="border p-2 flex-1"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="新しいタスクを入力"
        />
        <button className="bg-blue-500 text-white px-4 py-2" onClick={add}>
          追加
        </button>
      </div>
      <ul>
        {todos.map((todo, i) => (
          <li key={i} className="py-1 border-b">{todo}</li>
        ))}
      </ul>
    </div>
  );
}
  </boltAction>
  <boltAction type="start">npm run dev</boltAction>
</boltArtifact>
'''


def main() -> None:
    parser   = BoltActionParser()
    executor = BoltActionExecutor(output_dir="./bolt_output/todo-app")

    # パース
    artifacts = parser.parse(SAMPLE_LLM_OUTPUT)
    print(f"パース結果: {len(artifacts)} アーティファクト")

    for artifact in artifacts:
        print(f"\n--- {artifact.title} (id={artifact.artifact_id}) ---")
        print(f"アクション数: {len(artifact.actions)}")

        # 実行（dry_run=True でシェルコマンドはスキップ）
        result = executor.execute(artifact, dry_run=True)
        print(f"書き込みファイル: {result['files_written']}")
        print(f"予定コマンド: {result['commands']}")
        if result["errors"]:
            print(f"エラー: {result['errors']}")


if __name__ == "__main__":
    main()
