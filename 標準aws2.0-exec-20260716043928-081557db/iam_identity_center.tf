# PoC品質 — 本番環境への適用前に、必ずセキュリティレビューと適切な設定変更を行ってください
# 標準AWS2.0: IAM Identity Center（パーミッションセット）+ SCP/RCP ガバナンス

# ====================================================
# IAM Identity Center（旧称: AWS SSO）
#
# 設計思想「人的アクセスの一元管理」:
#   旧来: アカウントごとにIAMユーザーを作成 → 管理コスト増大・証跡が分散
#   標準: Identity Centerで全人的アクセスを一元管理 → 単一IdPで全アカウントにアクセス
#
# 事前要件: Organizations管理アカウントでIdentity Centerを有効化済みであること
# ====================================================
data "aws_ssoadmin_instances" "main" {}

locals {
  sso_instance_arn      = tolist(data.aws_ssoadmin_instances.main.arns)[0]
  sso_identity_store_id = tolist(data.aws_ssoadmin_instances.main.identity_store_ids)[0]
}

# ====================================================
# パーミッションセット: ロール別の最小権限定義
#
# 設計原則（最小権限 + セッション時間の最小化）:
#   ReadOnly      : 日常モニタリング用（8h）— 開発者が最もよく使うロール
#   Operator      : デプロイ・運用操作用（4h）— Bedrock/Lambda/ECS操作を含む
#   Admin         : インフラ変更用（1h）— 申請ベースで使用
#   SecurityAdmin : セキュリティ専任用（1h）— セキュリティチームのみ
# ====================================================

# --- ReadOnly: 日常調査・モニタリング ---
resource "aws_ssoadmin_permission_set" "readonly" {
  name             = "${var.project_name}-ReadOnly"
  instance_arn     = local.sso_instance_arn
  session_duration = "PT8H"
  description      = "読み取り専用アクセス（開発者の日常モニタリング・調査用）"
}

resource "aws_ssoadmin_managed_policy_attachment" "readonly" {
  instance_arn       = local.sso_instance_arn
  managed_policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
  permission_set_arn = aws_ssoadmin_permission_set.readonly.arn
}

# --- Operator: GenAIアプリ開発者・デプロイ担当 ---
resource "aws_ssoadmin_permission_set" "operator" {
  name             = "${var.project_name}-Operator"
  instance_arn     = local.sso_instance_arn
  session_duration = "PT4H"
  description      = "AIアプリデプロイ・運用操作（Bedrock・Lambda・ECS）"
}

resource "aws_ssoadmin_managed_policy_attachment" "operator_readonly" {
  instance_arn       = local.sso_instance_arn
  managed_policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
  permission_set_arn = aws_ssoadmin_permission_set.operator.arn
}

# Operatorへの追加インラインポリシー（Bedrock/デプロイ操作権限）
resource "aws_ssoadmin_permission_set_inline_policy" "operator" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.operator.arn

  inline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Bedrock: 推論・RAG検索・エージェント呼び出し
        Sid    = "BedrockRuntimeAccess"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModel",
          "bedrock:RetrieveAndGenerate",
          "bedrock-agent-runtime:Retrieve",
          "bedrock-agent-runtime:RetrieveAndGenerate",
          "bedrock-agent-runtime:InvokeAgent",
          "bedrock-agent:GetKnowledgeBase",
          "bedrock-agent:ListKnowledgeBases",
          "bedrock-agent:GetDataSource",
          "bedrock-agent:StartIngestionJob",
          "bedrock-agent:ListIngestionJobs"
        ]
        Resource = "*"
      },
      {
        # Lambda: コードデプロイ・バージョン管理・実行
        Sid    = "LambdaDeployAccess"
        Effect = "Allow"
        Action = [
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:PublishVersion",
          "lambda:CreateAlias",
          "lambda:UpdateAlias",
          "lambda:InvokeFunction",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration"
        ]
        Resource = "*"
      },
      {
        # ECS/ECR: コンテナイメージプッシュ・サービス更新
        Sid    = "ECSDeployAccess"
        Effect = "Allow"
        Action = [
          "ecs:UpdateService",
          "ecs:DescribeServices",
          "ecs:DescribeTasks",
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload"
        ]
        Resource = "*"
      },
      {
        # S3: ナレッジベースドキュメントのアップロード
        Sid    = "KnowledgeBaseS3Access"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.project_name}-kb-*",
          "arn:aws:s3:::${var.project_name}-kb-*/*"
        ]
      }
    ]
  })
}

# --- Admin: インフラ管理者 ---
resource "aws_ssoadmin_permission_set" "admin" {
  name             = "${var.project_name}-Admin"
  instance_arn     = local.sso_instance_arn
  session_duration = "PT1H"
  description      = "インフラ管理者（IaC変更・セキュリティ設定変更を含む）"
}

resource "aws_ssoadmin_managed_policy_attachment" "admin" {
  instance_arn       = local.sso_instance_arn
  managed_policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
  permission_set_arn = aws_ssoadmin_permission_set.admin.arn
}

# --- SecurityAdmin: セキュリティ専任管理者 ---
resource "aws_ssoadmin_permission_set" "security_admin" {
  name             = "${var.project_name}-SecurityAdmin"
  instance_arn     = local.sso_instance_arn
  session_duration = "PT1H"
  description      = "セキュリティ専任管理者（GuardDuty・Security Hub・CloudTrail監査）"
}

resource "aws_ssoadmin_managed_policy_attachment" "security_audit" {
  instance_arn       = local.sso_instance_arn
  managed_policy_arn = "arn:aws:iam::aws:policy/SecurityAudit"
  permission_set_arn = aws_ssoadmin_permission_set.security_admin.arn
}

resource "aws_ssoadmin_managed_policy_attachment" "guardduty_full" {
  instance_arn       = local.sso_instance_arn
  managed_policy_arn = "arn:aws:iam::aws:policy/AmazonGuardDutyFullAccess"
  permission_set_arn = aws_ssoadmin_permission_set.security_admin.arn
}

# ====================================================
# SCP（Service Control Policy）ガードレール
#
# 役割: プリンシパル（ユーザー・ロール）が「できること」の上限を組織レベルで定義
# 管理アカウントのrootには適用されない点に注意
# SCPは権限を付与しない — あくまで「上限」を定義するだけ
# ====================================================

# ガードレール1: 組織からの脱退を禁止（誤操作・不正操作防止）
resource "aws_organizations_policy" "deny_leave_org" {
  name        = "DenyLeaveOrganization"
  description = "メンバーアカウントがOrganizationsから離脱することを禁止する"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "DenyLeaveOrg"
      Effect   = "Deny"
      Action   = ["organizations:LeaveOrganization"]
      Resource = "*"
    }]
  })
}

# ガードレール2: 承認済みリージョン以外でのリソース作成を禁止（データ主権・コスト管理）
resource "aws_organizations_policy" "deny_non_approved_regions" {
  name        = "DenyNonApprovedRegions"
  description = "承認済みリージョン（${join(", ", var.allowed_regions)}）以外でのリソース作成を禁止"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "DenyNonApprovedRegions"
      Effect = "Deny"
      # グローバルサービス（IAM・Route53・CloudFront等）は除外
      NotAction = [
        "iam:*", "organizations:*", "route53:*", "budgets:*",
        "waf:*", "cloudfront:*", "sts:*", "support:*",
        "trustedadvisor:*", "health:*", "account:*", "ce:*"
      ]
      Resource = "*"
      Condition = {
        StringNotEquals = {
          "aws:RequestedRegion" = var.allowed_regions
        }
      }
    }]
  })
}

# ガードレール3: メンバーアカウントのrootユーザー使用禁止
# （Identity Center経由のフェデレーションアクセスを強制）
resource "aws_organizations_policy" "deny_root_usage" {
  name        = "DenyRootUserUsage"
  description = "メンバーアカウントでのrootユーザー操作を全禁止（Identity Center経由のアクセスを強制）"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "DenyRootUserAll"
      Effect   = "Deny"
      Action   = ["*"]
      Resource = "*"
      Condition = {
        StringLike = {
          "aws:PrincipalArn" = ["arn:aws:iam::*:root"]
        }
      }
    }]
  })
}

# ガードレール4: GuardDutyの停止・削除を禁止（継続的脅威検知の保護）
resource "aws_organizations_policy" "protect_guardduty" {
  name        = "ProtectGuardDuty"
  description = "GuardDutyの停止・削除を禁止し、継続的な脅威検知を保証する"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "ProtectGuardDuty"
      Effect = "Deny"
      Action = [
        "guardduty:DeleteDetector",
        "guardduty:DisassociateFromMasterAccount",
        "guardduty:DisassociateFromAdministratorAccount",
        "guardduty:StopMonitoringMembers",
        "guardduty:UpdateDetector"
      ]
      Resource = "*"
    }]
  })
}

# ====================================================
# RCP（Resource Control Policy）ガードレール
#
# 役割: リソース視点での制御（SCPのプリンシパル制御を補完）
# 対応サービス（2025年時点）: S3・STS・KMS・Secrets Manager・SQS
# SCPは「誰が何をできるか」を制限
# RCPは「リソースに対して何ができるか」を制限（二重のガードレール）
#
# 要件: AWS Terraform Provider v5.60以降
# ====================================================

# 全S3アクセスにTLSを強制（転送中の暗号化を組織全体で保証）
resource "aws_organizations_policy" "rcp_enforce_s3_tls" {
  name        = "RCPEnforceS3TLS"
  description = "全S3バケットへのHTTPアクセスをリソース側で拒否（転送中暗号化の強制）"
  type        = "RESOURCE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "EnforceTLSForAllS3Access"
      Effect   = "Deny"
      Principal = { AWS = "*" }
      Action   = ["s3:*"]
      Resource = "*"
      Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
    }]
  })
}

# ====================================================
# SCP/RCP を組織ルートOUにアタッチ
# root_ou_id が未設定の場合はスキップ（安全設計）
# ====================================================
resource "aws_organizations_policy_attachment" "deny_leave_org" {
  count     = var.root_ou_id != "" ? 1 : 0
  policy_id = aws_organizations_policy.deny_leave_org.id
  target_id = var.root_ou_id
}

resource "aws_organizations_policy_attachment" "deny_non_approved_regions" {
  count     = var.root_ou_id != "" ? 1 : 0
  policy_id = aws_organizations_policy.deny_non_approved_regions.id
  target_id = var.root_ou_id
}

resource "aws_organizations_policy_attachment" "deny_root_usage" {
  count     = var.root_ou_id != "" ? 1 : 0
  policy_id = aws_organizations_policy.deny_root_usage.id
  target_id = var.root_ou_id
}

resource "aws_organizations_policy_attachment" "protect_guardduty" {
  count     = var.root_ou_id != "" ? 1 : 0
  policy_id = aws_organizations_policy.protect_guardduty.id
  target_id = var.root_ou_id
}

resource "aws_organizations_policy_attachment" "rcp_enforce_s3_tls" {
  count     = var.root_ou_id != "" ? 1 : 0
  policy_id = aws_organizations_policy.rcp_enforce_s3_tls.id
  target_id = var.root_ou_id
}

# ====================================================
# 出力
# ====================================================
output "sso_instance_arn" {
  description = "IAM Identity Center インスタンスARN"
  value       = local.sso_instance_arn
}

output "permission_set_operator_arn" {
  description = "Operator パーミッションセットARN（CI/CD連携・アカウント割り当てに使用）"
  value       = aws_ssoadmin_permission_set.operator.arn
}
