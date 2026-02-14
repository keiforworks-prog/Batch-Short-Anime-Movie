#!/usr/bin/env python3
"""
Phase 2 Batch Retrieve: 画像生成バッチ結果取得
OpenAI Batch API の結果を取得し、画像を保存

【主要機能】
1. バッチステータスのポーリング
2. 結果の取得と画像保存
3. Google Drive への即時アップロード
4. コストトラッキング
5. チェックポイント更新
"""
import os
import sys
import json
import time
import base64
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

# 親ディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logger_utils import DualLogger
from project_utils import read_project_info, get_output_dir, ensure_image_output_dir
from config import (
    LOGS_DIR, BATCH_CHECK_INTERVAL, BATCH_MAX_WAIT_TIME
)
from cost_tracker import CostTracker
from gdrive_checkpoint import authenticate_gdrive, find_project_folder_on_drive

load_dotenv()

# モデル別価格
MODEL_PRICES = {
    "gpt-image-1": 0.25,
    "gpt-image-1-mini": 0.052
}


def load_batch_info(project_folder, logger):
    """
    バッチ情報を読み込み
    """
    batch_info_file = os.path.join(project_folder, "gpt_batch_info.json")
    
    if not os.path.exists(batch_info_file):
        logger.log(f"🚨 gpt_batch_info.json が見つかりません: {batch_info_file}")
        return None
    
    with open(batch_info_file, "r", encoding="utf-8") as f:
        return json.load(f)


def check_batch_status(batch_id, logger):
    """
    バッチのステータスを確認
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    batch = client.batches.retrieve(batch_id)
    
    # 進捗情報を表示
    if hasattr(batch, 'request_counts'):
        counts = batch.request_counts
        completed = getattr(counts, 'completed', 0)
        failed = getattr(counts, 'failed', 0)
        total = getattr(counts, 'total', 0)
        logger.log(f"  進捗: {completed}/{total} 完了, {failed} 失敗")
    
    return batch


def upload_image_to_drive(image_path, project_name, logger):
    """
    画像を Google Drive に即座にアップロード
    """
    try:
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.discovery import build
        
        parent_folder_id = os.getenv("GDRIVE_PARENT_FOLDER_ID")
        if not parent_folder_id:
            return
        
        creds = authenticate_gdrive()
        if not creds:
            return
        
        service = build('drive', 'v3', credentials=creds)
        
        # プロジェクトフォルダを検索
        project_folder_id = find_project_folder_on_drive(service, project_name, parent_folder_id)
        
        if not project_folder_id:
            # フォルダがない場合は作成
            folder_metadata = {
                'name': project_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_folder_id]
            }
            folder = service.files().create(body=folder_metadata, fields='id').execute()
            project_folder_id = folder.get('id')
        
        # images フォルダを検索または作成
        query = f"name='images' and '{project_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        files = results.get('files', [])
        
        if files:
            images_folder_id = files[0]['id']
        else:
            folder_metadata = {
                'name': 'images',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [project_folder_id]
            }
            folder = service.files().create(body=folder_metadata, fields='id').execute()
            images_folder_id = folder.get('id')
        
        # 画像ファイルをアップロード
        filename = os.path.basename(image_path)
        query = f"name='{filename}' and '{images_folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        existing_files = results.get('files', [])
        
        if existing_files:
            file_id = existing_files[0]['id']
            media = MediaFileUpload(image_path, mimetype='image/png')
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            file_metadata = {'name': filename, 'parents': [images_folder_id]}
            media = MediaFileUpload(image_path, mimetype='image/png')
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    
    except Exception as e:
        logger.log(f"⚠️ Drive アップロードエラー（続行します）: {e}")


def retrieve_batch_results(batch_id, project_folder, project_name, image_output_dir, logger, tracker):
    """
    バッチ結果を取得して画像を保存
    
    GPT Image モデルのレスポンス構造:
    {
        "response": {
            "status_code": 200,
            "body": {
                "created": 1234567890,
                "data": [
                    {
                        "b64_json": "base64エンコードされた画像データ"
                    }
                ]
            }
        }
    }
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    logger.log(f"\n📥 バッチ結果を取得中: {batch_id}")
    
    # バッチ情報を取得
    batch = client.batches.retrieve(batch_id)
    
    if not batch.output_file_id:
        logger.log("🚨 出力ファイルIDが見つかりません")
        return 0, 0
    
    # 結果ファイルをダウンロード
    logger.log(f"📥 結果ファイルをダウンロード中: {batch.output_file_id}")
    file_response = client.files.content(batch.output_file_id)
    
    # 結果を処理
    results = []
    for line in file_response.text.strip().split('\n'):
        if line.strip():
            result = json.loads(line)
            results.append(result)
    
    logger.log(f"✅ 取得成功: {len(results)} 件")
    
    # バッチリクエストファイルからモデル情報を事前に読み込む
    model_map = {}  # custom_id -> model
    batch_info = load_batch_info(project_folder, logger)
    if batch_info and "batch_file_path" in batch_info:
        batch_file_path = batch_info["batch_file_path"]
        if os.path.exists(batch_file_path):
            with open(batch_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    req = json.loads(line)
                    custom_id = req.get("custom_id", "")
                    model = req.get("body", {}).get("model", "")
                    model_map[custom_id] = model
    
    # 画像を保存
    success_count = 0
    failed_count = 0
    high_quality_count = 0
    mini_count = 0
    
    for result in results:
        custom_id = None
        try:
            custom_id = result["custom_id"]
            image_num = int(custom_id.split("_")[1])
            
            response = result.get("response", {})
            status_code = response.get("status_code", 0)
            
            if status_code == 200:
                # 画像データを取得（GPT Image モデルのレスポンス構造）
                body = response.get("body", {})
                data_list = body.get("data", [])
                
                # b64_json を探す
                b64_data = None
                if data_list:
                    # data[0] から b64_json を取得
                    b64_data = data_list[0].get("b64_json")
                
                if b64_data:
                    image_data = base64.b64decode(b64_data)
                    
                    # ファイル保存
                    filename = f"{image_num:03d}.png"
                    filepath = os.path.join(image_output_dir, filename)
                    
                    with open(filepath, "wb") as f:
                        f.write(image_data)
                    
                    logger.log(f"✅ 画像保存: {filename}")
                    success_count += 1
                    
                    # モデル判定（事前に読み込んだマップから取得）
                    model = model_map.get(custom_id, "")
                    if model == "gpt-image-1":
                        high_quality_count += 1
                    else:
                        mini_count += 1
                    
                    # Google Drive にアップロード
                    upload_image_to_drive(filepath, project_name, logger)
                else:
                    logger.log(f"⚠️ 画像データなし: {custom_id}")
                    logger.log(f"   レスポンス: {json.dumps(body, ensure_ascii=False)[:200]}...")
                    failed_count += 1
            else:
                error = response.get("error", {})
                error_msg = error.get("message", "不明なエラー")
                logger.log(f"⚠️ 失敗: {custom_id} - {error_msg}")
                failed_count += 1
                
        except Exception as e:
            logger.log(f"⚠️ エラー: {custom_id} - {str(e)}")
            import traceback
            logger.log(traceback.format_exc())
            failed_count += 1
    
    # コスト記録
    tracker.add_phase_2(
        images_generated=success_count,
        images_failed=failed_count,
        images_high_quality=high_quality_count,
        images_mini=mini_count
    )
    
    # バッチ情報を更新
    batch_info_file = os.path.join(project_folder, "gpt_batch_info.json")
    if os.path.exists(batch_info_file):
        with open(batch_info_file, "r", encoding="utf-8") as f:
            batch_info = json.load(f)
        
        batch_info["status"] = "completed"
        batch_info["completed_at"] = datetime.now().isoformat()
        batch_info["success_count"] = success_count
        batch_info["failed_count"] = failed_count
        
        with open(batch_info_file, "w", encoding="utf-8") as f:
            json.dump(batch_info, f, ensure_ascii=False, indent=2)
    
    return success_count, failed_count


def main():
    """メインの処理フロー"""
    # プロジェクト情報を取得
    project_name, model_name, _ = read_project_info()
    if not project_name:
        print("🚨 プロジェクト情報の取得に失敗しました")
        sys.exit(1)
    
    output_dir = get_output_dir(project_name, model_name)
    image_output_dir = ensure_image_output_dir(project_name, model_name)
    
    # ログ設定
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_file = os.path.join(LOGS_DIR, f"gpt_batch_retrieve_{project_name}.log")
    logger = DualLogger(log_file)
    
    # コストトラッカー
    tracker = CostTracker(project_name)
    
    try:
        logger.log(f"\n{'='*60}")
        logger.log(f"Phase 2-B (GPT Batch Retrieve): '{project_name}'")
        logger.log(f"{'='*60}")
        
        # API キー確認
        if not os.environ.get("OPENAI_API_KEY"):
            logger.log("🚨 エラー: OPENAI_API_KEY が設定されていません")
            sys.exit(1)
        
        # バッチ情報読み込み
        batch_info = load_batch_info(output_dir, logger)
        if not batch_info:
            sys.exit(1)
        
        batch_id = batch_info["batch_id"]
        logger.log(f"📋 バッチID: {batch_id}")
        logger.log(f"📋 送信日時: {batch_info.get('submitted_at', '不明')}")
        
        # ステータス確認ループ
        start_time = time.time()
        check_count = 0
        
        while True:
            check_count += 1
            logger.log(f"\n🔄 ステータス確認 #{check_count}")
            
            batch = check_batch_status(batch_id, logger)
            status = batch.status
            
            logger.log(f"  ステータス: {status}")
            
            if status == "completed":
                logger.log("\n✅ バッチ処理完了!")
                break
            elif status in ["failed", "expired", "cancelled"]:
                logger.log(f"\n❌ バッチ失敗: {status}")
                if hasattr(batch, 'errors') and batch.errors:
                    logger.log(f"  エラー詳細: {batch.errors}")
                logger.save_on_error()
                sys.exit(1)
            elif status == "in_progress":
                # 進捗表示
                pass
            
            # タイムアウトチェック
            elapsed = time.time() - start_time
            if elapsed > BATCH_MAX_WAIT_TIME:
                logger.log(f"\n❌ タイムアウト ({BATCH_MAX_WAIT_TIME}秒)")
                logger.save_on_error()
                sys.exit(1)
            
            # 待機
            remaining = BATCH_MAX_WAIT_TIME - elapsed
            logger.log(f"  次回チェックまで {BATCH_CHECK_INTERVAL}秒待機...")
            logger.log(f"  残り時間: {remaining/60:.1f}分")
            time.sleep(BATCH_CHECK_INTERVAL)
        
        # 結果取得
        success_count, failed_count = retrieve_batch_results(
            batch_id, output_dir, project_name, image_output_dir, logger, tracker
        )
        
        # コストサマリー
        logger.log(f"\n{tracker.get_detailed_summary()}")
        
        # 結果サマリー
        logger.log(f"\n{'='*60}")
        logger.log(f"📊 Phase 2-B 処理結果")
        logger.log(f"{'='*60}")
        logger.log(f"  - 成功: {success_count} 枚")
        logger.log(f"  - 失敗: {failed_count} 枚")
        logger.log(f"{'='*60}")
        
        if failed_count > 0:
            logger.log(f"⚠️ 一部の画像生成に失敗しましたが、処理を完了しました。")
        else:
            logger.log(f"🎉 全ての画像生成が正常に完了しました！")
        
        logger.log("\n--- Phase 2-B (GPT Batch Retrieve) が正常に完了しました ---")
        
        return True
        
    except Exception as e:
        logger.log(f"❌ エラー: {str(e)}")
        import traceback
        logger.log(traceback.format_exc())
        logger.save_on_error()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
