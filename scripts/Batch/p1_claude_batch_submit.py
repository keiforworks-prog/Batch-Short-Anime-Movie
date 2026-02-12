import os
import json
import anthropic
from datetime import datetime
from dotenv import load_dotenv
from config import CLAUDE_MODEL, CLAUDE_MAX_TOKENS
from logger_utils import DualLogger
from project_utils import get_current_project_info, read_file_safely

load_dotenv()

# ログ設定
log_file = os.path.join(os.path.dirname(__file__), "..", "logs", "batch_submit.log")
logger = DualLogger(log_file, include_timestamp=True)

def load_rules_and_settings(project_folder):
    """ルールファイルとキャラクター設定を読み込み"""
    base_dir = os.path.dirname(__file__)
    
    # キャラクター設定
    character_settings_file = os.path.join(project_folder, "character_settings.txt")
    character_settings = read_file_safely(character_settings_file, "キャラクター設定")
    if not character_settings:
        raise FileNotFoundError("キャラクター設定が見つかりません")
    
    # 画像生成ルール
    image_rules_file = os.path.join(base_dir, "rule", "image_rules.txt")
    image_rules = read_file_safely(image_rules_file, "画像生成ルール")
    if not image_rules:
        raise FileNotFoundError("画像生成ルールが見つかりません")
    
    return character_settings, image_rules

def load_script_lines(project_folder):
    """台本を1行ずつ読み込み"""
    # プロジェクトフォルダ内の台本ファイルを探す
    script_files = [f for f in os.listdir(project_folder) if f.endswith('.txt') and 'character_settings' not in f and 'prompts_list' not in f]
    
    if not script_files:
        raise FileNotFoundError("台本ファイルが見つかりません")
    
    script_file = os.path.join(project_folder, script_files[0])
    logger.log(f"台本ファイル: {script_file}")
    
    with open(script_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    
    return lines

def create_batch_requests(project_folder):
    """台本からバッチリクエストを作成"""
    # ルールと設定を読み込み
    character_settings, image_rules = load_rules_and_settings(project_folder)
    
    # 台本を読み込み
    script_lines = load_script_lines(project_folder)
    logger.log(f"台本行数: {len(script_lines)}行")
    
    # システムプロンプト
    system_prompt = f"""あなたは画像生成AIのプロンプトを作成する専門家です。

# キャラクター設定
{character_settings}

# 画像生成ルール
{image_rules}

台本の各行に対して、上記の設定とルールに従って高品質な画像生成プロンプトを1つ作成してください。
プロンプトのみを出力し、説明や補足は不要です。"""
    
    # バッチリクエストを作成
    requests = []
    for i, line in enumerate(script_lines, 1):
        request = {
            "custom_id": f"prompt_{i:03d}",
            "params": {
                "model": CLAUDE_MODEL,
                "max_tokens": CLAUDE_MAX_TOKENS,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": line}
                ]
            }
        }
        requests.append(request)
    
    logger.log(f"バッチリクエスト作成完了: {len(requests)}件")
    return requests

def submit_batch_job(requests, project_folder):
    """バッチジョブを送信"""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    
    logger.log(f"バッチジョブを送信中... (リクエスト数: {len(requests)})")
    
    # バッチ送信
    batch = client.messages.batches.create(requests=requests)
    
    batch_id = batch.id
    logger.log(f"✅ バッチ送信成功: {batch_id}")
    
    # バッチIDを保存
    batch_info = {
        "batch_id": batch_id,
        "submitted_at": datetime.now().isoformat(),
        "request_count": len(requests),
        "status": "processing"
    }
    
    batch_info_file = os.path.join(project_folder, "batch_info.json")
    with open(batch_info_file, "w", encoding="utf-8") as f:
        json.dump(batch_info, f, ensure_ascii=False, indent=2)
    
    logger.log(f"バッチ情報を保存: {batch_info_file}")
    return batch_id

def main():
    try:
        logger.log("=" * 50)
        logger.log("Phase 1 (Batch): プロンプト生成バッチ送信")
        logger.log("=" * 50)
        
        # プロジェクト情報取得
        project_name, project_folder = get_current_project_info()
        logger.log(f"プロジェクト: {project_name}")
        
        # バッチリクエスト作成
        requests = create_batch_requests(project_folder)
        
        # バッチ送信
        batch_id = submit_batch_job(requests, project_folder)
        
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