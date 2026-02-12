import os
import json
import time
import base64
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from logger_utils import DualLogger
from project_utils import get_current_project_info, ensure_image_output_dir
from config import BATCH_CHECK_INTERVAL, BATCH_MAX_WAIT_TIME

load_dotenv()

# ログ設定
log_file = os.path.join(os.path.dirname(__file__), "..", "logs", "gpt_batch_retrieve.log")
logger = DualLogger(log_file, include_timestamp=True)

def load_batch_info(project_folder):
    """バッチ情報を読み込み"""
    batch_info_file = os.path.join(project_folder, "gpt_batch_info.json")
    
    if not os.path.exists(batch_info_file):
        raise FileNotFoundError("gpt_batch_info.json が見つかりません")
    
    with open(batch_info_file, "r", encoding="utf-8") as f:
        return json.load(f)

def check_batch_status(batch_id):
    """バッチのステータスを確認"""
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    batch = client.batches.retrieve(batch_id)
    return batch

def retrieve_batch_results(batch_id, project_folder):
    """バッチ結果を取得して画像を保存"""
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    logger.log(f"バッチ結果を取得中: {batch_id}")
    
    # バッチ情報を取得
    batch = client.batches.retrieve(batch_id)
    
    if not batch.output_file_id:
        raise Exception("出力ファイルIDが見つかりません")
    
    # 結果ファイルをダウンロード
    logger.log(f"結果ファイルをダウンロード中: {batch.output_file_id}")
    file_response = client.files.content(batch.output_file_id)
    
    # 画像出力ディレクトリを作成
    project_name = os.path.basename(project_folder)
    image_output_dir = ensure_image_output_dir(project_name, "claude")
    
    # 結果を処理
    results = []
    for line in file_response.text.strip().split('\n'):
        result = json.loads(line)
        results.append(result)
    
    logger.log(f"取得成功: {len(results)}件")
    
    # 画像を保存
    success_count = 0
    failed_count = 0
    
    for result in results:
        try:
            custom_id = result["custom_id"]
            image_num = int(custom_id.split("_")[1])
            
            if result["response"]["status_code"] == 200:
                # 画像データを取得
                b64_data = result["response"]["body"]["data"][0]["b64_json"]
                image_data = base64.b64decode(b64_data)
                
                # ファイル保存
                filename = f"{image_num:03d}.png"
                filepath = os.path.join(image_output_dir, filename)
                
                with open(filepath, "wb") as f:
                    f.write(image_data)
                
                logger.log(f"✅ 画像保存: {filename}")
                success_count += 1
            else:
                logger.log(f"⚠️ 失敗: {custom_id}")
                failed_count += 1
                
        except Exception as e:
            logger.log(f"⚠️ エラー: {custom_id} - {str(e)}")
            failed_count += 1
    
    logger.log(f"\n画像保存完了")
    logger.log(f"✅ 成功: {success_count}枚")
    if failed_count > 0:
        logger.log(f"⚠️ 失敗: {failed_count}枚")
    
    # バッチ情報を更新
    batch_info_file = os.path.join(project_folder, "gpt_batch_info.json")
    with open(batch_info_file, "r", encoding="utf-8") as f:
        batch_info = json.load(f)
    
    batch_info["status"] = "completed"
    batch_info["completed_at"] = datetime.now().isoformat()
    batch_info["success_count"] = success_count
    batch_info["failed_count"] = failed_count
    
    with open(batch_info_file, "w", encoding="utf-8") as f:
        json.dump(batch_info, f, ensure_ascii=False, indent=2)

def main():
    try:
        logger.log("=" * 50)
        logger.log("Phase 2 (GPT Batch): バッチ結果取得")
        logger.log("=" * 50)
        
        # プロジェクト情報取得
        project_name, project_folder = get_current_project_info()
        logger.log(f"プロジェクト: {project_name}")
        
        # バッチ情報読み込み
        batch_info = load_batch_info(project_folder)
        batch_id = batch_info["batch_id"]
        logger.log(f"バッチID: {batch_id}")
        
        # ステータス確認ループ
        start_time = time.time()
        while True:
            batch = check_batch_status(batch_id)
            status = batch.status
            
            logger.log(f"ステータス: {status}")
            
            if status == "completed":
                logger.log("✅ バッチ処理完了")
                break
            elif status in ["failed", "expired", "cancelled"]:
                logger.log(f"❌ バッチ失敗: {status}")
                logger.save_on_error()
                return False
            
            # タイムアウトチェック
            elapsed = time.time() - start_time
            if elapsed > BATCH_MAX_WAIT_TIME:
                logger.log("❌ タイムアウト")
                logger.save_on_error()
                return False
            
            # 待機
            logger.log(f"次回チェックまで {BATCH_CHECK_INTERVAL}秒待機...")
            time.sleep(BATCH_CHECK_INTERVAL)
        
        # 結果取得
        retrieve_batch_results(batch_id, project_folder)
        
        logger.log("=" * 50)
        logger.log("✅ バッチ取得完了")
        logger.log("=" * 50)
        
        return True
        
    except Exception as e:
        logger.log(f"❌ エラー: {str(e)}")
        logger.save_on_error()
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)