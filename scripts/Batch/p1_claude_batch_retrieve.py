import os
import json
import time
import anthropic
from datetime import datetime
from dotenv import load_dotenv
from config import BATCH_CHECK_INTERVAL, BATCH_MAX_WAIT_TIME
from logger_utils import DualLogger
from project_utils import get_current_project_info

load_dotenv()

# ログ設定
log_file = os.path.join(os.path.dirname(__file__), "..", "logs", "batch_retrieve.log")
logger = DualLogger(log_file, include_timestamp=True)

def load_batch_info(project_folder):
    """バッチ情報を読み込み"""
    batch_info_file = os.path.join(project_folder, "batch_info.json")
    
    if not os.path.exists(batch_info_file):
        raise FileNotFoundError("batch_info.json が見つかりません")
    
    with open(batch_info_file, "r", encoding="utf-8") as f:
        return json.load(f)

def check_batch_status(batch_id):
    """バッチのステータスを確認"""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    batch = client.messages.batches.retrieve(batch_id)
    return batch

def retrieve_batch_results(batch_id, project_folder):
    """バッチ結果を取得して保存"""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    
    logger.log(f"バッチ結果を取得中: {batch_id}")
    
    # 結果を取得
    results = []
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            custom_id = result.custom_id
            content = result.result.message.content[0].text
            results.append({
                "custom_id": custom_id,
                "content": content
            })
        else:
            logger.log(f"失敗: {result.custom_id}")
    
    logger.log(f"取得成功: {len(results)}件")
    
    # prompts_list.txt に保存
    prompts_file = os.path.join(project_folder, "prompts_list.txt")
    with open(prompts_file, "w", encoding="utf-8") as f:
        for result in sorted(results, key=lambda x: x["custom_id"]):
            f.write(result["content"] + "\n")
    
    logger.log(f"プロンプト保存完了: {prompts_file}")
    
    # バッチ情報を更新
    batch_info_file = os.path.join(project_folder, "batch_info.json")
    with open(batch_info_file, "r", encoding="utf-8") as f:
        batch_info = json.load(f)
    
    batch_info["status"] = "completed"
    batch_info["completed_at"] = datetime.now().isoformat()
    batch_info["results_count"] = len(results)
    
    with open(batch_info_file, "w", encoding="utf-8") as f:
        json.dump(batch_info, f, ensure_ascii=False, indent=2)

def main():
    try:
        logger.log("=" * 50)
        logger.log("Phase 1 (Batch): バッチ結果取得")
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
            status = batch.processing_status
            
            logger.log(f"ステータス: {status}")
            
            if status == "ended":
                logger.log("✅ バッチ処理完了")
                break
            elif status in ["canceling", "canceled", "expired"]:
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