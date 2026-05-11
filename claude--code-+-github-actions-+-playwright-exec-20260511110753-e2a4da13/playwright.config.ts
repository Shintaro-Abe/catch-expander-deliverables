// PoC品質: 本番環境での利用前にプロジェクトの要件に合わせた調整が必要です
//
// Playwright 設定ファイル（CI最適化版）
//
// このファイルでは以下を制御します：
//   - ブラウザの種類（Chromium / Firefox / WebKit）
//   - CI環境での動作（リトライ回数、並列数、レポート形式）
//   - 失敗時のアーティファクト収集（スクリーンショット・トレース・動画）

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  // テストファイルのディレクトリ
  testDir: './tests',

  // fullyParallel: true にするとテストレベルで並列実行（シャーディングの効果最大化）
  // false だとファイル単位の並列実行になる
  fullyParallel: true,

  // CI環境では .only を付け忘れたテストを検知してエラーにする
  // （ローカルでは .only を使っても OK）
  forbidOnly: !!process.env.CI,

  // リトライ回数：CI では2回、ローカルでは0回
  // フレーキーテスト（ネットワーク遅延等で稀に失敗するテスト）への対応
  retries: process.env.CI ? 2 : 0,

  // 並列ワーカー数：CI では2（ランナーのCPUに合わせて調整）、ローカルは自動
  workers: process.env.CI ? 2 : undefined,

  // レポート形式：
  //   - CI: blob（後でシャード間を統合する中間形式）
  //   - ローカル: html（ブラウザで確認できる見やすい形式）
  reporter: process.env.CI ? 'blob' : 'html',

  // 全テスト共通の設定（use ブロック）
  use: {
    // ベースURL（環境変数で上書き可能）
    baseURL: process.env.BASE_URL ?? 'http://localhost:3000',

    // トレース（ブラウザ操作の詳細な記録）：初回リトライ時のみ収集
    // → 再現性の低いバグのデバッグに役立つ
    trace: 'on-first-retry',

    // スクリーンショット：失敗時のみキャプチャ（ストレージ節約）
    screenshot: 'only-on-failure',

    // 動画：失敗時のみ保持（ストレージ節約）
    video: 'retain-on-failure',
  },

  // テスト対象のブラウザ設定（プロジェクト）
  // CI コストを考慮する場合は Chromium のみに絞ることも可能
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },    // Safari エンジン
    },

    // モバイルテスト（必要に応じてコメントアウト解除）
    // {
    //   name: 'mobile-chrome',
    //   use: { ...devices['Pixel 5'] },
    // },
    // {
    //   name: 'mobile-safari',
    //   use: { ...devices['iPhone 13'] },
    // },
  ],

  // ローカル開発時に自動でテスト対象アプリを起動するオプション
  // CI では別途アプリが起動済みであることを前提とする
  // webServer: process.env.CI ? undefined : {
  //   command: 'npm run dev',
  //   url: 'http://localhost:3000',
  //   reuseExistingServer: !process.env.CI,
  // },
});
