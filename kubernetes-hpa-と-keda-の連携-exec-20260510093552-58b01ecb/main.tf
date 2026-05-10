# PoC品質: このコードは学習・検証目的のスケルトン実装です。本番利用前に十分なレビューと調整が必要です。

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# -------------------------------------------------------
# EKS クラスター（既存クラスターを data source で参照）
# 新規作成する場合は module "eks" { source = "terraform-aws-modules/eks/aws" } を使用
# -------------------------------------------------------
data "aws_eks_cluster" "this" {
  name = var.eks_cluster_name
}

data "aws_eks_cluster_auth" "this" {
  name = var.eks_cluster_name
}

data "aws_caller_identity" "current" {}

# OIDC プロバイダー ARN（IRSA 用）
data "aws_iam_openid_connect_provider" "eks" {
  url = data.aws_eks_cluster.this.identity[0].oidc[0].issuer
}

provider "kubernetes" {
  host                   = data.aws_eks_cluster.this.endpoint
  cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
  token                  = data.aws_eks_cluster_auth.this.token
}

provider "helm" {
  kubernetes {
    host                   = data.aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
    token                  = data.aws_eks_cluster_auth.this.token
  }
}

# -------------------------------------------------------
# SQS キュー（スケーリングのトリガーとなるキュー）
# -------------------------------------------------------
resource "aws_sqs_queue" "workload" {
  name                       = "${var.app_name}-queue"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 86400

  tags = var.common_tags
}

# -------------------------------------------------------
# KEDA インストール（Helm Chart）
# -------------------------------------------------------
resource "helm_release" "keda" {
  name             = "keda"
  repository       = "https://kedacore.github.io/charts"
  chart            = "keda"
  version          = var.keda_chart_version
  namespace        = var.keda_namespace
  create_namespace = true

  # IRSA: KEDA Operator ServiceAccount に IAM ロール ARN をアノテーション付与
  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.keda_operator.arn
  }

  # KEDA Operator の HA 設定（リーダーエレクション利用で最大 2 レプリカ）
  set {
    name  = "operator.replicaCount"
    value = "2"
  }

  # Prometheus メトリクスエンドポイントを有効化（監視用）
  set {
    name  = "prometheus.operator.enabled"
    value = "true"
  }

  depends_on = [aws_iam_role_policy.keda_sqs]
}

# -------------------------------------------------------
# サンプルワークロード用 Deployment（スケール対象）
# -------------------------------------------------------
resource "kubernetes_deployment" "worker" {
  metadata {
    name      = "${var.app_name}-worker"
    namespace = var.app_namespace
    labels = {
      app = "${var.app_name}-worker"
    }
  }

  spec {
    # KEDA が replicas を管理するため初期値を明示
    replicas = 1

    selector {
      match_labels = {
        app = "${var.app_name}-worker"
      }
    }

    template {
      metadata {
        labels = {
          app = "${var.app_name}-worker"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.worker.metadata[0].name

        container {
          name  = "worker"
          image = var.worker_image

          # HPA の CPU ベーススケーリングには requests の設定が必須
          resources {
            requests = {
              cpu    = "100m"
              memory = "128Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "512Mi"
            }
          }

          env {
            name  = "SQS_QUEUE_URL"
            value = aws_sqs_queue.workload.url
          }

          env {
            name  = "AWS_REGION"
            value = var.aws_region
          }
        }

        # ゼロスケールからの起動時、処理中ポッドの安全な停止のための猶予期間
        termination_grace_period_seconds = 60
      }
    }
  }
}

# ワークロード用 Namespace
resource "kubernetes_namespace" "app" {
  metadata {
    name = var.app_namespace
  }
}

# ワークロード用 ServiceAccount（IRSA によるSQSアクセス用）
resource "kubernetes_service_account" "worker" {
  metadata {
    name      = "${var.app_name}-worker"
    namespace = var.app_namespace
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.worker.arn
    }
  }

  depends_on = [kubernetes_namespace.app]
}
