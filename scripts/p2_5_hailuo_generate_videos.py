"""
Phase 2.5: 動画生成 (MiniMax Hailuo 2.3 Fast)
- motion_prompts_list.txt + 画像ファイルから動画を生成
- MiniMax公式API使用 (I2V: Image-to-Video)
- 非同期タスク: 送信 → ポーリング → ダウンロード
- チェックポイント機能: 中断時に再開可能
"""

import os
import sys
import json
import time
import base64
import requests
from datetime import datetime

# === プロジェクト設定読み込み ===
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import (
    HAILUO_MODEL,
    HAILUO_RESOLUTION,
    HAILUO_DURATION,
    HAILUO_POLL_INTERVAL,
    HAILUO_MAX_WAIT_TIME,
    TEST_MODE_LIMIT,
    LOGS_DIR,
    LOG_PREFIX_ERROR,
)
from project_utils import get_current_project_info
from logger_utils import DualLogger
from cost_tracker import CostTracker
from dotenv import load_dotenv

# .envファイルを明示的に読み込み（scriptsフォルダからの実行対応）
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
load_dotenv(os.path.join(_project_root, ".env"))
load_dotenv()  # カレントディレクトリの.envも読む

# === API設定 ===
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "").strip().replace('\ufeff', '').replace('\r', '').replace('\n', '')
MINIMAX_BASE_URL = "https://api.minimax.io/v1"

HEADERS = {
    "Authorization": f"Bearer {MINIMAX_API_KEY}",
    "Content-Type": "application/json",
}


def image_to_base64_url(image_path):
    """画像ファイルをBase64データURLに変換"""
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    # 拡張子からMIMEタイプを判定
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    mime = mime_map.get(ext, "image/png")
    return f"data:{mime};base64,{data}"


def submit_video_task(prompt, image_path, logger):
    """
    動画生成タスクを送信
    Returns: task_id (str) or None
    """
    # 画像をBase64に変換
    first_frame = image_to_base64_url(image_path)

    payload = {
        "model": HAILUO_MODEL,
        "prompt": prompt,
        "first_frame_image": first_frame,
        "resolution": HAILUO_RESOLUTION,
        "duration": HAILUO_DURATION,
    }

    try:
        resp = requests.post(
            f"{MINIMAX_BASE_URL}/video_generation",
            headers=HEADERS,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        if "task_id" in data:
            return data["task_id"]
        else:
            logger.log(f"タスク送信エラー: {data}")
            return None

    except requests.exceptions.RequestException as e:
        logger.log(f"API通信エラー: {e}")
        return None


def poll_task_status(task_id, logger):
    """
    タスクの完了をポーリング
    Returns: file_id (str) or None
    """
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed > HAILUO_MAX_WAIT_TIME:
            logger.log(f"タイムアウト ({HAILUO_MAX_WAIT_TIME}秒超過)")
            return None

        time.sleep(HAILUO_POLL_INTERVAL)

        try:
            resp = requests.get(
                f"{MINIMAX_BASE_URL}/query/video_generation",
                headers=HEADERS,
                params={"task_id": task_id},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "Unknown")

            if status == "Success":
                file_id = data.get("file_id")
                if file_id:
                    return file_id
                else:
                    logger.log(f"Successだがfile_idなし: {data}")
                    return None

            elif status == "Fail":
                error_msg = data.get("error_message", "不明なエラー")
                logger.log(f"生成失敗: {error_msg}")
                return None

            elif status in ("Preparing", "Processing", "Waiting", "Queueing"):
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                logger.log(f"  ステータス: {status} ({mins}分{secs}秒経過)")

            else:
                logger.log(f"  未知のステータス: {status}")

        except requests.exceptions.RequestException as e:
            logger.log(f"ポーリングエラー（リトライ）: {e}")


def download_video(file_id, output_path, logger):
    """
    file_idから動画ファイルをダウンロード
    Returns: True/False
    """
    try:
        resp = requests.get(
            f"{MINIMAX_BASE_URL}/files/retrieve",
            headers=HEADERS,
            params={"file_id": file_id},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        download_url = data.get("file", {}).get("download_url")
        if not download_url:
            logger.log(f"ダウンロードURLなし: {data}")
            return False

        # 動画ファイルをダウンロード
        video_resp = requests.get(download_url, timeout=120)
        video_resp.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(video_resp.content)

        size_mb = len(video_resp.content) / (1024 * 1024)
        logger.log(f"  ダウンロード完了: {size_mb:.1f}MB")
        return True

    except requests.exceptions.RequestException as e:
        logger.log(f"ダウンロードエラー: {e}")
        return False


def load_checkpoint(checkpoint_path):
    """チェックポイントファイルを読み込み"""
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": [], "failed": [], "pending_tasks": {}}


def save_checkpoint(checkpoint_path, checkpoint_data):
    """チェックポイントファイルを保存"""
    with open(checkpoint_path, "w", encoding="utf-8") as f:
        json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)


def upload_video_to_drive(video_path, project_name, logger):
    """
    動画を Google Drive に即座にアップロード
    
    Args:
        video_path: ローカルの動画パス
        project_name: プロジェクト名
        logger: ロガー
    """
    try:
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.discovery import build
        from gdrive_checkpoint import authenticate_gdrive, find_project_folder_on_drive
        
        parent_folder_id = os.environ.get("GDRIVE_PARENT_FOLDER_ID")
        if not parent_folder_id:
            return
        
        creds = authenticate_gdrive()
        if not creds:
            return
        
        service = build('drive', 'v3', credentials=creds)
        
        # プロジェクトフォルダを検索
        project_folder_id = find_project_folder_on_drive(service, project_name, parent_folder_id)
        
        if not project_folder_id:
            folder_metadata = {
                'name': project_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_folder_id]
            }
            folder = service.files().create(body=folder_metadata, fields='id').execute()
            project_folder_id = folder.get('id')
        
        # videos フォルダを検索または作成
        query = f"name='videos' and '{project_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        folders = results.get('files', [])
        
        if folders:
            videos_folder_id = folders[0]['id']
        else:
            folder_metadata = {
                'name': 'videos',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [project_folder_id]
            }
            folder = service.files().create(body=folder_metadata, fields='id').execute()
            videos_folder_id = folder.get('id')
        
        # 既存ファイルを検索（上書き対応）
        filename = os.path.basename(video_path)
        query = f"name='{filename}' and '{videos_folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        existing_files = results.get('files', [])
        
        if existing_files:
            file_id = existing_files[0]['id']
            media = MediaFileUpload(video_path, mimetype='video/mp4')
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            file_metadata = {'name': filename, 'parents': [videos_folder_id]}
            media = MediaFileUpload(video_path, mimetype='video/mp4')
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        
        logger.log(f"  ☁️ Drive にアップロード完了: {filename}")
    
    except Exception as e:
        logger.log(f"  ⚠️ Drive アップロードエラー（続行）: {e}")


def main():
    if not MINIMAX_API_KEY:
        print("🚨 MINIMAX_API_KEY が設定されていません")
        sys.exit(1)

    # プロジェクトパス取得
    try:
        project_name, project_path = get_current_project_info()
    except Exception as e:
        print(f"🚨 プロジェクト情報取得失敗: {e}")
        sys.exit(1)

    if not project_path:
        print("🚨 プロジェクトパスが取得できません")
        sys.exit(1)

    # ログ設定
    log_file = os.path.join(LOGS_DIR, f"{LOG_PREFIX_ERROR}{project_name}_phase2_5_video.txt")
    logger = DualLogger(log_file)
    
    # コストトラッカー初期化
    tracker = CostTracker(project_name)

    # ファイルパス設定
    motion_prompts_path = os.path.join(project_path, "motion_prompts_list.txt")
    images_dir = os.path.join(project_path, "images")
    videos_dir = os.path.join(project_path, "videos")
    checkpoint_path = os.path.join(project_path, "video_checkpoint.json")
    log_path = os.path.join(project_path, "video_generation_log.json")

    # === Cloud Run 対応: ローカルにファイルがなければ Drive からダウンロード ===
    from gdrive_checkpoint import download_images_from_drive, download_motion_prompts_from_drive
    
    # motion_prompts_list.txt の確認とダウンロード
    if not os.path.exists(motion_prompts_path):
        logger.log(f"📁 ローカルに motion_prompts_list.txt がありません")
        logger.log(f"☁️  Google Drive からダウンロードを試みます...")
        
        if not download_motion_prompts_from_drive(project_name, motion_prompts_path, logger):
            logger.log(f"🚨 motion_prompts_list.txt が見つかりません（ローカルにもDriveにもない）")
            sys.exit(1)
    
    # images フォルダの確認とダウンロード
    images_exist = os.path.exists(images_dir) and len(os.listdir(images_dir)) > 0
    
    if not images_exist:
        logger.log(f"📁 ローカルに画像がありません")
        logger.log(f"☁️  Google Drive からダウンロードを試みます...")
        
        os.makedirs(images_dir, exist_ok=True)
        downloaded = download_images_from_drive(project_name, images_dir, logger)
        
        if downloaded == 0:
            logger.log(f"🚨 画像が見つかりません（ローカルにもDriveにもない）")
            sys.exit(1)
        
        logger.log(f"✅ {downloaded} 枚の画像をダウンロードしました")

    # モーションプロンプト読み込み
    with open(motion_prompts_path, "r", encoding="utf-8") as f:
        motion_prompts = [line.strip() for line in f if line.strip()]

    # 画像ファイル一覧（連番順）
    image_files = sorted(
        [f for f in os.listdir(images_dir) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))],
        key=lambda x: int(os.path.splitext(x)[0]) if os.path.splitext(x)[0].isdigit() else x,
    )

    # 数の整合性チェック
    if len(motion_prompts) != len(image_files):
        logger.log(
            f"プロンプト数({len(motion_prompts)})と画像数({len(image_files)})が不一致。"
            f"少ない方に合わせます。"
        )

    total = min(len(motion_prompts), len(image_files))

    # テストモード
    if TEST_MODE_LIMIT > 0:
        total = min(total, TEST_MODE_LIMIT)
        logger.log(f"⚠️  テストモード: {TEST_MODE_LIMIT}本のみ生成")

    logger.log(f"=" * 60)
    logger.log(f"Phase 2.5: 動画生成 (Hailuo {HAILUO_MODEL})")
    logger.log(f"  対象: {total}本")
    logger.log(f"  解像度: {HAILUO_RESOLUTION}, 長さ: {HAILUO_DURATION}秒")
    logger.log(f"  推定コスト: ${total * 0.14:.2f}")
    logger.log(f"  推定時間: {total * 1.5:.0f}〜{total * 3:.0f}分")
    logger.log(f"=" * 60)

    # 出力フォルダ作成
    os.makedirs(videos_dir, exist_ok=True)

    # チェックポイント読み込み
    checkpoint = load_checkpoint(checkpoint_path)
    completed_indices = set(checkpoint["completed"])

    # 生成ログ
    generation_log = []
    success_count = 0
    fail_count = 0
    skip_count = len(completed_indices)

    if skip_count > 0:
        logger.log(f"♻️  チェックポイントから再開: {skip_count}本スキップ")

    for i in range(total):
        idx = i + 1  # 1-based index
        padded = f"{idx:03d}"

        # チェックポイントでスキップ
        if idx in completed_indices:
            continue

        image_path = os.path.join(images_dir, image_files[i])
        prompt = motion_prompts[i]
        output_path = os.path.join(videos_dir, f"{padded}.mp4")

        logger.log(f"\n🎬 [{padded}/{total:03d}] 動画生成開始")
        logger.log(f"  プロンプト: {prompt[:80]}...")

        # Step 1: タスク送信
        task_id = submit_video_task(prompt, image_path, logger)
        if not task_id:
            logger.log(f"  ❌ タスク送信失敗")
            fail_count += 1
            checkpoint["failed"].append(idx)
            save_checkpoint(checkpoint_path, checkpoint)

            generation_log.append({
                "index": idx,
                "status": "submit_failed",
                "prompt": prompt,
                "timestamp": datetime.now().isoformat(),
            })
            continue

        logger.log(f"  📤 タスク送信完了: {task_id}")

        # チェックポイントに保留タスク記録
        checkpoint["pending_tasks"][str(idx)] = task_id
        save_checkpoint(checkpoint_path, checkpoint)

        # Step 2: ポーリング
        file_id = poll_task_status(task_id, logger)
        if not file_id:
            logger.log(f"  ❌ 生成失敗またはタイムアウト")
            fail_count += 1
            checkpoint["failed"].append(idx)
            checkpoint["pending_tasks"].pop(str(idx), None)
            save_checkpoint(checkpoint_path, checkpoint)

            generation_log.append({
                "index": idx,
                "status": "generation_failed",
                "task_id": task_id,
                "prompt": prompt,
                "timestamp": datetime.now().isoformat(),
            })
            continue

        # Step 3: ダウンロード
        if download_video(file_id, output_path, logger):
            logger.log(f"  ✅ 完了: {padded}.mp4")
            success_count += 1
            checkpoint["completed"].append(idx)
            checkpoint["pending_tasks"].pop(str(idx), None)
            save_checkpoint(checkpoint_path, checkpoint)
            
            # 即座に Drive にアップロード
            upload_video_to_drive(output_path, project_name, logger)

            generation_log.append({
                "index": idx,
                "status": "success",
                "task_id": task_id,
                "file_id": file_id,
                "output": f"{padded}.mp4",
                "prompt": prompt,
                "timestamp": datetime.now().isoformat(),
            })
        else:
            logger.log(f"  ❌ ダウンロード失敗")
            fail_count += 1
            checkpoint["failed"].append(idx)
            checkpoint["pending_tasks"].pop(str(idx), None)
            save_checkpoint(checkpoint_path, checkpoint)

            generation_log.append({
                "index": idx,
                "status": "download_failed",
                "task_id": task_id,
                "file_id": file_id,
                "prompt": prompt,
                "timestamp": datetime.now().isoformat(),
            })

    # === 結果サマリー ===
    # コスト記録
    video_model_type = "fast" if "Fast" in HAILUO_MODEL else "standard"
    tracker.add_phase_2_5(
        videos_generated=success_count,
        videos_failed=fail_count,
        model=video_model_type
    )
    
    logger.log(f"\n{'=' * 60}")
    logger.log(f"Phase 2.5 完了")
    logger.log(f"  ✅ 成功: {success_count}本")
    logger.log(f"  ❌ 失敗: {fail_count}本")
    logger.log(f"  ♻️  スキップ(既完了): {skip_count}本")
    logger.log(f"  💰 コスト: ${tracker.phase_2_5_cost:.2f} (約{int(tracker.phase_2_5_cost * tracker.USD_TO_JPY)}円)")
    logger.log(f"{'=' * 60}")

    # 生成ログ保存
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({
            "model": HAILUO_MODEL,
            "resolution": HAILUO_RESOLUTION,
            "duration": HAILUO_DURATION,
            "total": total,
            "success": success_count,
            "failed": fail_count,
            "skipped": skip_count,
            "estimated_cost_usd": tracker.phase_2_5_cost,
            "generated_at": datetime.now().isoformat(),
            "details": generation_log,
        }, f, ensure_ascii=False, indent=2)

    logger.log(f"ログ保存: {log_path}")

    # チェックポイント完了後削除
    if fail_count == 0 and len(checkpoint.get("pending_tasks", {})) == 0:
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
            logger.log("チェックポイント削除（全完了）")

    # 失敗があった場合はエラー終了
    if fail_count > 0:
        logger.log(f"⚠️  {fail_count}本の失敗あり。再実行で再開可能です。")
        sys.exit(1)


if __name__ == "__main__":
    main()
