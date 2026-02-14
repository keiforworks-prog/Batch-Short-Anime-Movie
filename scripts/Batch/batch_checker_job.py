#!/usr/bin/env python3
"""
Batch Checker Job: Cloud Scheduler から定期実行される軽量ジョブ

【機能】
1. batch_status.json (Cloud Storage) から監視対象を取得
2. OpenAI API でバッチステータスを確認
3. 完了したプロジェクトがあれば Post-flow Job を起動

【Cloud Scheduler 設定】
- 頻度: */5 * * * * (5分ごと)
- ターゲット: Cloud Run Job
"""
import os
import sys
import json
from datetime import datetime
from dotenv import load_dotenv

# 親ディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI
from google.cloud import storage

load_dotenv()

# === 設定 ===
GCS_BUCKET = os.environ.get("GCS_BUCKET_NAME", "")
BATCH_STATUS_BLOB = "batch_status.json"
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
REGION = os.environ.get("GCP_REGION", "asia-northeast1")
POST_FLOW_JOB_NAME = "batch-post-flow-job"


def load_batch_status_from_gcs():
    """
    Cloud Storage から batch_status.json を読み込み
    """
    if not GCS_BUCKET:
        print("⚠️ GCS_BUCKET_NAME が設定されていません")
        return {"projects": {}}
    
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(BATCH_STATUS_BLOB)
        
        if not blob.exists():
            print("📁 batch_status.json が存在しません")
            return {"projects": {}}
        
        content = blob.download_as_text()
        return json.loads(content)
    
    except Exception as e:
        print(f"⚠️ GCS 読み込みエラー: {e}")
        return {"projects": {}}


def save_batch_status_to_gcs(status_data):
    """
    Cloud Storage に batch_status.json を保存
    """
    if not GCS_BUCKET:
        return
    
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(BATCH_STATUS_BLOB)
        
        blob.upload_from_string(
            json.dumps(status_data, ensure_ascii=False, indent=2),
            content_type="application/json"
        )
        print(f"✅ batch_status.json を GCS に保存しました")
    
    except Exception as e:
        print(f"⚠️ GCS 保存エラー: {e}")


def check_batch_status_api(batch_id):
    """
    OpenAI API でバッチステータスを確認
    """
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        batch = client.batches.retrieve(batch_id)
        
        # 進捗情報
        if hasattr(batch, 'request_counts'):
            counts = batch.request_counts
            completed = getattr(counts, 'completed', 0)
            failed = getattr(counts, 'failed', 0)
            total = getattr(counts, 'total', 0)
            print(f"  進捗: {completed}/{total} 完了, {failed} 失敗")
        
        return batch.status, batch
    
    except Exception as e:
        print(f"⚠️ API エラー: {e}")
        return "error", None


def trigger_post_flow_job(project_name, project_info):
    """
    Cloud Run Job (Post-flow) を起動
    """
    try:
        from google.cloud import run_v2
        
        client = run_v2.JobsClient()
        
        # Job 名
        job_name = f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{POST_FLOW_JOB_NAME}"
        
        # 環境変数でプロジェクト情報を渡す
        request = run_v2.RunJobRequest(
            name=job_name,
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[
                    run_v2.RunJobRequest.Overrides.ContainerOverride(
                        env=[
                            run_v2.EnvVar(name="TARGET_PROJECT_NAME", value=project_name),
                            run_v2.EnvVar(name="TARGET_BATCH_TYPE", value=project_info["batch_type"]),
                            run_v2.EnvVar(name="TARGET_OUTPUT_DIR", value=project_info["output_dir"]),
                            run_v2.EnvVar(name="TARGET_MODEL_NAME", value=project_info.get("model_name", "claude")),
                        ]
                    )
                ]
            )
        )
        
        operation = client.run_job(request=request)
        print(f"✅ Post-flow Job を起動しました: {project_name}")
        print(f"   Operation: {operation.operation.name}")
        
        return True
    
    except Exception as e:
        print(f"❌ Post-flow Job の起動に失敗: {e}")
        return False


def main():
    """メイン処理"""
    print(f"\n{'='*60}")
    print(f"🔍 Batch Checker Job 開始")
    print(f"   時刻: {datetime.now().isoformat()}")
    print(f"{'='*60}")
    
    # API キー確認
    if not os.environ.get("OPENAI_API_KEY"):
        print("🚨 OPENAI_API_KEY が設定されていません")
        sys.exit(1)
    
    # batch_status.json を読み込み
    status_data = load_batch_status_from_gcs()
    projects = status_data.get("projects", {})
    
    if not projects:
        print("📁 監視対象のバッチがありません")
        print("✅ Checker Job 完了")
        return
    
    print(f"📋 {len(projects)} 件のバッチを確認中...")
    
    completed_projects = []
    
    for project_name, project_info in projects.items():
        batch_id = project_info["batch_id"]
        current_status = project_info.get("status", "unknown")
        
        # 既に完了/失敗しているものはスキップ
        if current_status in ["completed", "failed", "expired", "cancelled", "post_flow_started"]:
            continue
        
        print(f"\n📋 {project_name}")
        print(f"   Batch ID: {batch_id}")
        
        # API でステータス確認
        api_status, batch_obj = check_batch_status_api(batch_id)
        
        # 状態を更新
        project_info["status"] = api_status
        project_info["last_checked"] = datetime.now().isoformat()
        
        if api_status == "completed":
            print(f"✅ バッチ完了: {project_name}")
            completed_projects.append(project_name)
        
        elif api_status in ["failed", "expired", "cancelled"]:
            print(f"❌ バッチ失敗: {project_name} ({api_status})")
        
        else:
            print(f"   ステータス: {api_status}")
    
    # 状態を保存
    save_batch_status_to_gcs(status_data)
    
    # 完了したプロジェクトの Post-flow Job を起動
    for project_name in completed_projects:
        project_info = projects[project_name]
        
        if trigger_post_flow_job(project_name, project_info):
            # 状態を更新（重複起動防止）
            project_info["status"] = "post_flow_started"
            project_info["post_flow_started_at"] = datetime.now().isoformat()
    
    # 最終状態を保存
    if completed_projects:
        save_batch_status_to_gcs(status_data)
    
    print(f"\n{'='*60}")
    print(f"✅ Checker Job 完了")
    print(f"   完了検知: {len(completed_projects)} 件")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
