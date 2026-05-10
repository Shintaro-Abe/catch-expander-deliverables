## IaCコード（Terraform または CloudFormation）

# Kubernetes HPA と KEDA の連携 — Terraform PoC

> **PoC 品質**: このコードは学習・検証目的のスケルトン実装です。本番利用前に十分なレビューと調整が必要です。

---

## 概要

このリポジトリは **Amazon EKS** 上で **KEDA（Kubernetes Event-driven Autoscaling）** を使い、  
**AWS SQS キューの深度に応じて Pod を自動スケール**する構成を Terraform で実装したサンプルです。

---

## アーキテクチャ

```
[SQS キュー]
    │  メッセージ数を監視（sqs:GetQueueAttributes）
    ▼
[KEDA Operator]  ← IRSA（IAM Roles for Service Accounts）でアクセス
    │
    ├─ 0 ↔ 1 Pod（Activation Phase）: KEDA が Deployment を直接操作
    │
    └─ 自動生成 HPA
           │
           └─ 1 ↔ N Pod（Scaling Phase）: HPA がメトリクスに基づき制御

[Worker Pod] ← SQS からメッセージを受信・処理（IRSA 経由）
```

### なぜ HPA 単独ではゼロスケールできないのか

| 課題 | 説明 |
|------|------|
| **ゼロ除算問題** | HPA の計算式 `ceil(currentReplicas × currentMetric / desiredMetric)` は `currentReplicas=0` で破綻する |
| **メトリクス欠如** | Pod が 0 のとき収集対象がなく、スケールアップのトリガーを得られない（鶏と卵問題） |
| **HPA の minReplicas 制限** | 標準 HPA は `minReplicas: 0` を実用的にサポートしない |

**KEDA の解決策**: HPA の `minReplicas` を内部的に `1` に保ちつつ、  
`0 ↔ 1` のトランジションを KEDA Operator が直接 Deployment を操作することで実現します。

---

## ファイル構成

| ファイル | 内容 |
|----------|------|
| `main.tf` | EKS data source 参照、SQS キュー、KEDA Helm インストール、Worker Deployment |
| `variables.tf` | 全変数定義（スケーリングパラメータ含む） |
| `iam.tf` | KEDA Operator / Worker Pod の IRSA ロールとポリシー |
| `keda_scaledobject.tf` | `TriggerAuthentication` と `ScaledObject` の Kubernetes マニフェスト |

---

## 前提条件

- Terraform `>= 1.5`
- 稼働中の Amazon EKS クラスター（Kubernetes `>= 1.29`）
- EKS クラスターで **OIDC プロバイダー**が有効化済み
- `kubectl`、`helm` が利用可能な環境

---

## デプロイ手順

```bash
# 1. 変数ファイルを作成
cat > terraform.tfvars <<'EOF'
eks_cluster_name = "my-cluster"
aws_region       = "ap-northeast-1"
app_name         = "keda-demo"
worker_image     = "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/my-worker:latest"
EOF

# 2. 初期化・プレビュー・適用
terraform init
terraform plan
terraform apply
```

---

## 主要パラメータの調整指針

| パラメータ | デフォルト | 調整ポイント |
|-----------|-----------|-------------|
| `min_replica_count` | `0` | レイテンシ要件が厳しい場合は `1` に設定してコールドスタートを回避 |
| `max_replica_count` | `10` | SQS メッセージ処理の最大並列数に合わせて設定 |
| `sqs_queue_length_target` | `5` | Pod 1 台あたりのメッセージ処理能力に基づき調整 |
| `polling_interval` | `30` | 短くするとリアルタイム性向上・API 負荷増大のトレードオフ |
| `cooldown_period` | `300` | 短すぎるとスケールスラッシング（激しい上下）が発生 |

---

## メリット・デメリット

### メリット

- **ゼロスケールによるコスト削減**: アイドル時は Pod が 0 台になり、リソース消費ゼロ
- **イベント駆動スケーリング**: SQS キュー深度という実ビジネスメトリクスに直結したスケール
- **60種類以上のスケーラー対応**: Kafka、RabbitMQ、Prometheus 等へ容易に切り替え可能
- **フォールバック機構**: メトリクス取得失敗時も指定レプリカ数を維持して安定稼働

### デメリット・注意点

- **コールドスタート遅延**: 0→1 スケール時に 2〜15 秒以上の追加レイテンシが発生しうる
- **HA の制限**: KEDA 公式が「完全な HA はサポートしない」と明記（メトリクスサーバーはシングルアクティブ制約）
- **HPA スケールダウンポリシーとの競合**: ゼロスケール実行時、KEDA は HPA のスケールダウンポリシーを無視して強制的に 0 にする場合がある
- **Kafka 使用時のリバランシング**: スケールイベントのたびにコンシューマーグループのリバランシング（処理一時停止）が発生

---

## 本番導入時のアンチパターン

| アンチパターン | 問題 | 対策 |
|---------------|------|------|
| ゼロスケールを全サービスに適用 | レイテンシ SLA 違反 | `minReplicaCount: 1` でコールドスタートを回避 |
| `pollingInterval` を短く設定 | Kubernetes API への過負荷 | デフォルト 30 秒から調整 |
| 認証情報をハードコード | セキュリティリスク | IRSA / EKS Pod Identity を常に使用 |
| HPA と ScaledObject の二重管理 | リソース競合 | KEDA 管理の ScaledObject と手動作成 HPA を同一リソースに共存させない |
| ステージングなしで本番投入 | 設定ミスによる障害 | 本番トラフィックパターンを再現してテスト |

---

## スケーラー選択ガイド

| 要件 | 推奨スケーラー |
|------|---------------|
| AWS SQS キュー処理 | `aws-sqs-queue` |
| Kafka ストリーム処理 | `kafka` |
| 任意のカスタムメトリクス | `prometheus` |
| 業務時間帯のスケジュール | `cron` |
| CPU・メモリバウンド（ゼロスケール不要） | HPA 単独 |
| バッチ・ジョブ系ワークロード | `ScaledJob`（ScaledObject ではなく） |


## プログラムコード（Python またはユーザープロファイルの技術スタック）

# Kubernetes HPA と KEDA の連携 — PoC コード集

> **PoC 品質**: このリポジトリは学習・検証用のスケルトン実装です。本番利用前に認証・テスト・セキュリティレビューを実施してください。

---

## 概要

このリポジトリは **Kubernetes HPA (Horizontal Pod Autoscaler)** と **KEDA (Kubernetes Event-driven Autoscaling)** の連携動作を理解するための Python コード・マニフェストを提供します。

### HPA と KEDA の役割分担

```
[イベントなし]
     │
     ▼
 KEDA Operator ──── 0台を維持（ゼロスケール）
     │
     │  イベント検知
     ▼
 KEDA Operator ──── Deployment を 0→1 に直接パッチ（Phase 1）
     │
     │  メトリクス増加
     ▼
 HPA（KEDAが自動生成）──── 1→N にスケールアウト（Phase 2）
     │
     │  イベントなし + cooldownPeriod 経過
     ▼
 KEDA Operator ──── Deployment を N→0 に直接パッチ（ゼロスケール）
```

| フェーズ | 担当 | スケール範囲 |
|---------|------|------------|
| Activation Phase | KEDA Operator | 0 ↔ 1 |
| Scaling Phase | KEDA が管理する HPA | 1 ↔ N |

---

## ファイル構成

```
.
├── keda_hpa_monitor.py          # HPA・ScaledObject の状態監視スクリプト
├── keda_scaledobject_manager.py # ScaledObject の作成・更新・削除
├── keda_metrics_simulator.py    # スケーリングロジックのシミュレーター（クラスター不要）
├── manifests/
│   └── keda_scaled_objects.yaml # SQS・Kafka・Prometheus+Cron の ScaledObject 定義
└── README.md
```

---

## 前提条件

### Kubernetes クラスター
- Kubernetes 1.29 以上
- KEDA 2.13 以上がインストール済み

```bash
# KEDA インストール（Helm）
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda --namespace keda --create-namespace
```

### Python 環境

```bash
pip install kubernetes rich pyyaml
```

---

## 使い方

### 1. スケーリングシミュレーター（クラスター不要）

実際の Kubernetes クラスターなしに、KEDA の 2 フェーズスケーリングロジックと
`activationThreshold` の罠を確認できます。

```bash
python keda_metrics_simulator.py
```

**出力例（SQS ワークロードシナリオ）:**
```
Step  時刻(仮想)  メッセージ数   Active  前台数  後台数  アクション
   0          0s           0       No       0       0  idle (0 維持)
  10        300s         112      Yes       0       1  activate (0→1)  ◀ 変化あり
  11        330s         134      Yes       1      27  HPA スケーリング → 27台 ◀ 変化あり
  ...
  45       1350s           0       No      10      10  cooldown中 (残150s)
  50       1500s           0       No      10       0  cooldown完了 → 0台 ◀ 変化あり
```

### 2. HPA・ScaledObject の監視

```bash
# 1回だけ確認
python keda_hpa_monitor.py -n demo

# 30秒ごとに繰り返し監視
python keda_hpa_monitor.py -n demo --watch --interval 30

# Pod 内部から実行（in-cluster config）
python keda_hpa_monitor.py --in-cluster -n demo
```

### 3. ScaledObject の管理

```bash
# SQS ScaledObject を作成・確認するワークフロー例を実行
python keda_scaledobject_manager.py
```

コードから ScaledObject を動的に生成する例:

```python
from keda_scaledobject_manager import build_sqs_scaled_object, ScaledObjectManager
from kubernetes import config

config.load_kube_config()
manager = ScaledObjectManager()

so = build_sqs_scaled_object(
    name="my-worker-scaler",
    namespace="production",
    deployment_name="my-worker",
    queue_url="https://sqs.ap-northeast-1.amazonaws.com/123456789012/tasks",
    aws_region="ap-northeast-1",
    queue_length=5,
    min_replica_count=0,  # ゼロスケール有効
    max_replica_count=30,
)
manager.apply(so)
```

### 4. Kubernetes マニフェストの適用

```bash
# KEDA ScaledObject マニフェストを適用
kubectl apply -f manifests/keda_scaled_objects.yaml

# 適用後の確認
kubectl get scaledobject -n demo
kubectl get hpa -n demo  # KEDA が自動生成した HPA が表示される
```

---

## メリット・デメリット

### KEDA + HPA の採用メリット

| 観点 | 内容 |
|------|------|
| **ゼロスケール** | アイドル時に Pod を 0 台にでき、コストを大幅削減（特に開発・ステージング環境） |
| **豊富なトリガー** | Kafka・SQS・RabbitMQ・Prometheus など 70 種類以上のイベントソースに標準対応 |
| **HPA との共存** | KEDA は HPA を内部利用するため、既存の Kubernetes 運用知識をそのまま活かせる |
| **フォールバック** | メトリクス取得失敗時のデフォルトレプリカ数設定が可能 |
| **マルチトリガー** | Kafka + Cron など複数トリガーを組み合わせた多層スケーリングが実現できる |

### KEDA 導入のデメリット・注意点

| 課題 | 内容 | 対策 |
|------|------|------|
| **コールドスタート遅延** | 0→1 のアクティベート時に Pod 起動時間（2〜15秒以上）の遅延が発生 | `minReplicaCount: 1` を設定、またはイメージをスリム化 |
| **HA の制限** | メトリクスサーバーはシングルアクティブ制約あり、フェイルオーバー中はスケーリングが一時停止 | `--enable-aggregator-routing=true` を kube-apiserver に設定 |
| **cooldownPeriod と HPA の競合** | ゼロスケール時に HPA のスケールダウンポリシーが無視される（GitHub Issue #7204） | 保守的な cooldownPeriod を設定し挙動を事前確認 |
| **activationThreshold の罠** | `activationThreshold > threshold` の設定だと 0 台が固着する | `activationThreshold ≤ threshold` を守る |
| **Kafka リバランス** | スケールイベントごとにコンシューマーリバランスが発生し処理が一時停止 | 協調スティッキーリバランス戦略を採用 |

---

## AWS EKS での IRSA 設定

```bash
# 1. EKS OIDC プロバイダーを有効化（eksctl）
eksctl utils associate-iam-oidc-provider --cluster <cluster-name> --approve

# 2. KEDA Operator 用 IAM ロールを作成
aws iam create-role --role-name keda-operator \
  --assume-role-policy-document file://trust-policy.json

# 3. SQS 読み取り権限を付与（sqs:GetQueueAttributes のみ必要）
aws iam put-role-policy --role-name keda-operator \
  --policy-name keda-sqs-policy \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"sqs:GetQueueAttributes","Resource":"arn:aws:sqs:*:ACCOUNT_ID:*"}]}'

# 4. Helm で KEDA をインストール（IRSA 設定付き）
helm install keda kedacore/keda \
  --set podIdentity.aws.irsa.enabled=true \
  --set podIdentity.aws.irsa.roleArn=arn:aws:iam::ACCOUNT_ID:role/keda-operator \
  --namespace keda --create-namespace
```

---

## 参考リンク

- [KEDA 公式ドキュメント](https://keda.sh/docs/)
- [KEDA Scaler 一覧](https://keda.sh/docs/2.19/scalers/)
- [Kubernetes HPA ドキュメント](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [KEDA GitHub](https://github.com/kedacore/keda)


---

📝 [Notionで詳細を見る](https://www.notion.so/Kubernetes-HPA-KEDA-35c47b55202e814d84a8d984030b29b2)
