#!/usr/bin/env python3
"""
Batch Post-flow Job: バッチ完了後の処理を実行

【機能】
1. 環境変数からプロジェクト情報を取得
2. P2-B (Batch Retrieve) → P2.5 (Video Generation) → P3 (Upload) を実行
3. 完了後に batch_status.json を更新

【起動方法】
Checker Job から Cloud Run Job として起動される
環境変数:
  - TARGET_PROJECT_NAME: プロジェクト名
  - TARGET_BATCH_TYPE: バッチタイプ (gpt_images / claude_prompts)
  - TARGET_OUTPUT_DIR: 出力ディレクトリ
  - TARGET_MODEL_NAME: モデル名
"""
import os
import sys
import json
import subprocess
from datetime import datetime
from dotenv import load_dotenv

# 親ディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import storage

load_dotenv()

# === 設定 ===
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GCS_BUCKET = os.environ.get("GCS_BUCKET_NAME", "")
BATCH_STATUS_BLOB = "batch_status.json"

# スクリプトパス
P2_BATCH_RETRIEVE_SCRIPT = os.path.join(BASE_DIR, "Batch", "p2_gpt_batch_retrieve.py")
P2_5_VIDEO_SCRIPT = os.path.join(BASE_DIR, "p2_5_hailuo_generate_videos.py")
P3_UPLOAD_SCRIPT = os.path.join(BASE_DIR, "p3_gdrive_upload.py")
CONTACT_NOTE_FILE = os.path.join(BASE_DIR, "_current_project.json")


def update_current_project(project_name, model_name, output_dir):
    """
    _current_project.json を更新（後続スクリプト用）
    """
    import time
    
    data = {
        "project_name": project_name,
        "model_name": model_name,
        "script_full_path": "",
        "start_time": time.time()
    }
    
    with open(CONTACT_NOTE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"📝 _current_project.json を更新しました")


def run_script(script_path, phase_name):
    """
    スクリプトを実行
    """
    if not os.path.exists(script_path):
        print(f"🚨 スクリプトが見つかりません: {script_path}")
        return False
    
    print(f"\n{'='*50}")
    print(f"▶️ {phase_name} を実行中...")
    print(f"{'='*50}")
    
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=86400  # 24時間
        )
        
        # 出力を表示
        if result.stdout:
            print(result.stdout)
        
        if result.returncode == 0:
            print(f"✅ {phase_name} 完了")
            return True
        else:
            print(f"❌ {phase_name} 失敗 (終了コード: {result.returncode})")
            if result.stderr:
                print(f"エラー: {result.stderr[:1000]}")
            return False
    
    except subprocess.TimeoutExpired:
        print(f"❌ {phase_name} タイムアウト")
        return False
    except Exception as e:
        print(f"❌ {phase_name} エラー: {e}")
        return False


def update_batch_status_in_gcs(project_name, new_status):
    """
    GCS の batch_status.json を更新
    """
    if not GCS_BUCKET:
        return
    
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(BATCH_STATUS_BLOB)
        
        if not blob.exists():
            return
        
        content = blob.download_as_text()
        status_data = json.loads(content)
        
        if project_name in status_data.get("projects", {}):
            status_data["projects"][project_name]["status"] = new_status
            status_data["projects"][project_name]["completed_at"] = datetime.now().isoformat()
            
            blob.upload_from_string(
                json.dumps(status_data, ensure_ascii=False, indent=2),
                content_type="application/json"
            )
            print(f"✅ batch_status.json を更新しました: {project_name} → {new_status}")
    
    except Exception as e:
        print(f"⚠️ batch_status.json の更新に失敗: {e}")


def remove_from_batch_status(project_name):
    """
    GCS の batch_status.json からプロジェクトを削除
    """
    if not GCS_BUCKET:
        return
    
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(BATCH_STATUS_BLOB)
        
        if not blob.exists():
            return
        
        content = blob.download_as_text()
        status_data = json.loads(content)
        
        if project_name in status_data.get("projects", {}):
            del status_data["projects"][project_name]
            
            blob.upload_from_string(
                json.dumps(status_data, ensure_ascii=False, indent=2),
                content_type="application/json"
            )
            print(f"✅ {project_name} を batch_status.json から削除しました")
    
    except Exception as e:
        print(f"⚠️ batch_status.json からの削除に失敗: {e}")


def main():
    """メイン処理"""
    # 環境変数からプロジェクト情報を取得
    project_name = os.environ.get("TARGET_PROJECT_NAME")
    batch_type = os.environ.get("TARGET_BATCH_TYPE")
    output_dir = os.environ.get("TARGET_OUTPUT_DIR")
    model_name = os.environ.get("TARGET_MODEL_NAME", "claude")
    
    if not project_name:
        print("🚨 TARGET_PROJECT_NAME が設定されていません")
        sys.exit(1)
    
    print(f"\n{'='*60}")
    print(f"🚀 Post-flow Job 開始")
    print(f"   プロジェクト: {project_name}")
    print(f"   バッチタイプ: {batch_type}")
    print(f"   出力先: {output_dir}")
    print(f"   時刻: {datetime.now().isoformat()}")
    print(f"{'='*60}")
    
    # _current_project.json を更新
    update_current_project(project_name, model_name, output_dir)
    
    success = True
    
    if batch_type == "gpt_images":
        # P2-B: バッチ結果取得
        if not run_script(P2_BATCH_RETRIEVE_SCRIPT, "Phase 2-B (GPT Batch Retrieve)"):
            print("❌ P2-B 失敗")
            update_batch_status_in_gcs(project_name, "post_flow_failed")
            sys.exit(1)
        
        # P2.5: 動画生成
        if not run_script(P2_5_VIDEO_SCRIPT, "Phase 2.5 (Video Generation)"):
            print("⚠️ P2.5 失敗（アップロードは続行）")
            # P2.5 の失敗は致命的ではない
        
        # P3: Google Drive アップロード
        if not run_script(P3_UPLOAD_SCRIPT, "Phase 3 (Google Drive Upload)"):
            print("❌ P3 失敗")
            update_batch_status_in_gcs(project_name, "post_flow_failed")
            sys.exit(1)
    
    elif batch_type == "claude_prompts":
        # TODO: Claude バッチの後続フロー
        print("⚠️ Claude バッチの後続フローは未実装")
        update_batch_status_in_gcs(project_name, "post_flow_failed")
        sys.exit(1)
    
    else:
        print(f"🚨 不明なバッチタイプ: {batch_type}")
        sys.exit(1)
    
    # 成功したらステータスを削除
    remove_from_batch_status(project_name)
    
    print(f"\n{'='*60}")
    print(f"🎉 Post-flow Job 完了: {project_name}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
