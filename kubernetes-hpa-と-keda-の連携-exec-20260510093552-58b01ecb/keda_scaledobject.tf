# PoC品質: このコードは学習・検証目的のスケルトン実装です。本番利用前に十分なレビューと調整が必要です。

# -------------------------------------------------------
# KEDA ScaledObject（SQS トリガー）
#
# ScaledObject の役割:
#   - KEDA Operator が読み取り、HPAを自動生成・管理する
#   - 0↔1 のスケール（Activation phase）: KEDA Operator が直接 Deployment を操作
#   - 1↔N のスケール（Scaling phase）: 自動生成された HPA が担当
#
# 二段階スケーリングの流れ:
#   SQS メッセージなし → 0 Pod（KEDA が Deployment.spec.replicas=0 に書き換え）
#   SQS メッセージあり → 1 Pod（KEDA が Activation phase を担当）
#   メッセージ増加    → N Pod（HPA が queueLength 閾値に基づきスケールアウト）
# -------------------------------------------------------

resource "kubernetes_manifest" "trigger_authentication" {
  manifest = {
    apiVersion = "keda.sh/v1alpha1"
    kind       = "TriggerAuthentication"
    metadata = {
      name      = "${var.app_name}-trigger-auth"
      namespace = var.app_namespace
    }
    spec = {
      podIdentity = {
        # KEDA 2.13 以降の推奨値（旧: "aws-eks"）
        # KEDA Operator の IRSA ロールが SQS アクセス権を持つため、
        # Operator Identity モデルで TriggerAuthentication は実質不要だが、
        # Pod Identity モデルへの切り替えを想定して定義しておく
        provider = "aws"
      }
    }
  }

  depends_on = [helm_release.keda, kubernetes_namespace.app]
}

resource "kubernetes_manifest" "scaledobject_sqs" {
  manifest = {
    apiVersion = "keda.sh/v1alpha1"
    kind       = "ScaledObject"
    metadata = {
      name      = "${var.app_name}-scaledobject"
      namespace = var.app_namespace
    }
    spec = {
      scaleTargetRef = {
        apiVersion = "apps/v1"
        kind       = "Deployment"
        name       = "${var.app_name}-worker"
      }

      # ゼロスケール設定（0 にするとアイドル時 Pod がゼロになりコスト削減）
      # レイテンシ要件が厳しい場合は 1 以上を推奨（コールドスタート回避）
      minReplicaCount = var.min_replica_count
      maxReplicaCount = var.max_replica_count

      # pollingInterval: KEDAがSQSをチェックする間隔（秒）
      # 短すぎると Kubernetes API への負荷が増大する。デフォルト 30 秒から調整
      pollingInterval = var.polling_interval

      # cooldownPeriod: SQS が空になった後、0 にスケールダウンするまでの待機時間（秒）
      # 短すぎるとスケールスラッシング（激しい上下）が発生しうる
      cooldownPeriod = var.cooldown_period

      # フォールバック: メトリクス取得が連続 3 回失敗した場合、3 レプリカを維持
      # 本番環境では必ず設定することを推奨
      fallback = {
        failureThreshold = 3
        replicas         = 3
      }

      # HPA のスケールダウン安定化ウィンドウ設定
      # 急激なスケールダウンを防ぎ、スパイクトラフィックへの耐性を確保
      advanced = {
        horizontalPodAutoscalerConfig = {
          behavior = {
            scaleDown = {
              stabilizationWindowSeconds = 300
              policies = [
                {
                  type          = "Percent"
                  value         = 25
                  periodSeconds = 60
                }
              ]
            }
            scaleUp = {
              stabilizationWindowSeconds = 0
              policies = [
                {
                  type          = "Percent"
                  value         = 100
                  periodSeconds = 15
                }
              ]
            }
          }
        }
      }

      triggers = [
        {
          type = "aws-sqs-queue"

          # KEDA Operator Identity モデル使用時は authenticationRef を外す。
          # Pod Identity モデル使用時は以下のコメントを解除する:
          # authenticationRef = {
          #   name = kubernetes_manifest.trigger_authentication.manifest.metadata.name
          # }

          metadata = {
            # SQS キュー URL（環境変数からの参照も可能: queueURLFromEnv）
            queueURL = aws_sqs_queue.workload.url

            # Pod 1台あたりの目標メッセージ数
            # desiredReplicas = ceil((ApproximateNumberOfMessages + InFlight) / queueLength)
            queueLength = tostring(var.sqs_queue_length_target)

            # activationQueueLength: 0 → 1 Pod の起動トリガー閾値
            # 0 = メッセージが 1件でも来たら即スケールアップ
            activationQueueLength = "0"

            awsRegion = var.aws_region

            # In-flight（処理中）メッセージもカウントに含める
            # false にすると処理中メッセージが無視され過剰スケールアップが発生しうる
            scaleOnInFlight = "true"

            # 遅延メッセージ（DelaySeconds設定済み）はデフォルト除外
            scaleOnDelayed = "false"

            # Operator Identity モデル（TriggerAuthentication不要の簡潔な設定）
            # v2.13 で deprecated、v3 で削除予定
            identityOwner = "operator"
          }
        }
      ]
    }
  }

  depends_on = [
    helm_release.keda,
    kubernetes_deployment.worker,
    kubernetes_manifest.trigger_authentication
  ]
}

# -------------------------------------------------------
# 参考: Prometheus スケーラーを組み合わせた複合トリガー例
#
# 複数トリガーを定義した場合、HPA は最大値を採用する。
# 例: 「SQS キュー深度 OR CPU 使用率」のどちらかがしきい値超過でスケールアウト
#
# triggers = [
#   {
#     type = "aws-sqs-queue"
#     metadata = {
#       queueURL    = aws_sqs_queue.workload.url
#       queueLength = "5"
#       awsRegion   = var.aws_region
#     }
#   },
#   {
#     type = "prometheus"
#     metadata = {
#       serverAddress = "http://prometheus-server.monitoring.svc.cluster.local"
#       query         = "sum(rate(http_requests_total{namespace=\"demo\"}[2m]))"
#       threshold     = "100"
#     }
#   },
#   {
#     type = "cron"  # 業務時間帯は最低 5 台を確保（コールドスタート回避）
#     metadata = {
#       timezone       = "Asia/Tokyo"
#       start          = "0 9 * * 1-5"
#       end            = "0 18 * * 1-5"
#       desiredReplicas = "5"
#     }
#   }
# ]
# -------------------------------------------------------
