# PoC品質: このコードは学習・検証目的のスケルトン実装です。本番利用前に十分なレビューと調整が必要です。

# -------------------------------------------------------
# IRSA（IAM Roles for Service Accounts）の仕組み
#
# [KEDA Operator Pod]
#   └── ServiceAccount (keda-operator)
#         └── IAM Role (keda-operator) ← OIDC で AssumeRoleWithWebIdentity
#               └── Policy: sqs:GetQueueAttributes のみ（最小権限）
#
# KEDA は SQS キューの属性取得のみを行うため、
# GetQueueAttributes だけあれば ApproximateNumberOfMessages 等を読み取れる
# -------------------------------------------------------

locals {
  oidc_provider_url = replace(
    data.aws_eks_cluster.this.identity[0].oidc[0].issuer,
    "https://", ""
  )
  account_id = data.aws_caller_identity.current.account_id
}

# -------------------------------------------------------
# KEDA Operator 用 IAM ロール
# -------------------------------------------------------
data "aws_iam_policy_document" "keda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.eks.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:sub"
      # keda Namespace の keda-operator ServiceAccount に限定（最小権限の原則）
      values = ["system:serviceaccount:${var.keda_namespace}:keda-operator"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "keda_operator" {
  name               = "${var.app_name}-keda-operator"
  assume_role_policy = data.aws_iam_policy_document.keda_trust.json
  description        = "KEDA Operator が SQS メトリクスを取得するための IRSA ロール"

  tags = var.common_tags
}

# KEDA が SQS キュー属性（メッセージ数等）を読み取るための最小権限ポリシー
resource "aws_iam_role_policy" "keda_sqs" {
  name = "${var.app_name}-keda-sqs-metrics"
  role = aws_iam_role.keda_operator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          # スケーリング判断に必要な属性（ApproximateNumberOfMessages等）を取得
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.workload.arn
      }
    ]
  })
}

# -------------------------------------------------------
# ワークロード（Worker Pod）用 IAM ロール
# Worker が SQS からメッセージを読み取り・削除するためのロール
# -------------------------------------------------------
data "aws_iam_policy_document" "worker_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.eks.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:${var.app_namespace}:${var.app_name}-worker"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "${var.app_name}-worker"
  assume_role_policy = data.aws_iam_policy_document.worker_trust.json
  description        = "Worker Pod が SQS メッセージを処理するための IRSA ロール"

  tags = var.common_tags
}

# Worker が SQS を操作するための最小権限ポリシー
resource "aws_iam_role_policy" "worker_sqs" {
  name = "${var.app_name}-worker-sqs"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = aws_sqs_queue.workload.arn
      }
    ]
  })
}

# -------------------------------------------------------
# outputs
# -------------------------------------------------------
output "keda_operator_role_arn" {
  description = "KEDA Operator IRSA ロール ARN"
  value       = aws_iam_role.keda_operator.arn
}

output "worker_role_arn" {
  description = "Worker Pod IRSA ロール ARN"
  value       = aws_iam_role.worker.arn
}

output "sqs_queue_url" {
  description = "SQS キュー URL"
  value       = aws_sqs_queue.workload.url
}

output "sqs_queue_arn" {
  description = "SQS キュー ARN"
  value       = aws_sqs_queue.workload.arn
}
