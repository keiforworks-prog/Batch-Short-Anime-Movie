import os
import json
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from logger_utils import DualLogger
from project_utils import get_current_project_info
from config import GPT_IMAGE_MODEL, IMAGE_SIZE, IMAGE_QUALITY

load_dotenv()

# ログ設定
log_file = os.path.join(os.path.dirname(__file__), "..", "logs", "gpt_batch_submit.log")
logger = DualLogger(log_file, include_timestamp=True)

def load_prompts(project_folder):
    """プロンプトリストを読み込み"""
    prompts_file = os.path.join(project_folder, "prompts_list.txt")
    
    if not os.path.exists(prompts_file):
        raise FileNotFoundError(f"プロンプトファイルが見つかりません: {prompts_file}")
    
    with open(prompts_file, "r", encoding="utf-8") as f:
        prompts = [line.strip() for line in f if line.strip()]
    
    logger.log(f"プロンプト数: {len(prompts)}件")
    return prompts

def create_batch_file(prompts, project_folder):
    """バッチリクエスト用のJSONLファイルを作成"""
    batch_requests = []
    
    for i, prompt in enumerate(prompts, 1):
        request = {
            "custom_id": f"image_{i:03d}",
            "method": "POST",
            "url": "/v1/images/generations",
            "body": {
                "model": GPT_IMAGE_MODEL,
                "prompt": prompt,
                "size": IMAGE_SIZE,
                "quality": IMAGE_QUALITY,
                "response_format": "b64_json"
            }
        }
        batch_requests.append(request)
    
    # JSONLファイルを作成
    batch_file_path = os.path.join(project_folder, "gpt_batch_requests.jsonl")
    with open(batch_file_path, "w", encoding="utf-8") as f:
        for req in batch_requests:
            f.write(json.dumps(req) + "\n")
    
    logger.log(f"バッチファイル作成: {batch_file_path}")
    return batch_file_path

def submit_batch_job(batch_file_path, project_folder):
    """バッチジョブを送信"""
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    logger.log("バッチファイルをアップロード中...")
    
    # ファイルをアップロード
    with open(batch_file_path, "rb") as f:
        batch_input_file = client.files.create(
            file=f,
            purpose="batch"
        )
    
    logger.log(f"ファイルID: {batch_input_file.id}")
    
    # バッチジョブを作成
    logger.log("バッチジョブを送信中...")
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
        "status": "validating"
    }
    
    batch_info_file = os.path.join(project_folder, "gpt_batch_info.json")
    with open(batch_info_file, "w", encoding="utf-8") as f:
        json.dump(batch_info, f, ensure_ascii=False, indent=2)
    
    logger.log(f"バッチ情報を保存: {batch_info_file}")
    return batch_id

def main():
    try:
        logger.log("=" * 50)
        logger.log("Phase 2 (GPT Batch): 画像生成バッチ送信")
        logger.log("=" * 50)
        
        # プロジェクト情報取得
        project_name, project_folder = get_current_project_info()
        logger.log(f"プロジェクト: {project_name}")
        
        # プロンプト読み込み
        prompts = load_prompts(project_folder)
        
        # バッチファイル作成
        batch_file_path = create_batch_file(prompts, project_folder)
        
        # バッチ送信
        batch_id = submit_batch_job(batch_file_path, project_folder)
        
        logger.log("=" * 50)
        logger.log(f"✅ バッチ送信完了")
        logger.log(f"バッチID: {batch_id}")
        logger.log(f"完了まで最大24時間かかります")
        logger.log("=" * 50)
        
        return True
        
    except Exception as e:
        logger.log(f"❌ エラー: {str(e)}")
        logger.save_on_error()
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)