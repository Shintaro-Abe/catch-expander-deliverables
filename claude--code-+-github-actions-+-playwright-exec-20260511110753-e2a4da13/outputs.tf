# PoC品質: このコードは概念実証用です。本番環境では追加のセキュリティ設定・テストが必要です。

# =============================================================================
# 出力値定義
# Claude Code + GitHub Actions + Playwright テスト基盤
#
# これらの値は `terraform output` コマンドで確認でき、
# GitHub ActionsのSecretsやワークフロー変数として設定します。
# =============================================================================

# --- S3バケット情報 ---

output "report_bucket_name" {
  description = "Playwrightレポート保存用S3バケット名（GitHub ActionsのS3_BUCKET変数に設定）"
  value       = aws_s3_bucket.playwright_reports.id
}

output "report_bucket_arn" {
  description = "Playwrightレポート保存用S3バケットARN"
  value       = aws_s3_bucket.playwright_reports.arn
}

output "report_bucket_region" {
  description = "S3バケットのリージョン"
  value       = aws_s3_bucket.playwright_reports.region
}

# --- CloudFront情報 ---

output "cloudfront_distribution_id" {
  description = "CloudFrontディストリビューションID（キャッシュ無効化時に使用: CLOUDFRONT_DISTRIBUTION_ID）"
  value       = var.enable_cloudfront ? aws_cloudfront_distribution.playwright_reports[0].id : null
}

output "cloudfront_domain_name" {
  description = "テストレポートの公開URL（例: https://<id>.cloudfront.net/reports/<run-id>/index.html）"
  value       = var.enable_cloudfront ? "https://${aws_cloudfront_distribution.playwright_reports[0].domain_name}" : null
}

# --- IAMロール情報 ---

output "github_actions_role_arn" {
  description = "GitHub ActionsがAssumeRoleするIAMロールARN（GitHub ActionsのAWS_ROLE_ARN変数に設定）"
  value       = aws_iam_role.github_actions.arn
}

output "github_oidc_provider_arn" {
  description = "GitHub OIDC プロバイダーARN"
  value       = aws_iam_openid_connect_provider.github.arn
}

# --- GitHub Actions設定例 ---

output "github_actions_workflow_snippet" {
  description = "GitHub Actionsワークフローへの設定例（参考用）"
  sensitive   = false
  value       = <<-EOT
    # .github/workflows/playwright.yml に追加する設定例

    env:
      AWS_ROLE_ARN: ${aws_iam_role.github_actions.arn}
      AWS_REGION: ${var.aws_region}
      S3_BUCKET: ${aws_s3_bucket.playwright_reports.id}
      CLOUDFRONT_DISTRIBUTION_ID: ${var.enable_cloudfront ? aws_cloudfront_distribution.playwright_reports[0].id : "（CloudFront無効）"}

    # AWSへの認証ステップ（OIDC使用）
    - name: Configure AWS credentials
      uses: aws-actions/configure-aws-credentials@v4
      with:
        role-to-assume: ${aws_iam_role.github_actions.arn}
        aws-region: ${var.aws_region}

    # Playwrightレポートをアップロード
    - name: Upload report to S3
      run: |
        aws s3 sync playwright-report/ s3://${aws_s3_bucket.playwright_reports.id}/reports/${{ github.run_id }}/
        ${var.enable_cloudfront ? "aws cloudfront create-invalidation --distribution-id ${aws_cloudfront_distribution.playwright_reports[0].id} --paths '/reports/${{ github.run_id }}/*'" : ""}
  EOT
}

# --- コスト見積もり参考情報 ---

output "cost_notes" {
  description = "月額コスト目安（参考値）"
  value       = <<-EOT
    月額コスト目安（東京リージョン・2026年5月時点）:
    - S3ストレージ: ~$0.025/GB/月（30日保存・レポート数に依存）
    - S3転送:      無料（同一リージョン内）
    - CloudFront:  ~$0.085/GB（アジアパシフィック）+ $0.01/10,000リクエスト
    ※ Anthropic Claude API利用料は別途発生（claude-sonnet-4-6: $3/MTok入力）
  EOT
}
