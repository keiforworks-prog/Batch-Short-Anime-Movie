#!/usr/bin/env python3
"""
Phase 2 Batch Submit: 画像生成バッチリクエスト送信
prompts_data.jsonl から image_prompt を読み込み、OpenAI Batch API で送信

【主要機能】
1. JSONL形式 (prompts_data.jsonl) に対応
2. モデル選択ロジック（1枚目と最後2枚は高品質版）
3. Google Drive からのプロンプトダウンロード
4. チェックポイント機能（既存画像をスキップ）
5. コストトラッキング
"""
import os
import sys
import json
import glob
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

# 親ディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logger_utils import DualLogger
from project_utils import read_project_info, get_output_dir, ensure_image_output_dir
from config import (
    LOGS_DIR, GPT_IMAGE_MODEL, IMAGE_SIZE, IMAGE_QUALITY,
    TEST_MODE_LIMIT
)
from gdrive_checkpoint import check_drive_checkpoint, authenticate_gdrive, find_project_folder_on_drive

load_dotenv()

# モデル別価格
MODEL_PRICES = {
    "gpt-image-1": 0.25,      # 高品質版
    "gpt-image-1-mini": 0.052 # Mini版
}


def select_model_for_image(index, total_count):
    """
    画像のインデックスに応じてモデルを選択
    
    Args:
        index: 画像のインデックス（1から始まる）
        total_count: 総画像数
    
    Returns:
        tuple: (model_name, price_per_image)
    """
    # 1枚目または最後2枚の場合は高品質版
    if index == 1 or index >= total_count - 1:
        return "gpt-image-1", MODEL_PRICES["gpt-image-1"]
    else:
        return "gpt-image-1-mini", MODEL_PRICES["gpt-image-1-mini"]


def download_prompts_from_drive(project_name, output_file_path, logger):
    """
    Google Drive から prompts_data.jsonl をダウンロード
    """
    try:
        from googleapiclient.http import MediaIoBaseDownload
        from googleapiclient.discovery import build
        import io
        
        parent_folder_id = os.getenv("GDRIVE_PARENT_FOLDER_ID")
        if not parent_folder_id:
            return False
        
        creds = authenticate_gdrive()
        if not creds:
            return False
        
        service = build('drive', 'v3', credentials=creds)
        
        project_folder_id = find_project_folder_on_drive(service, project_name, parent_folder_id)
        if not project_folder_id:
            return False
        
        query = f"name='prompts_data.jsonl' and '{project_folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        files = results.get('files', [])
        
        if not files:
            logger.log(f"⚠️ Drive に prompts_data.jsonl が見つかりません。")
            return False
        
        file_id = files[0]['id']
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
        with open(output_file_path, 'wb') as f:
            f.write(fh.getvalue())
        
        logger.log(f"☁️  Drive から prompts_data.jsonl をダウンロードしました")
        return True
    
    except Exception as e:
        logger.log(f"⚠️ Drive ダウンロードエラー: {e}")
        return False


def load_prompts_from_jsonl(prompts_file_path, logger):
    """
    prompts_data.jsonl を読み込み、プロンプトのリストを抽出する
    """
    logger.log(f"🔄 プロンプトファイルを読み込み中: {prompts_file_path}")
    
    if not os.path.exists(prompts_file_path):
        logger.log(f"🚨 エラー: プロンプトファイルが見つかりません。")
        return []
    
    try:
        prompts = []
        with open(prompts_file_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    if "index" not in data or "image_prompt" not in data:
                        logger.log(f"⚠️ 行{line_num}: 必須フィールドがありません。スキップします。")
                        continue
                    prompts.append(data)
                except json.JSONDecodeError as e:
                    logger.log(f"⚠️ 行{line_num}: JSON解析エラー: {e}")
                    continue
        
        logger.log(f"✅ {len(prompts)} 個のプロンプトを読み込みました。")
        return prompts
    
    except Exception as e:
        logger.log(f"🚨 プロンプトファイルの読み込み中にエラーが発生しました: {e}")
        return []


def check_existing_images(image_output_dir, project_name, logger):
    """
    チェックポイント機能: ローカル → Google Drive の順で確認
    """
    existing_images = set()
    
    if os.path.exists(image_output_dir):
        try:
            local_images = glob.glob(os.path.join(image_output_dir, "*.png"))
            existing_images = set([os.path.basename(f) for f in local_images])
            
            if existing_images:
                logger.log(f"🔄 ローカルチェックポイント検出: {len(existing_images)} 枚の画像が既に生成済み")
                return existing_images
        except Exception as e:
            logger.log(f"⚠️ ローカルファイルの確認中にエラー: {e}")
    
    # Google Drive を確認
    try:
        parent_folder_id = os.getenv("GDRIVE_PARENT_FOLDER_ID")
        if parent_folder_id:
            drive_images = check_drive_checkpoint(project_name, parent_folder_id, checkpoint_type="images")
            if drive_images:
                existing_images = set(drive_images)
                logger.log(f"☁️  Google Drive チェックポイント検出: {len(existing_images)} 枚")
    except Exception as e:
        logger.log(f"⚠️ Drive チェックポイント確認中にエラー: {e}")
    
    return existing_images


def create_batch_file(prompts_to_process, total_count, project_folder, logger):
    """
    バッチリクエスト用のJSONLファイルを作成
    
    Args:
        prompts_to_process: 処理するプロンプトのリスト
        total_count: 総画像数（モデル選択用）
        project_folder: プロジェクトフォルダ
        logger: ロガー
    
    Returns:
        str: バッチファイルのパス
    """
    batch_requests = []
    
    for prompt_data in prompts_to_process:
        image_index = prompt_data["index"]
        image_prompt = prompt_data["image_prompt"]
        
        # モデル選択
        model, price = select_model_for_image(image_index, total_count)
        
        request = {
            "custom_id": f"image_{image_index:03d}",
            "method": "POST",
            "url": "/v1/images/generations",
            "body": {
                "model": model,
                "prompt": image_prompt,
                "size": IMAGE_SIZE,
                "quality": IMAGE_QUALITY,
                "output_format": "png"  # GPT Image モデルは response_format ではなく output_format を使用
            }
        }
        batch_requests.append(request)
        logger.log(f"  📝 画像 {image_index}: {model} (${price}/枚)")
    
    # JSONLファイルを作成
    batch_file_path = os.path.join(project_folder, "gpt_batch_requests.jsonl")
    with open(batch_file_path, "w", encoding="utf-8") as f:
        for req in batch_requests:
            f.write(json.dumps(req, ensure_ascii=False) + "\n")
    
    logger.log(f"\n✅ バッチファイル作成: {batch_file_path}")
    logger.log(f"   リクエスト数: {len(batch_requests)} 件")
    
    return batch_file_path


def submit_batch_job(batch_file_path, project_folder, logger):
    """
    バッチジョブを送信
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    logger.log("\n📤 バッチファイルをアップロード中...")
    
    # ファイルをアップロード
    with open(batch_file_path, "rb") as f:
        batch_input_file = client.files.create(
            file=f,
            purpose="batch"
        )
    
    logger.log(f"✅ ファイルアップロード完了: {batch_input_file.id}")
    
    # バッチジョブを作成
    logger.log("📤 バッチジョブを送信中...")
    batch = client.batches.create(
        input_file_id=batch_input_file.id,
        endpoint="/v1/images/generations",
        completion_window="24h"
    )
    
    batch_id = batch.id
    logger.log(f"✅ バッチ送信成功: {batch_id}")
    
    # バッチ情報を保存
    batch_info = {
        "batch_id": batch_id,
        "input_file_id": batch_input_file.id,
        "submitted_at": datetime.now().isoformat(),
        "status": "validating",
        "batch_file_path": batch_file_path
    }
    
    batch_info_file = os.path.join(project_folder, "gpt_batch_info.json")
    with open(batch_info_file, "w", encoding="utf-8") as f:
        json.dump(batch_info, f, ensure_ascii=False, indent=2)
    
    logger.log(f"✅ バッチ情報を保存: {batch_info_file}")
    
    return batch_id


def main():
    """メインの処理フロー"""
    # プロジェクト情報を取得
    project_name, model_name, _ = read_project_info()
    if not project_name:
        print("🚨 プロジェクト情報の取得に失敗しました")
        sys.exit(1)
    
    output_dir = get_output_dir(project_name, model_name)
    image_output_dir = ensure_image_output_dir(project_name, model_name)
    prompts_file = os.path.join(output_dir, "prompts_data.jsonl")
    
    # ログ設定
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_file = os.path.join(LOGS_DIR, f"gpt_batch_submit_{project_name}.log")
    logger = DualLogger(log_file)
    
    try:
        logger.log(f"\n{'='*60}")
        logger.log(f"Phase 2-A (GPT Batch Submit): '{project_name}'")
        logger.log(f"{'='*60}")
        
        # API キー確認
        if not os.environ.get("OPENAI_API_KEY"):
            logger.log("🚨 エラー: OPENAI_API_KEY が設定されていません")
            sys.exit(1)
        
        # prompts_data.jsonl を確認
        if not os.path.exists(prompts_file):
            logger.log("📁 ローカルに prompts_data.jsonl が見つかりません。")
            logger.log("☁️  Google Drive からダウンロードを試みます...")
            
            if not download_prompts_from_drive(project_name, prompts_file, logger):
                logger.log("🚨 エラー: prompts_data.jsonl が見つかりません")
                sys.exit(1)
        
        # プロンプトを読み込み
        prompt_data_list = load_prompts_from_jsonl(prompts_file, logger)
        if not prompt_data_list:
            logger.log("🚨 プロンプトが見つかりません")
            sys.exit(1)
        
        total_count = len(prompt_data_list)
        logger.log(f"📊 総画像数: {total_count} 枚")
        
        # チェックポイント確認
        existing_images = check_existing_images(image_output_dir, project_name, logger)
        
        # 全て完了している場合
        if len(existing_images) >= total_count:
            logger.log(f"\n✅ 全ての画像が既に生成済みです ({len(existing_images)}/{total_count})")
            logger.log(f"▶️  Phase 2 をスキップします")
            sys.exit(0)
        
        # 未完了分をフィルタリング
        prompts_to_process = []
        for prompt_data in prompt_data_list:
            filename = f"{prompt_data['index']:03d}.png"
            if filename not in existing_images:
                prompts_to_process.append(prompt_data)
        
        # テストモード対応
        if TEST_MODE_LIMIT > 0:
            logger.log(f"\n⚠️ テストモード: 最初の {TEST_MODE_LIMIT} 枚のみ処理します")
            prompts_to_process = prompts_to_process[:TEST_MODE_LIMIT]
        
        logger.log(f"\n📊 処理対象: {len(prompts_to_process)} 枚")
        logger.log(f"   既存: {len(existing_images)} 枚")
        logger.log(f"   スキップ: {total_count - len(prompts_to_process) - len(existing_images)} 枚")
        
        if not prompts_to_process:
            logger.log("✅ 処理対象の画像がありません")
            sys.exit(0)
        
        # バッチファイル作成
        logger.log(f"\n📝 バッチリクエストを作成中...")
        batch_file_path = create_batch_file(prompts_to_process, total_count, output_dir, logger)
        
        # バッチ送信
        batch_id = submit_batch_job(batch_file_path, output_dir, logger)
        
        # コスト見積もり
        high_quality_count = 0
        mini_count = 0
        for prompt_data in prompts_to_process:
            model, _ = select_model_for_image(prompt_data["index"], total_count)
            if model == "gpt-image-1":
                high_quality_count += 1
            else:
                mini_count += 1
        
        estimated_cost = (high_quality_count * MODEL_PRICES["gpt-image-1"] + 
                         mini_count * MODEL_PRICES["gpt-image-1-mini"])
        
        logger.log(f"\n{'='*60}")
        logger.log(f"✅ バッチ送信完了")
        logger.log(f"{'='*60}")
        logger.log(f"  バッチID: {batch_id}")
        logger.log(f"  リクエスト数: {len(prompts_to_process)} 件")
        logger.log(f"  - 高品質版 (gpt-image-1): {high_quality_count} 枚")
        logger.log(f"  - Mini版 (gpt-image-1-mini): {mini_count} 枚")
        logger.log(f"  推定コスト: ${estimated_cost:.2f}")
        logger.log(f"  完了まで最大24時間かかります")
        logger.log(f"{'='*60}")
        
        # クローラーに登録（ローカル）
        try:
            from batch_crawler import register_batch
            register_batch(
                project_name=project_name,
                batch_id=batch_id,
                batch_type="gpt_images",
                output_dir=output_dir,
                model_name=model_name
            )
            logger.log(f"\n🔄 ローカルクローラーに登録しました")
            logger.log(f"   クローラーを起動: python batch_crawler.py start")
        except ImportError:
            logger.log(f"\n⚠️ batch_crawler モジュールが見つかりません（ローカル）")
        except Exception as e:
            logger.log(f"\n⚠️ ローカルクローラー登録エラー（続行）: {e}")
        
        # GCS にも登録（Cloud Run 用）
        try:
            from google.cloud import storage
            import json
            
            gcs_bucket = os.environ.get("GCS_BUCKET_NAME")
            if gcs_bucket:
                client = storage.Client()
                bucket = client.bucket(gcs_bucket)
                blob = bucket.blob("batch_status.json")
                
                # 既存の状態を読み込み
                if blob.exists():
                    content = blob.download_as_text()
                    status_data = json.loads(content)
                else:
                    status_data = {"projects": {}}
                
                # プロジェクトを追加
                from datetime import datetime
                status_data["projects"][project_name] = {
                    "batch_id": batch_id,
                    "batch_type": "gpt_images",
                    "status": "in_progress",
                    "submitted_at": datetime.now().isoformat(),
                    "output_dir": output_dir,
                    "model_name": model_name
                }
                
                # 保存
                blob.upload_from_string(
                    json.dumps(status_data, ensure_ascii=False, indent=2),
                    content_type="application/json"
                )
                logger.log(f"☁️  GCS (Cloud Run用) に登録しました")
        except ImportError:
            logger.log(f"\n⚠️ google-cloud-storage がインストールされていません")
        except Exception as e:
            logger.log(f"\n⚠️ GCS 登録エラー（続行）: {e}")
        
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
