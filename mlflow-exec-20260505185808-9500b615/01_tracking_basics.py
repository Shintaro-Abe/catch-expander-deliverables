# PoC品質 - 本番利用前に認証・セキュリティ設定を必ず見直してください
"""
MLflow Tracking 基礎チュートリアル
===================================
MLflowの最初の一歩。実験（Experiment）とRun（実行）を記録し、
UIで比較・可視化する方法を示します。

【前提】
    pip install mlflow scikit-learn pandas numpy
    mlflow server --host 0.0.0.0 --port 5000  # 別ターミナルで起動
"""

import numpy as np
import pandas as pd
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
import mlflow
import mlflow.sklearn

# ── サーバー接続設定 ────────────────────────────────────────────────────────
# MLFLOW_TRACKING_URI 環境変数でも設定可能（環境変数推奨）
TRACKING_URI = "http://localhost:5000"
EXPERIMENT_NAME = "iris-classification-demo"

mlflow.set_tracking_uri(TRACKING_URI)
mlflow.set_experiment(EXPERIMENT_NAME)


# ── データ準備 ──────────────────────────────────────────────────────────────
def load_data():
    """Irisデータセットを読み込み、学習用・評価用に分割する"""
    iris = load_iris(as_frame=True)
    X_train, X_test, y_train, y_test = train_test_split(
        iris.data, iris.target, test_size=0.2, random_state=42, stratify=iris.target
    )
    return X_train, X_test, y_train, y_test


# ── 方法1: autolog（自動ログ）─────────────────────────────────────────────
def run_with_autolog(X_train, X_test, y_train, y_test):
    """
    autologを使うと、1行でパラメータ・メトリクス・モデルを自動記録できます。
    scikit-learn / PyTorch / XGBoost など15以上のライブラリに対応。
    """
    print("\n[方法1] autolog による自動ログ")

    # autolog() を呼ぶだけで fit() 時に全情報が自動記録される
    mlflow.sklearn.autolog()

    with mlflow.start_run(run_name="autolog-logistic-regression"):
        model = LogisticRegression(solver="lbfgs", max_iter=1000, C=1.0)
        model.fit(X_train, y_train)

        # ↑ fit() の中でパラメータ・メトリクス・モデルアーティファクトが自動保存される
        # autolog では cross_val_score も自動実行されることがある点に注意

    print("  完了: UIで確認してください →", TRACKING_URI)


# ── 方法2: 手動ログ（詳細制御）─────────────────────────────────────────────
def run_with_manual_log(X_train, X_test, y_train, y_test):
    """
    log_param / log_metric / log_model を個別に呼ぶことで、
    何をいつ記録するかを完全に制御できます。
    """
    print("\n[方法2] 手動ログによる詳細制御")

    # autolog は無効化して手動制御に切り替える
    mlflow.sklearn.autolog(disable=True)

    param_grid = [
        {"n_estimators": 50,  "max_depth": 3},
        {"n_estimators": 100, "max_depth": 5},
        {"n_estimators": 200, "max_depth": None},
    ]

    for params in param_grid:
        with mlflow.start_run(run_name=f"rf-n{params['n_estimators']}-d{params['max_depth']}"):

            # ─── パラメータ記録 ──────────────────────────────────────────
            mlflow.log_params(params)
            mlflow.set_tag("model_type", "RandomForest")
            mlflow.set_tag("dataset", "iris")

            # ─── 学習 ───────────────────────────────────────────────────
            model = RandomForestClassifier(**params, random_state=42)
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)

            # ─── メトリクス記録 ─────────────────────────────────────────
            # step を指定すると時系列グラフとして可視化可能（エポックごとの損失など）
            metrics = {
                "accuracy":  accuracy_score(y_test, y_pred),
                "f1_macro":  f1_score(y_test, y_pred, average="macro"),
                "precision": precision_score(y_test, y_pred, average="macro"),
                "recall":    recall_score(y_test, y_pred, average="macro"),
            }
            mlflow.log_metrics(metrics)

            # ─── モデルアーティファクト保存 ─────────────────────────────
            # input_example を渡すと、モデルの入力スキーマが自動推論される
            mlflow.sklearn.log_model(
                sk_model=model,
                name="random_forest_model",
                input_example=X_test.iloc[:3],
            )

            print(f"  params={params} → accuracy={metrics['accuracy']:.4f}")

    print("  完了: UIで3つのRunを比較してください →", TRACKING_URI)


# ── 方法3: ネストしたRun（子Run）─────────────────────────────────────────
def run_nested_example(X_train, X_test, y_train, y_test):
    """
    親RunとネストされたRun（子Run）を組み合わせることで、
    複数フォールドのクロスバリデーション結果などをまとめて管理できます。
    """
    print("\n[方法3] ネストしたRun（親子関係）")

    mlflow.sklearn.autolog(disable=True)

    with mlflow.start_run(run_name="cv-parent-run") as parent_run:
        mlflow.set_tag("experiment_type", "cross_validation")

        fold_accuracies = []
        for fold in range(3):
            # nested=True で子Runを作成
            with mlflow.start_run(run_name=f"fold-{fold}", nested=True):
                # 簡易的にランダムサンプリングでフォールドをシミュレート
                idx = np.random.choice(len(X_train), size=int(len(X_train) * 0.8), replace=False)
                X_f, y_f = X_train.iloc[idx], y_train.iloc[idx]

                model = LogisticRegression(max_iter=1000)
                model.fit(X_f, y_f)
                acc = accuracy_score(y_test, model.predict(X_test))
                fold_accuracies.append(acc)

                mlflow.log_metric("fold_accuracy", acc)
                mlflow.log_param("fold_index", fold)

        # 親Runに集約メトリクスを記録
        mlflow.log_metric("mean_cv_accuracy", np.mean(fold_accuracies))
        mlflow.log_metric("std_cv_accuracy", np.std(fold_accuracies))
        print(f"  CV平均精度: {np.mean(fold_accuracies):.4f} ± {np.std(fold_accuracies):.4f}")

    print("  完了: 親Runを展開して子Runを確認してください →", TRACKING_URI)


# ── メイン実行 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    X_train, X_test, y_train, y_test = load_data()

    run_with_autolog(X_train, X_test, y_train, y_test)
    run_with_manual_log(X_train, X_test, y_train, y_test)
    run_nested_example(X_train, X_test, y_train, y_test)

    print(f"\nすべての実験が記録されました。UIで確認: {TRACKING_URI}")
    print(f"実験名: {EXPERIMENT_NAME}")
