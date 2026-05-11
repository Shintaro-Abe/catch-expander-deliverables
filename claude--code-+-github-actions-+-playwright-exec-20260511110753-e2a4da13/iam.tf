# PoC品質: このコードは概念実証用です。本番環境では追加のセキュリティ設定・テストが必要です。

# =============================================================================
# IAM設定: GitHub Actions OIDC認証
# Claude Code + GitHub Actions + Playwright テスト基盤
#
# OIDC（OpenID Connect）とは？
#   長期的なAWSアクセスキーをGitHubに保存する代わりに、
#   GitHubが発行する一時的なトークンでAWSに認証する仕組みです。
#   これによりシークレットの漏洩リスクを大幅に低減できます。
# =============================================================================

# =============================================================================
# GitHub Actions OIDC プロバイダー
# GitHubのOIDCエンドポイントをAWSに登録する
# =============================================================================

# 既存のOIDCプロバイダーが存在する場合は data source で参照
data "aws_iam_openid_connect_provider" "github" {
  count = 0 # 新規作成する場合は 0、既存を参照する場合は 1 にして url を指定

  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  # GitHubのOIDCサービスが使用するクライアントID
  client_id_list = ["sts.amazonaws.com"]

  # GitHubのOIDCエンドポイントのTLS証明書サムプリント
  # 参考: https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]

  tags = {
    Name = "github-actions-oidc"
  }
}

# =============================================================================
# IAMロール: GitHub Actions実行用
# =============================================================================

# 信頼ポリシー: どのGitHubリポジトリ・ブランチからのアクセスを許可するか定義
data "aws_iam_policy_document" "github_actions_trust" {
  statement {
    sid     = "AllowGitHubOIDC"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    # 信頼するOIDCオーディエンスを制限
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # 信頼するGitHubリポジトリとブランチを制限
    # sub（Subject）クレームの形式: repo:<org>/<repo>:ref:refs/heads/<branch>
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        for branch in var.github_allowed_branches :
        "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/${branch}"
      ]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${local.name_prefix}-github-actions-role"
  description        = "GitHub Actions用IAMロール（OIDC認証）- Playwright CI/CD"
  assume_role_policy = data.aws_iam_policy_document.github_actions_trust.json

  # セッション有効期限: 1時間（Playwrightテスト実行時間を考慮して設定）
  max_session_duration = 3600

  tags = {
    Name    = "${local.name_prefix}-github-actions-role"
    Purpose = "playwright-ci"
  }
}

# =============================================================================
# IAMポリシー: Playwright レポートのS3操作権限
# =============================================================================

data "aws_iam_policy_document" "playwright_reports_access" {
  # S3バケットのオブジェクト操作（テストレポートのアップロード・ダウンロード）
  statement {
    sid    = "PlaywrightReportsObjectAccess"
    effect = "Allow"

    actions = [
      "s3:PutObject",       # レポートのアップロード
      "s3:GetObject",       # レポートのダウンロード（デバッグ用）
      "s3:DeleteObject",    # 古いレポートの削除
      "s3:AbortMultipartUpload", # 大容量レポートのマルチパートアップロード制御
    ]

    resources = ["${aws_s3_bucket.playwright_reports.arn}/*"]
  }

  # バケット一覧表示（アーティファクト管理スクリプト用）
  statement {
    sid    = "PlaywrightReportsBucketList"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.playwright_reports.arn]
  }
}

resource "aws_iam_policy" "playwright_reports_access" {
  name        = "${local.name_prefix}-playwright-reports-policy"
  description = "Playwright HTMLレポートのS3アップロード・管理権限"
  policy      = data.aws_iam_policy_document.playwright_reports_access.json
}

resource "aws_iam_role_policy_attachment" "playwright_reports_access" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.playwright_reports_access.arn
}

# =============================================================================
# IAMポリシー: CloudFrontキャッシュ無効化権限
# （レポート更新後にCloudFrontキャッシュを即座に更新するために必要）
# =============================================================================

data "aws_iam_policy_document" "cloudfront_invalidation" {
  count = var.enable_cloudfront ? 1 : 0

  statement {
    sid    = "CloudFrontCacheInvalidation"
    effect = "Allow"

    actions = [
      "cloudfront:CreateInvalidation",
      "cloudfront:GetInvalidation",
      "cloudfront:ListInvalidations",
    ]

    resources = [aws_cloudfront_distribution.playwright_reports[0].arn]
  }
}

resource "aws_iam_policy" "cloudfront_invalidation" {
  count = var.enable_cloudfront ? 1 : 0

  name        = "${local.name_prefix}-cloudfront-invalidation-policy"
  description = "PlaywrightレポートのCloudFrontキャッシュ無効化権限"
  policy      = data.aws_iam_policy_document.cloudfront_invalidation[0].json
}

resource "aws_iam_role_policy_attachment" "cloudfront_invalidation" {
  count = var.enable_cloudfront ? 1 : 0

  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.cloudfront_invalidation[0].arn
}
