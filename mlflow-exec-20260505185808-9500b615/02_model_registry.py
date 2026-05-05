# PoC品質 - 本番利用前に認証・セキュリティ設定を必ず見直してください
"""
MLflow Model Registry チュートリアル
======================================
モデルのバージョン管理・ライフサイクル管理（開発→本番）を担う
Model Registry の操作方法を示します。

【重要な概念】
  - Registered Model : モデルの名前付きエントリ（複数バージョンを持つ）
  - Model Version    : 登録のたびに自動採番（v1, v2, v3 ...）
  - Alias            : バージョンへの名前付き参照（champion / challenger）
                       旧来の Staging/Production ステージの代替として推奨

【前提】
    pip install mlflow scikit-learn
    mlflow server --host 0.0.0.0 --port 5000  # 事前に起動済みであること
"""

from sklearn.datasets import load_iris
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import mlflow
import mlflow.sklearn
from mlflow import MlflowClient

# ── 設定 ────────────────────────────────────────────────────────────────────
TRACKING_URI  = "http://localhost:5000"
MODEL_NAME    = "iris-classifier"
EXPERIMENT    = "registry-demo"

mlflow.set_tracking_uri(TRACKING_URI)
mlflow.set_experiment(EXPERIMENT)

client = MlflowClient(tracking_uri=TRACKING_URI)


# ── ユーティリティ ───────────────────────────────────────────────────────────
def get_data():
    iris = load_iris(as_frame=True)
    return train_test_split(iris.data, iris.target, test_size=0.2,
                            random_state=42, stratify=iris.target)


def train_and_register(model, run_name: str, X_train, X_test, y_train, y_test) -> str:
    """モデルを学習し、Registry に登録して model_version を返す"""
    mlflow.sklearn.autolog(disable=True)

    with mlflow.start_run(run_name=run_name) as run:
        model.fit(X_train, y_train)
        acc = accuracy_score(y_test, model.predict(X_test))
        mlflow.log_metric("accuracy", acc)

        # registered_model_name を指定すると、log_model が Registry への登録も行う
        mlflow.sklearn.log_model(
            sk_model=model,
            name="model",
            input_example=X_test.iloc[:3],
            registered_model_name=MODEL_NAME,   # ← これ1行でRegistryに登録
        )
        print(f"  [{run_name}] accuracy={acc:.4f}, run_id={run.info.run_id[:8]}...")
        return run.info.run_id


# ── ステップ1: 複数バージョンを登録 ─────────────────────────────────────────
def step1_register_multiple_versions():
    """
    3種類のモデルを学習して Registry に v1, v2, v3 として登録します。
    同じ MODEL_NAME に登録するたびにバージョン番号が自動インクリメントされます。
    """
    print("\n[Step1] 複数モデルを登録")
    X_train, X_test, y_train, y_test = get_data()

    models = [
        (LogisticRegression(max_iter=1000), "logistic-regression-v1"),
        (RandomForestClassifier(n_estimators=100, random_state=42), "random-forest-v2"),
        (GradientBoostingClassifier(n_estimators=100, random_state=42), "gradient-boosting-v3"),
    ]
    for model, name in models:
        train_and_register(model, name, X_train, X_test, y_train, y_test)

    # 登録済みバージョン一覧を確認
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    print(f"\n  登録バージョン数: {len(versions)}")
    for v in versions:
        print(f"    v{v.version}: source={v.source.split('/')[-1]}, status={v.status}")


# ── ステップ2: エイリアス設定（champion / challenger）──────────────────────
def step2_set_aliases():
    """
    エイリアスは「バージョンへの名前付きポインタ」です。
    コードを変更せずにエイリアスだけ差し替えることで本番モデルを切り替えられます。

    【旧来の Stage（Staging/Production）との違い】
      - Stage はバージョンごとに1つしか設定できない
      - Alias は複数設定でき、任意の名前を使える（例: champion / challenger / canary）
    """
    print("\n[Step2] エイリアス設定")

    # v1 を「現行本番」として champion に設定
    client.set_registered_model_alias(MODEL_NAME, "champion", 1)
    print(f"  v1 → alias 'champion' に設定（現行本番）")

    # v3 を「テスト中の新バージョン」として challenger に設定
    client.set_registered_model_alias(MODEL_NAME, "challenger", 3)
    print(f"  v3 → alias 'challenger' に設定（新バージョン検証中）")

    # タグでバリデーション状態を管理
    client.set_model_version_tag(MODEL_NAME, "1", "validation_status", "approved")
    client.set_model_version_tag(MODEL_NAME, "3", "validation_status", "pending_review")
    print("  タグ設定完了: validation_status")


# ── ステップ3: エイリアスでモデルをロード ───────────────────────────────────
def step3_load_by_alias():
    """
    URI: models:/<モデル名>@<エイリアス> でいつでも最新の本番モデルを参照できます。
    バージョン番号をハードコードする必要がなくなります。
    """
    print("\n[Step3] エイリアス経由でモデルをロード")
    _, X_test, _, y_test = get_data()

    # champion（現行本番）をロード
    champion = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@champion")
    champion_preds = champion.predict(X_test)
    champion_acc = accuracy_score(y_test, champion_preds)

    # challenger（新バージョン候補）をロード
    challenger = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@challenger")
    challenger_preds = challenger.predict(X_test)
    challenger_acc = accuracy_score(y_test, challenger_preds)

    print(f"  champion  (v1): accuracy={champion_acc:.4f}")
    print(f"  challenger(v3): accuracy={challenger_acc:.4f}")
    return champion_acc, challenger_acc


# ── ステップ4: 本番切り替え（champion 更新）────────────────────────────────
def step4_promote_champion(champion_acc: float, challenger_acc: float):
    """
    challenger の精度が champion を超えた場合、エイリアスを付け替えるだけで
    本番モデルをノーダウンタイムで更新できます。
    """
    print("\n[Step4] 本番モデル更新の意思決定")

    threshold = 0.01  # 1%以上改善した場合のみ切り替える
    if challenger_acc - champion_acc > threshold:
        # champion エイリアスを v3 に付け替える
        client.set_registered_model_alias(MODEL_NAME, "champion", 3)
        # 旧 champion に archived タグを付ける
        client.set_model_version_tag(MODEL_NAME, "1", "validation_status", "archived")
        client.set_model_version_tag(MODEL_NAME, "3", "validation_status", "approved")
        print(f"  ✓ challenger が champion より {challenger_acc - champion_acc:.4f} 優れているため昇格")
        print(f"    champion を v1 → v3 に更新しました")
    else:
        print(f"  champion を維持（改善幅 {challenger_acc - champion_acc:.4f} < 閾値 {threshold}）")


# ── ステップ5: マルチ環境プロモーション（Staging → Production）───────────────
def step5_multi_env_promotion():
    """
    copy_model_version() を使うと、dev/staging/prod など別々の
    Registered Model（別環境）にモデルを昇格コピーできます。

    【構成例】
      iris-classifier-dev    → 開発者が自由に実験
      iris-classifier-staging → QAチームが検証
      iris-classifier-prod   → 本番サービスが参照
    """
    print("\n[Step5] マルチ環境プロモーション（概念説明）")
    print("  以下の copy_model_version() は実際のAWS環境で動作します")
    print("  ローカルでは示唆のみ:\n")

    code = """
    # staging 環境の 'candidate' エイリアスのバージョンを production にコピー
    client.copy_model_version(
        src_model_uri="models:/iris-classifier-staging@candidate",
        dst_name="iris-classifier-prod"
    )
    # prod 環境で新バージョンを champion に設定
    client.set_registered_model_alias("iris-classifier-prod", "champion", new_version)
    """
    print(code)


# ── ステップ6: Registry の検索・一覧 ────────────────────────────────────────
def step6_search_registry():
    """MlflowClient でプログラム的にモデルを検索・管理する"""
    print("\n[Step6] Registry 検索")

    # 登録済みモデル一覧
    registered_models = client.search_registered_models()
    print(f"  登録済みモデル数: {len(registered_models)}")

    # 特定モデルの全バージョンを取得
    versions = client.search_model_versions(
        f"name='{MODEL_NAME}'",
        order_by=["version_number DESC"],
    )
    print(f"\n  {MODEL_NAME} のバージョン一覧:")
    for v in versions:
        tags = {t.key: t.value for t in v.tags}
        aliases = v.aliases
        print(f"    v{v.version}: aliases={aliases}, tags={tags}")


# ── メイン ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    step1_register_multiple_versions()
    step2_set_aliases()
    champion_acc, challenger_acc = step3_load_by_alias()
    step4_promote_champion(champion_acc, challenger_acc)
    step5_multi_env_promotion()
    step6_search_registry()

    print(f"\nModel Registry UI: {TRACKING_URI}/#/models")
