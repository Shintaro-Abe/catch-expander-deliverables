// PoC品質: 実際のアプリに合わせてセレクタ・URL・アサーションを修正してください
//
// Playwright テストファイルのサンプル
//
// セレクタ（要素の特定方法）の優先順位（Playwright公式ガイド推奨）：
//   getByRole > getByLabel > getByTestId > getByText > CSS
//
// getByRole を優先する理由：
//   - アクセシビリティ（a11y）の検証も兼ねられる
//   - DOM構造が変わっても壊れにくい
//   - フレーク率（非決定的失敗率）が低い（1.5%未満）

import { test, expect, Page } from '@playwright/test';

// Page Object Model（POM）パターン：
//   ページの操作をクラスにまとめることで、テストコードの重複を減らし保守性を高める
class LoginPage {
  constructor(private page: Page) {}

  async goto() {
    await this.page.goto('/login');
  }

  async login(email: string, password: string) {
    // getByRole: aria-label や role 属性を使った要素の特定（最優先セレクタ）
    await this.page.getByLabel('メールアドレス').fill(email);
    await this.page.getByLabel('パスワード').fill(password);
    await this.page.getByRole('button', { name: 'ログイン' }).click();
  }
}

// test.describe: 関連するテストをグループ化する
test.describe('ログイン機能', () => {

  // test.beforeEach: 各テストの前に実行される共通処理
  let loginPage: LoginPage;
  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
    await loginPage.goto();
  });

  test('正常なログイン', async ({ page }) => {
    // 環境変数からテストアカウント情報を取得（ハードコードしない）
    await loginPage.login(
      process.env.TEST_EMAIL ?? 'test@example.com',
      process.env.TEST_PASSWORD ?? 'test-password'
    );

    // ログイン後のページ遷移を確認
    // toHaveURL は Playwright の自動待機機能付きアサーション（明示的な wait は不要）
    await expect(page).toHaveURL('/dashboard');
    await expect(page.getByRole('heading', { name: 'ダッシュボード' })).toBeVisible();
  });

  test('無効なパスワードでのログイン失敗', async ({ page }) => {
    await loginPage.login('test@example.com', 'wrong-password');

    // エラーメッセージの表示を確認
    await expect(
      page.getByRole('alert').filter({ hasText: 'メールアドレスまたはパスワードが正しくありません' })
    ).toBeVisible();

    // ログインページに留まっていることを確認
    await expect(page).toHaveURL('/login');
  });

  test('空フォームのバリデーション', async ({ page }) => {
    // 何も入力せずにログインボタンをクリック
    await page.getByRole('button', { name: 'ログイン' }).click();

    // HTML5バリデーションエラーの確認
    // getByTestId: data-testid 属性での要素特定（アプリ固有の識別子）
    await expect(page.getByTestId('email-error')).toBeVisible();
    await expect(page.getByTestId('password-error')).toBeVisible();
  });
});

// API テストとUIテストの組み合わせ
// storageState: 認証済み状態を保存して再利用（ログイン処理の繰り返しを避けコスト削減）
test.describe('認証済みユーザーの操作', () => {

  // グローバルセットアップで保存した認証状態を使用
  // → playwright.config.ts の globalSetup で storageState を生成しておく
  test.use({ storageState: 'playwright/.auth/user.json' });

  test('プロフィール更新', async ({ page }) => {
    await page.goto('/profile');

    const nameInput = page.getByLabel('表示名');
    await nameInput.clear();
    await nameInput.fill('新しい名前');

    await page.getByRole('button', { name: '保存' }).click();

    // 成功トーストの確認（アニメーション後に表示される要素は waitFor が必要な場合あり）
    await expect(page.getByRole('status').filter({ hasText: '保存しました' })).toBeVisible();
  });
});
