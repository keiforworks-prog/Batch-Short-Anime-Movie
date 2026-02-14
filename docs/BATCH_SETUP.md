# Batch 自動処理 セットアップガイド

## 概要

バッチ送信後、完了を自動検知して P2.5 (動画生成) → P3 (アップロード) を実行する仕組み。

```
あなた: Batch 送信 (P2-A)
    │
    ▼
Cloud Scheduler (5分ごと)
    │
    ▼
Checker Job: バッチ完了確認
    │
    │ 完了検知
    ▼
Post-flow Job: P2-B → P2.5 → P3
    │
    ▼
Google Drive に動画がアップロードされる
```

**費用: 月約$10**

---

## 1. 事前準備

### 1.1 Artifact Registry リポジトリ作成

```bash
gcloud artifacts repositories create batch-jobs \
    --repository-format=docker \
    --location=asia-northeast1 \
    --description="Batch processing jobs"
```

### 1.2 Secret Manager に API キーを登録

```bash
# OpenAI
echo -n "sk-xxx" | gcloud secrets create OPENAI_API_KEY --data-file=-

# Anthropic
echo -n "sk-ant-xxx" | gcloud secrets create ANTHROPIC_API_KEY --data-file=-

# MiniMax (動画生成用)
echo -n "xxx" | gcloud secrets create MINIMAX_API_KEY --data-file=-

# Google Drive 親フォルダID
echo -n "1ABC..." | gcloud secrets create GDRIVE_PARENT_FOLDER_ID --data-file=-
```

### 1.3 GCS バケット作成 (状態管理用)

```bash
gsutil mb -l asia-northeast1 gs://YOUR_PROJECT_ID-batch-status
```

---

## 2. イメージのビルドとデプロイ

### 2.1 Cloud Build でイメージをビルド

```bash
cd Batch-Short-Anime-Movie
gcloud builds submit --config=cloudbuild-batch.yaml
```

### 2.2 Checker Job を作成

```bash
gcloud run jobs create batch-checker-job \
    --image=asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/batch-jobs/batch-checker:latest \
    --region=asia-northeast1 \
    --memory=512Mi \
    --cpu=1 \
    --max-retries=1 \
    --task-timeout=300 \
    --set-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest \
    --set-env-vars=GCS_BUCKET_NAME=YOUR_PROJECT_ID-batch-status,GCP_PROJECT_ID=YOUR_PROJECT_ID,GCP_REGION=asia-northeast1
```

### 2.3 Post-flow Job を作成

```bash
gcloud run jobs create batch-post-flow-job \
    --image=asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/batch-jobs/batch-postflow:latest \
    --region=asia-northeast1 \
    --memory=2Gi \
    --cpu=2 \
    --max-retries=1 \
    --task-timeout=86400 \
    --set-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest,MINIMAX_API_KEY=MINIMAX_API_KEY:latest,GDRIVE_PARENT_FOLDER_ID=GDRIVE_PARENT_FOLDER_ID:latest \
    --set-env-vars=GCS_BUCKET_NAME=YOUR_PROJECT_ID-batch-status
```

---

## 3. Cloud Scheduler 設定

### 3.1 Scheduler を作成 (5分ごと)

```bash
gcloud scheduler jobs create http batch-checker-scheduler \
    --location=asia-northeast1 \
    --schedule="*/5 * * * *" \
    --uri="https://asia-northeast1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR_PROJECT_ID/jobs/batch-checker-job:run" \
    --http-method=POST \
    --oauth-service-account-email=YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com
```

### 3.2 Scheduler を有効化

```bash
gcloud scheduler jobs resume batch-checker-scheduler --location=asia-northeast1
```

---

## 4. 使い方

### 4.1 バッチを送信

```bash
# ローカルで実行
python scripts/Batch/p2_gpt_batch_submit.py
```

これで自動的に `batch_status.json` が GCS にアップロードされます。

### 4.2 状態確認

```bash
# GCS の状態を確認
gsutil cat gs://YOUR_PROJECT_ID-batch-status/batch_status.json
```

### 4.3 手動で Checker を実行

```bash
gcloud run jobs execute batch-checker-job --region=asia-northeast1
```

---

## 5. トラブルシューティング

### ログを確認

```bash
# Checker Job のログ
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=batch-checker-job" --limit=50

# Post-flow Job のログ
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=batch-post-flow-job" --limit=50
```

### Scheduler を一時停止

```bash
gcloud scheduler jobs pause batch-checker-scheduler --location=asia-northeast1
```

### バッチ状態をリセット

```bash
gsutil rm gs://YOUR_PROJECT_ID-batch-status/batch_status.json
```

---

## 6. 費用の目安

| 項目 | 月間費用 |
|------|----------|
| Cloud Scheduler | 無料 (3ジョブまで) |
| Checker Job | ~$1 (5分ごと × 数秒) |
| Post-flow Job | ~$8 (98本 × 30分) |
| GCS | ~$0.01 |
| **合計** | **~$10/月** |

---

## 7. P2-A 送信時の自動登録

`p2_gpt_batch_submit.py` は送信完了時に自動で GCS に登録します。

手動で登録する場合:

```python
from scripts.Batch.batch_checker_job import save_batch_status_to_gcs

status_data = {
    "projects": {
        "プロジェクト名": {
            "batch_id": "batch_xxx",
            "batch_type": "gpt_images",
            "status": "in_progress",
            "submitted_at": "2024-01-01T00:00:00",
            "output_dir": "/path/to/output",
            "model_name": "claude"
        }
    }
}
save_batch_status_to_gcs(status_data)
```
