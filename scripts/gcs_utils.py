"""
Google Cloud Storage 関連のユーティリティ
"""
import os
from google.cloud import storage
from config import PROJECT_ROOT

# GCS設定
GCS_BUCKET_NAME = "ai-image-pipeline-scripts"
GCS_INPUT_FOLDER = os.environ.get("GCS_INPUT_FOLDER", "input-short/")

def get_gcs_client():
    """GCS クライアントを取得"""
    # Cloud Run でもローカルでもデフォルト認証を使う
    return storage.Client()

def list_gcs_scripts():
    """
    GCS の input フォルダ内の .txt ファイル一覧を取得
    
    Returns:
        list: ファイル名のリスト
    """
    client = get_gcs_client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blobs = bucket.list_blobs(prefix=GCS_INPUT_FOLDER)
    
    script_files = []
    for blob in blobs:
        if blob.name.endswith('.txt') and blob.name != GCS_INPUT_FOLDER:
            filename = os.path.basename(blob.name)
            script_files.append(filename)
    
    return sorted(script_files)

def download_gcs_script(filename, local_path):
    """
    GCS から台本をダウンロード
    
    Args:
        filename (str): ファイル名（例: はまたろう.txt）
        local_path (str): ローカル保存先パス
    
    Returns:
        bool: 成功時 True
    """
    try:
        client = get_gcs_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(f"{GCS_INPUT_FOLDER}{filename}")
        
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        blob.download_to_filename(local_path)
        
        print(f"✅ GCS からダウンロード: {filename}")
        return True
        
    except Exception as e:
        print(f"❌ GCS ダウンロードエラー: {e}")
        return False
