#!/usr/bin/env python3
"""
Batch Crawler: バッチ処理監視デーモン

【機能】
1. 複数プロジェクトのバッチ状態を監視
2. 完了検知時に後続フェーズを自動実行（P2-B → P2.5 → P3）
3. 状態管理ファイル（batch_status.json）で永続化
4. 失敗時のリトライとエラー通知

【使い方】
  python batch_crawler.py start    # デーモン開始
  python batch_crawler.py status   # 現在の状態を表示
  python batch_crawler.py add <project_name>  # 手動でプロジェクト追加
"""
import os
import sys
import json
import time
import subprocess
from datetime import datetime
from dotenv import load_dotenv

# 親ディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI
from config import BATCH_CHECK_INTERVAL, LOGS_DIR, PROJECT_ROOT
from logger_utils import DualLogger

load_dotenv()

# === 設定 ===
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATCH_STATUS_FILE = os.path.join(BASE_DIR, "batch_status.json")
CRAWLER_LOG_FILE = os.path.join(LOGS_DIR, "batch_crawler.log")

# 後続スクリプトのパス
P2_BATCH_RETRIEVE_SCRIPT = os.path.join(BASE_DIR, "Batch", "p2_gpt_batch_retrieve.py")
P2_5_VIDEO_SCRIPT = os.path.join(BASE_DIR, "p2_5_hailuo_generate_videos.py")
P3_UPLOAD_SCRIPT = os.path.join(BASE_DIR, "p3_gdrive_upload.py")


def load_batch_status():
    """
    バッチ状態ファイルを読み込み
    
    Returns:
        dict: {
            "projects": {
                "project_name": {
                    "batch_id": "batch_xxx",
                    "batch_type": "gpt_images",  # or "claude_prompts"
                    "status": "in_progress",  # validating, in_progress, completed, failed
                    "submitted_at": "2024-01-01T00:00:00",
                    "last_checked": "2024-01-01T00:00:00",
                    "output_dir": "/path/to/output",
                    "model_name": "claude"
                }
            }
        }
    """
    if os.path.exists(BATCH_STATUS_FILE):
        with open(BATCH_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"projects": {}}


def save_batch_status(status_data):
    """バッチ状態ファイルを保存"""
    with open(BATCH_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status_data, f, ensure_ascii=False, indent=2)


def register_batch(project_name, batch_id, batch_type, output_dir, model_name="claude"):
    """
    新しいバッチをクローラーに登録
    
    Args:
        project_name: プロジェクト名
        batch_id: OpenAI Batch ID
        batch_type: "gpt_images" or "claude_prompts"
        output_dir: 出力ディレクトリ
        model_name: モデル名
    """
    status_data = load_batch_status()
    
    status_data["projects"][project_name] = {
        "batch_id": batch_id,
        "batch_type": batch_type,
        "status": "validating",
        "submitted_at": datetime.now().isoformat(),
        "last_checked": None,
        "output_dir": output_dir,
        "model_name": model_name,
        "retry_count": 0
    }
    
    save_batch_status(status_data)
    print(f"✅ バッチ登録完了: {project_name} ({batch_id})")


def unregister_batch(project_name):
    """バッチをクローラーから削除"""
    status_data = load_batch_status()
    
    if project_name in status_data["projects"]:
        del status_data["projects"][project_name]
        save_batch_status(status_data)
        print(f"✅ バッチ削除: {project_name}")


def check_batch_status_api(batch_id, logger):
    """
    OpenAI API でバッチステータスを確認
    
    Returns:
        tuple: (status, batch_object)
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
            logger.log(f"  進捗: {completed}/{total} 完了, {failed} 失敗")
        
        return batch.status, batch
    
    except Exception as e:
        logger.log(f"⚠️ API エラー: {e}")
        return "error", None


def update_current_project(project_name, model_name, output_dir):
    """
    _current_project.json を更新（後続スクリプト用）
    """
    contact_note_file = os.path.join(BASE_DIR, "_current_project.json")
    
    data = {
        "project_name": project_name,
        "model_name": model_name,
        "script_full_path": "",  # バッチモードでは不要
        "start_time": time.time()
    }
    
    with open(contact_note_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_script(script_path, phase_name, logger):
    """
    スクリプトを実行
    
    Returns:
        bool: 成功時 True
    """
    if not os.path.exists(script_path):
        logger.log(f"🚨 スクリプトが見つかりません: {script_path}")
        return False
    
    logger.log(f"\n▶️ {phase_name} を実行中...")
    
    try:
        import locale
        system_encoding = locale.getpreferredencoding() or 'utf-8'
        
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            encoding=system_encoding,
            errors='replace',
            timeout=86400  # 24時間
        )
        
        if result.returncode == 0:
            logger.log(f"✅ {phase_name} 完了")
            return True
        else:
            logger.log(f"❌ {phase_name} 失敗 (終了コード: {result.returncode})")
            if result.stderr:
                logger.log(f"  エラー: {result.stderr[:500]}")
            return False
    
    except subprocess.TimeoutExpired:
        logger.log(f"❌ {phase_name} タイムアウト")
        return False
    except Exception as e:
        logger.log(f"❌ {phase_name} エラー: {e}")
        return False


def execute_post_batch_flow(project_name, batch_type, output_dir, model_name, logger):
    """
    バッチ完了後のフローを実行
    
    P2 (GPT Images) の場合:
        P2-B Retrieve → P2.5 Videos → P3 Upload
    
    P1 (Claude Prompts) の場合:
        P1-B Retrieve → P2 Images → ...
    """
    logger.log(f"\n{'='*60}")
    logger.log(f"🚀 後続フロー開始: {project_name}")
    logger.log(f"{'='*60}")
    
    # _current_project.json を更新
    update_current_project(project_name, model_name, output_dir)
    
    if batch_type == "gpt_images":
        # P2-B: バッチ結果取得
        if not run_script(P2_BATCH_RETRIEVE_SCRIPT, "Phase 2-B (GPT Batch Retrieve)", logger):
            logger.log("❌ P2-B 失敗。フロー中断。")
            return False
        
        # P2.5: 動画生成
        if not run_script(P2_5_VIDEO_SCRIPT, "Phase 2.5 (Video Generation)", logger):
            logger.log("⚠️ P2.5 失敗。アップロードは続行します。")
        
        # P3: Google Drive アップロード
        if not run_script(P3_UPLOAD_SCRIPT, "Phase 3 (Google Drive Upload)", logger):
            logger.log("❌ P3 失敗。")
            return False
    
    elif batch_type == "claude_prompts":
        # Claude バッチの場合は P1-B → P2 → P2.5 → P3
        # TODO: 実装
        logger.log("⚠️ Claude バッチの後続フローは未実装")
        return False
    
    logger.log(f"\n{'='*60}")
    logger.log(f"✅ 後続フロー完了: {project_name}")
    logger.log(f"{'='*60}")
    
    return True


def crawler_loop(logger):
    """
    メインのクローラーループ
    """
    logger.log(f"\n{'='*60}")
    logger.log(f"🔄 Batch Crawler 開始")
    logger.log(f"   チェック間隔: {BATCH_CHECK_INTERVAL}秒")
    logger.log(f"   状態ファイル: {BATCH_STATUS_FILE}")
    logger.log(f"{'='*60}")
    
    while True:
        try:
            status_data = load_batch_status()
            projects = status_data.get("projects", {})
            
            if not projects:
                logger.log(f"\n⏳ 監視対象のバッチがありません。待機中...")
                time.sleep(BATCH_CHECK_INTERVAL)
                continue
            
            logger.log(f"\n🔍 {len(projects)} 件のバッチを確認中...")
            
            completed_projects = []
            
            for project_name, project_info in projects.items():
                batch_id = project_info["batch_id"]
                current_status = project_info["status"]
                
                # 既に完了/失敗しているものはスキップ
                if current_status in ["completed", "failed", "expired", "cancelled"]:
                    continue
                
                logger.log(f"\n📋 {project_name}: {batch_id}")
                
                # API でステータス確認
                api_status, batch_obj = check_batch_status_api(batch_id, logger)
                
                # 状態を更新
                project_info["status"] = api_status
                project_info["last_checked"] = datetime.now().isoformat()
                
                if api_status == "completed":
                    logger.log(f"✅ バッチ完了: {project_name}")
                    completed_projects.append(project_name)
                
                elif api_status in ["failed", "expired", "cancelled"]:
                    logger.log(f"❌ バッチ失敗: {project_name} ({api_status})")
                    if batch_obj and hasattr(batch_obj, 'errors'):
                        logger.log(f"   エラー: {batch_obj.errors}")
                
                elif api_status == "error":
                    # API エラーの場合はリトライカウントを増やす
                    project_info["retry_count"] = project_info.get("retry_count", 0) + 1
                    if project_info["retry_count"] >= 5:
                        logger.log(f"❌ API エラーが続いています: {project_name}")
                
                else:
                    logger.log(f"  ステータス: {api_status}")
            
            # 状態を保存
            save_batch_status(status_data)
            
            # 完了したプロジェクトの後続フローを実行
            for project_name in completed_projects:
                project_info = projects[project_name]
                
                success = execute_post_batch_flow(
                    project_name,
                    project_info["batch_type"],
                    project_info["output_dir"],
                    project_info["model_name"],
                    logger
                )
                
                if success:
                    # 完了したプロジェクトを削除
                    unregister_batch(project_name)
                else:
                    # 失敗した場合は状態を更新
                    project_info["status"] = "post_flow_failed"
                    save_batch_status(status_data)
            
            # 次のチェックまで待機
            logger.log(f"\n⏳ 次のチェックまで {BATCH_CHECK_INTERVAL}秒待機...")
            time.sleep(BATCH_CHECK_INTERVAL)
        
        except KeyboardInterrupt:
            logger.log("\n\n🛑 Crawler 停止（Ctrl+C）")
            break
        
        except Exception as e:
            logger.log(f"\n🚨 予期せぬエラー: {e}")
            import traceback
            logger.log(traceback.format_exc())
            time.sleep(60)  # エラー時は1分待機


def show_status():
    """現在の状態を表示"""
    status_data = load_batch_status()
    projects = status_data.get("projects", {})
    
    print(f"\n{'='*60}")
    print(f"📊 Batch Crawler Status")
    print(f"{'='*60}")
    
    if not projects:
        print("  監視対象のバッチはありません。")
    else:
        for project_name, info in projects.items():
            print(f"\n📋 {project_name}")
            print(f"   Batch ID: {info['batch_id']}")
            print(f"   Type: {info['batch_type']}")
            print(f"   Status: {info['status']}")
            print(f"   Submitted: {info['submitted_at']}")
            print(f"   Last Checked: {info.get('last_checked', 'Never')}")
    
    print(f"\n{'='*60}")


def main():
    """メインエントリーポイント"""
    if len(sys.argv) < 2:
        print("使い方:")
        print("  python batch_crawler.py start   # クローラー開始")
        print("  python batch_crawler.py status  # 状態表示")
        print("  python batch_crawler.py add <project_name> <batch_id> <batch_type> <output_dir>")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "start":
        os.makedirs(LOGS_DIR, exist_ok=True)
        logger = DualLogger(CRAWLER_LOG_FILE)
        crawler_loop(logger)
    
    elif command == "status":
        show_status()
    
    elif command == "add":
        if len(sys.argv) < 6:
            print("使い方: python batch_crawler.py add <project_name> <batch_id> <batch_type> <output_dir>")
            sys.exit(1)
        
        project_name = sys.argv[2]
        batch_id = sys.argv[3]
        batch_type = sys.argv[4]
        output_dir = sys.argv[5]
        
        register_batch(project_name, batch_id, batch_type, output_dir)
    
    elif command == "remove":
        if len(sys.argv) < 3:
            print("使い方: python batch_crawler.py remove <project_name>")
            sys.exit(1)
        
        project_name = sys.argv[2]
        unregister_batch(project_name)
    
    else:
        print(f"不明なコマンド: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
