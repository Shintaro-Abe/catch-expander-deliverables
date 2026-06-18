# PoC品質: このDockerfileは学習・検証目的のスケルトンです。本番利用前にセキュリティレビューを実施してください。
#
# LLM推論サービス向け マルチステージビルド Dockerfile
# - ステージ1 (builder): 依存関係のインストール
# - ステージ2 (runtime): 最小限の実行環境（非rootユーザー）
#
# ビルド例:
#   docker build -t llm-service:latest .
#   docker build --target builder -t llm-service:debug .  # デバッグ用

# ===== ステージ1: ビルド環境 =====
FROM python:3.12-slim AS builder

# セキュリティ: apt キャッシュを残さない
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# キャッシュ最適化: 依存ファイルを先にコピーしてレイヤーキャッシュを活用
# （requirements.txt が変わらない限り pip install はキャッシュが効く）
COPY requirements.txt .

# --target でインストール先を指定 → ステージ2へ /app/libs としてコピー
RUN pip install --no-cache-dir --target=/build/libs -r requirements.txt


# ===== ステージ2: 実行環境（本番） =====
FROM python:3.12-slim AS runtime

# セキュリティ: 非rootユーザーを明示的なUID/GIDで作成
# UID 5000 を指定することで、ボリュームマウント時の権限を一致させやすくなる
ARG APP_UID=5000
ARG APP_GID=5000
RUN groupadd -g ${APP_GID} appgroup \
    && useradd -u ${APP_UID} -g appgroup -s /sbin/nologin -M appuser

WORKDIR /app

# ビルドステージのライブラリのみをコピー（コンパイラ等は含まない）
COPY --from=builder --chown=appuser:appgroup /build/libs /app/libs

# アプリケーションコードをコピー
COPY --chown=appuser:appgroup src/ /app/src/

# Python パスにライブラリを追加
ENV PYTHONPATH="/app/libs:${PYTHONPATH}"
# バッファリング無効化（ログがリアルタイムに出力される）
ENV PYTHONUNBUFFERED=1
# Hugging Face モデルキャッシュ先（ボリュームマウントポイントと一致させる）
ENV HF_HOME="/app/.cache/huggingface"
# pycache を書き込まない（読み取り専用FSとの相性向上）
ENV PYTHONDONTWRITEBYTECODE=1

# 非rootユーザーに切り替え（この命令以降はappuserとして実行）
USER appuser

# 1024番以下のポートはroot権限が必要なため 8000番を使用
# 本番ではリバースプロキシ（nginx/ALB）が80/443を受けてこのポートへ転送する
EXPOSE 8000

# ヘルスチェック: コンテナの死活監視
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# exec形式 CMD: シグナル（SIGTERM）が直接プロセスに届き、グレースフルシャットダウンが機能する
CMD ["python", "-m", "src.main"]
