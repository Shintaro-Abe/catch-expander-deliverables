# PoC品質 - 本番利用前に GitHub PAT のスコープ最小化・Secrets 管理を実装すること

"""
Bolt × GitHub CI/CD ブリッジ

Bolt.new は GitHub Actions とのネイティブ連携を持たない。
このモジュールは Bolt が生成したコードを GitHub リポジトリにプッシュし、
CI/CD パイプラインをトリガーするブリッジ層を実装する。

推奨ワークフロー (research-2 より):
  1. Bolt.new でコード生成
  2. このブリッジでGitHubへプッシュ
  3. GitHub Actions が自動起動 (test → staging deploy → prod deploy)
  4. 本番APIキーは GitHub Secrets で管理（Bolt側には不要）

AI-DLC における位置づけ: プロトタイプ → CI/CD 統合フェーズ
"""

import os
import json
import base64
from pathlib import Path
from dataclasses import dataclass
from typing import Any

import anthropic


# ---- GitHub Actions ワークフロー テンプレート ----------------------------

GITHUB_ACTIONS_TEMPLATE = """\
# Bolt.new 生成コード用 CI/CD ワークフロー
# このファイルは bolt_cicd_bridge.py によって自動生成されました
name: Bolt App CI/CD

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
      - run: npm ci
      - run: npm test -- --run
      - run: npm run build

  deploy-staging:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/develop'
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
      - run: npm ci && npm run build
      # デプロイ先: Vercel / Netlify / Cloudflare Pages から選択
      - name: Deploy to Vercel (Staging)
        env:
          VERCEL_TOKEN: ${{{{ secrets.VERCEL_TOKEN }}}}
          VERCEL_ORG_ID: ${{{{ secrets.VERCEL_ORG_ID }}}}
          VERCEL_PROJECT_ID: ${{{{ secrets.VERCEL_PROJECT_ID }}}}
        run: npx vercel --token $VERCEL_TOKEN --prebuilt

  deploy-production:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
      - run: npm ci && npm run build
      - name: Deploy to Vercel (Production)
        env:
          VERCEL_TOKEN: ${{{{ secrets.VERCEL_TOKEN }}}}
          VERCEL_ORG_ID: ${{{{ secrets.VERCEL_ORG_ID }}}}
          VERCEL_PROJECT_ID: ${{{{ secrets.VERCEL_PROJECT_ID }}}}
        run: npx vercel --prod --token $VERCEL_TOKEN --prebuilt
"""

BOLT_IGNORE_TEMPLATE = """\
# .bolt/ignore - AIコンテキストから除外するファイル
# Bolt Pro プランの月間10Mトークン上限を効率的に使うための設定
# 大規模プロジェクトで「Project size exceeded」エラーを防ぐ

# 依存パッケージ（最大の削減効果）
node_modules/
.pnp
.pnp.js

# ビルド成果物
dist/
build/
.next/
out/
.nuxt/

# テスト・カバレッジ
coverage/
.nyc_output/
*.test.snap

# キャッシュ
.cache/
.turbo/
.eslintcache
*.tsbuildinfo

# ログ・一時ファイル
*.log
npm-debug.log*
.DS_Store
*.local

# 環境変数（セキュリティのため必ず除外）
.env
.env.local
.env.*.local
"""


# ---- コアクラス ---------------------------------------------------------

@dataclass
class RepoFile:
    """リポジトリに書き込む単一ファイル"""
    path: str      # リポジトリ内の相対パス
    content: str   # ファイル内容


class BoltCICDBridge:
    """
    Bolt 生成コードを GitHub リポジトリに統合し CI/CD をセットアップするクラス。
    GitHub REST API v3 を使用（github パッケージ不要）。

    注意: PAT (Personal Access Token) は repo スコープのみ付与し、
    GitHub Secrets に保存すること。コードにハードコードしないこと。
    """

    GITHUB_API_BASE = "https://api.github.com"

    def __init__(
        self,
        github_token: str | None = None,
        owner: str | None = None,
        repo: str | None = None,
    ) -> None:
        self.token = github_token or os.environ.get("GITHUB_TOKEN", "")
        self.owner = owner or os.environ.get("GITHUB_OWNER", "")
        self.repo  = repo  or os.environ.get("GITHUB_REPO", "")
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def scaffold_repo(self, files: list[RepoFile], branch: str = "main") -> dict:
        """
        Bolt 生成ファイル群 + CI/CD 設定ファイルをリポジトリに一括コミットする。
        GitHub の Tree API を使ってバッチコミットすることで API レート制限を節約。
        """
        import urllib.request

        # CI/CD ワークフローと .bolt/ignore を自動付加
        all_files = list(files) + [
            RepoFile(
                path=".github/workflows/bolt-cicd.yml",
                content=GITHUB_ACTIONS_TEMPLATE,
            ),
            RepoFile(
                path=".bolt/ignore",
                content=BOLT_IGNORE_TEMPLATE,
            ),
        ]

        # ベースツリーの SHA を取得
        base_sha = self._get_branch_sha(branch)

        # Tree オブジェクトを構築
        tree_items = [
            {
                "path": f.path,
                "mode": "100644",
                "type": "blob",
                "content": f.content,
            }
            for f in all_files
        ]

        # ツリー作成
        tree_sha = self._create_tree(tree_items, base_sha)

        # コミット作成
        commit_sha = self._create_commit(
            message="feat: Bolt.new 生成コードを初期化 + CI/CD 設定追加",
            tree_sha=tree_sha,
            parent_sha=base_sha,
        )

        # ブランチ更新
        self._update_branch(branch, commit_sha)

        return {
            "commit_sha": commit_sha,
            "files_pushed": [f.path for f in all_files],
            "branch": branch,
            "repo_url": f"https://github.com/{self.owner}/{self.repo}",
        }

    def _get_branch_sha(self, branch: str) -> str:
        data = self._github_request("GET", f"/repos/{self.owner}/{self.repo}/git/ref/heads/{branch}")
        return data["object"]["sha"]

    def _create_tree(self, tree_items: list[dict], base_tree_sha: str) -> str:
        data = self._github_request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/git/trees",
            body={"base_tree": base_tree_sha, "tree": tree_items},
        )
        return data["sha"]

    def _create_commit(self, message: str, tree_sha: str, parent_sha: str) -> str:
        data = self._github_request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/git/commits",
            body={"message": message, "tree": tree_sha, "parents": [parent_sha]},
        )
        return data["sha"]

    def _update_branch(self, branch: str, commit_sha: str) -> None:
        self._github_request(
            "PATCH",
            f"/repos/{self.owner}/{self.repo}/git/refs/heads/{branch}",
            body={"sha": commit_sha, "force": False},
        )

    def _github_request(self, method: str, path: str, body: dict | None = None) -> dict:
        import urllib.request
        import urllib.error

        url = self.GITHUB_API_BASE + path
        data = json.dumps(body).encode() if body else None

        req = urllib.request.Request(url, data=data, headers=self._headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitHub API エラー {exc.code}: {exc.read().decode()}") from exc


# ---- Bolt コード生成 + CI/CD プッシュの統合フロー -----------------------

class BoltToGitHubFlow:
    """
    Bolt スタイルのコード生成から GitHub プッシュまでの一気通貫フロー。
    AI-DLC の「プロトタイプ → CI/CD 統合」フェーズを自動化する。
    """

    def __init__(self) -> None:
        self.llm_client = anthropic.Anthropic()
        self.cicd_bridge = BoltCICDBridge()

    def generate_and_push(self, app_description: str, branch: str = "develop") -> dict:
        """
        1. Claude で React アプリのスケルトンを生成
        2. GitHub にプッシュして CI/CD をトリガー
        """
        # Step 1: コード生成
        print(f"[1/2] Claude でコードを生成中...")
        response = self.llm_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": (
                        "React + TypeScript + Vite のアプリスケルトンを生成してください。"
                        "package.json と src/App.tsx の2ファイルのみ提供してください。"
                        "各ファイルを === FILENAME: path === で区切って出力してください。"
                    ),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": app_description}],
        )
        raw_output = response.content[0].text

        # Step 2: 出力テキストをファイルに分解
        files = self._parse_files(raw_output)
        print(f"  生成ファイル数: {len(files)}")

        # Step 3: GitHub にプッシュ
        print(f"[2/2] GitHub にプッシュ中 (branch={branch})...")
        result = self.cicd_bridge.scaffold_repo(files, branch=branch)
        print(f"  コミット: {result['commit_sha'][:8]}")

        return result

    @staticmethod
    def _parse_files(raw: str) -> list[RepoFile]:
        """=== FILENAME: path === 区切りのテキストをファイルリストに変換"""
        import re
        files = []
        pattern = re.compile(r"=== FILENAME: (.+?) ===\n(.*?)(?=\n=== FILENAME:|$)", re.DOTALL)
        for match in pattern.finditer(raw):
            files.append(RepoFile(path=match.group(1).strip(), content=match.group(2).strip()))
        return files


# ---- 使用例 --------------------------------------------------------------

def main() -> None:
    # GitHub 認証情報は環境変数から取得（コードにハードコードしない）
    # export GITHUB_TOKEN=ghp_xxx
    # export GITHUB_OWNER=your-org
    # export GITHUB_REPO=your-repo

    bridge = BoltCICDBridge()

    # 手動でファイルリストを作成してプッシュする例
    files = [
        RepoFile(
            path="package.json",
            content=json.dumps({
                "name": "bolt-app",
                "version": "0.0.1",
                "scripts": {"dev": "vite", "build": "vite build", "test": "vitest run"},
                "dependencies": {"react": "^18.3.0", "react-dom": "^18.3.0"},
                "devDependencies": {"vite": "^5.0.0", "vitest": "^1.0.0"},
            }, indent=2),
        ),
        RepoFile(
            path="src/App.tsx",
            content='export default function App() { return <h1>Bolt App</h1>; }',
        ),
    ]

    print("=== Bolt → GitHub CI/CD ブリッジ デモ ===")
    print("注意: 実際の GitHub Token と リポジトリが必要です")
    print("\n生成される CI/CD ファイル:")
    print("  - .github/workflows/bolt-cicd.yml  (GitHub Actions)")
    print("  - .bolt/ignore  (AIコンテキスト最適化)")

    # 実際の push は環境変数が設定されている場合のみ実行
    if os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_OWNER"):
        result = bridge.scaffold_repo(files)
        print(f"\nプッシュ完了: {result['repo_url']}/commit/{result['commit_sha'][:8]}")
    else:
        print("\n[SKIP] GITHUB_TOKEN / GITHUB_OWNER が未設定のため実行をスキップ")


if __name__ == "__main__":
    main()
