# Python 3.11の公式イメージを使用（軽量版）
FROM python:3.11-slim

# 作業ディレクトリを設定
WORKDIR /app

# システムパッケージの更新と必要なツールのインストール
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt をコピーして、ライブラリをインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーション全体をコピー
COPY . .

# run.sh に実行権限を与える
RUN chmod +x run.sh

# 環境変数の設定（UTF-8対応）
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV LANG=C.UTF-8

# デフォルトコマンド
CMD ["./run.sh"]