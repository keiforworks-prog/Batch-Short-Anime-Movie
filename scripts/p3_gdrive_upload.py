#!/usr/bin/env python3
"""
Phase 3: Google Drive アップロードスクリプト（JSONL形式対応版）
生成された全ファイル（台本、キャラクター設定、プロンプト、画像）をDriveにアップロード
"""
import os
import sys
import traceback
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from cost_tracker import CostTracker
import cost_tracker
print(f"🔍 cost_tracker のパス: {cost_tracker.__file__}")
print(f"🔍 GPT_IMAGE_PRICES: {CostTracker.GPT_IMAGE_PRICES}")


# 共通モジュールのインポート
from config import (
    BASE_DIR, LOGS_DIR, LOG_PREFIX_ERROR, LOG_SUFFIX_PHASE3,
    CREDENTIALS_FILE, TOKEN_FILE, GDRIVE_SCOPES
)
from logger_utils import DualLogger
from project_utils import read_project_info, get_output_dir

# .envファイルから環境変数を読み込む
load_dotenv()


def load_cost_data(project_name, output_dir, logger):
    """
    各Phaseのコストデータを読み込む
    
    Returns:
        dict: Discord通知用のコストサマリー、または None
    """
    try:
        import glob
        
        # CostTrackerを初期化してデータを読み込む
        tracker = CostTracker(project_name)
        
        # Phase 1.1 のコストは固定値で概算
        tracker.add_phase_1_1(api_calls=1)
        
        # prompts_data.jsonl の行数からプロンプト数を取得
        prompts_file = os.path.join(output_dir, "prompts_data.jsonl")
        prompt_count = 0
        if os.path.exists(prompts_file):
            with open(prompts_file, "r", encoding="utf-8") as f:
                prompt_count = len([line for line in f if line.strip()])
        
        # images フォルダの画像数を取得
        images_dir = os.path.join(output_dir, "images")
        image_count = 0
        if os.path.exists(images_dir):
            image_count = len(glob.glob(os.path.join(images_dir, "*.png")))
        
        # videos フォルダの動画数を取得
        videos_dir = os.path.join(output_dir, "videos")
        video_count = 0
        if os.path.exists(videos_dir):
            video_count = len(glob.glob(os.path.join(videos_dir, "*.mp4")))
        
        # Phase 1.2 の実際のトークン数を読み込む
        tokens_file = os.path.join(output_dir, "phase1_2_tokens.json")
        if os.path.exists(tokens_file):
            with open(tokens_file, "r", encoding="utf-8") as f:
                token_data = json.load(f)
            
            tracker.add_phase_1_2(
                cache_creation=token_data["cache_creation_tokens"],
                cache_read=token_data["cache_read_tokens"],
                input_tokens=token_data["input_tokens"],
                output_tokens=token_data["output_tokens"]
            )
            logger.log(f"💰 Phase 1.2 の実際のトークン数を読み込みました")
        else:
            # トークンファイルがない場合は概算（後方互換性）
            if prompt_count > 0:
                avg_cache_creation = 5500
                avg_cache_read = prompt_count * 5000
                avg_input = prompt_count * 100
                avg_output = prompt_count * 1700
                
                tracker.add_phase_1_2(
                    cache_creation=avg_cache_creation,
                    cache_read=avg_cache_read,
                    input_tokens=avg_input,
                    output_tokens=avg_output
                )
                logger.log(f"⚠️ Phase 1.2 のトークン数を概算しました（実測値なし）")
        
        # Phase 2 のコスト
        tracker.add_phase_2(
            images_generated=image_count,
            images_failed=0,
            images_high_quality=0,
            images_mini=image_count  # 全てMini
        )
        
        # Discord通知用データを取得
        cost_summary = tracker.get_summary_for_discord()
        
        logger.log(f"💰 コストデータを読み込みました:")
        logger.log(f"  - プロンプト数: {prompt_count}")
        logger.log(f"  - 画像数: {image_count}")
        logger.log(f"  - 動画数: {video_count}")
        logger.log(f"  - 推定コスト: ${cost_summary['total_usd']:.2f} (約{cost_summary['total_jpy']}円)")
        
        # 動画数をサマリーに追加
        cost_summary['videos_generated'] = video_count
        
        return cost_summary
    
    except Exception as e:
        logger.log(f"⚠️ コストデータの読み込みに失敗しました: {e}")
        logger.log("⚠️ コスト情報なしでDiscord通知を送信します。")
        return None


def notify_discord_direct(project_name, gdrive_link, cost_summary, logger):
    """
    Discordに直接通知する（GAS不要）
    
    Args:
        project_name (str): プロジェクト名
        gdrive_link (str): Google DriveのリンクURL
        cost_summary (dict): コストサマリーデータ（Noneの場合はコスト情報なし）
        logger: ロガー
    """
    logger.log("\n--- Discordへの通知を試行（直接送信） ---")
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL_DIRECT_ANIME")

    if not webhook_url:
        logger.log("⚠️ .envファイルにDISCORD_WEBHOOK_URL_DIRECT_ANIMEが設定されていないため、通知をスキップします。")
        return
    
    # デバッグ: URLの長さと最初/最後の文字を表示
    logger.log(f"🔍 Webhook URL length: {len(webhook_url)}")
    logger.log(f"🔍 URL starts with: {webhook_url[:50]}...")
    logger.log(f"🔍 URL ends with: ...{webhook_url[-20:]}")

    # 埋め込みフィールドを構築
    embed_fields = [
        {
            "name": "📂 Google Drive リンク",
            "value": f"[こちらをクリック]({gdrive_link})"
        }
    ]
    
    # コスト情報がある場合は追加
    if cost_summary:
        total_usd = cost_summary.get('total_usd', 0)
        total_jpy = cost_summary.get('total_jpy', 0)
        phase_1_usd = cost_summary.get('phase_1_usd', 0)
        phase_2_usd = cost_summary.get('phase_2_usd', 0)
        cloud_run_usd = cost_summary.get('cloud_run_usd', 0)
        cloud_run_duration = cost_summary.get('cloud_run_duration', 0)
        prompts_count = cost_summary.get('prompts_count', 0)
        images_generated = cost_summary.get('images_generated', 0)
        
        duration_text = f"{cloud_run_duration}分" if cloud_run_duration > 0 else '不明'
        
        embed_fields.append({
            "name": "💰 コスト",
            "value": f"**${total_usd:.2f}** (約{int(total_jpy)}円)\n"
                     f"・Phase 1: ${phase_1_usd:.2f}\n"
                     f"・Phase 2: ${phase_2_usd:.2f}\n"
                     f"・Cloud Run: ${cloud_run_usd:.2f} ({duration_text})",
            "inline": False
        })
        
        videos_generated = cost_summary.get('videos_generated', 0)
        
        embed_fields.append({
            "name": "📊 生成結果",
            "value": f"・プロンプト: {prompts_count}個\n"
                     f"・画像: {images_generated}枚\n"
                     f"・動画: {videos_generated}本",
            "inline": False
        })
    
    # Discordペイロード
    payload = {
        "username": "AI動画生成パイプライン",
        "embeds": [{
            "title": f"✅ プロジェクト完了: {project_name}",
            "description": "ファイルのアップロードが完了しました。",
            "color": 3447003,
            "fields": embed_fields,
            "timestamp": datetime.utcnow().isoformat()
        }]
    }
    
    logger.log("--- Discord通知デバッグ情報 ---")
    logger.log(f"送信先URL: {webhook_url}")
    logger.log(f"ペイロード: {json.dumps(payload, ensure_ascii=False, indent=2)}")
    
    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            },
            timeout=10
        )
        
        logger.log(f"応答ステータスコード: {response.status_code}")
        
        if response.status_code == 204 or response.status_code == 200:
            logger.log("✅ Discordへの通知が正常に送信されました。")
        else:
            logger.log(f"🚨 Discord通知に失敗しました。HTTPステータス: {response.status_code}")
            logger.log(f"応答内容: {response.text}")
    
    except requests.exceptions.RequestException as e:
        logger.log(f"🚨 Discord通知中にネットワークエラーが発生しました: {e}")
    except Exception as e:
        logger.log(f"🚨 Discord通知中に予期せぬエラーが発生しました: {e}")
    logger.log("--- デバッグ情報ここまで ---")


def authenticate_gdrive(logger):
    """Google Drive APIの認証を行う（gdrive_token.json 対応）"""
    creds = None
    token_path = os.path.join(BASE_DIR, "gdrive_token.json")
    credentials_path = os.path.join(BASE_DIR, CREDENTIALS_FILE)

    if not os.path.exists(credentials_path):
        logger.log(f"🚨 エラー: {CREDENTIALS_FILE} が見つかりません。")
        return None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, GDRIVE_SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                logger.log("🚨 トークンのリフレッシュに失敗しました。認証ファイルを削除して再試行します。")
                os.remove(token_path)
                creds = None
        
        if not creds:
            logger.log(f"🚨 有効な認証情報(gdrive_token.json)がありません。")
            logger.log("authenticate_gdrive.py を実行して、先に手動での認証を完了させてください。")
            return None

        with open(token_path, 'w') as token:
            token.write(creds.to_json())
            
    return creds


def upload_folder_to_drive(service, project_name, local_project_path, parent_folder_id, logger):
    """
    指定されたフォルダをGoogle Driveにアップロードし、共有リンクを返す
    JSONL形式 (prompts_data.jsonl) に対応
    
    Args:
        service: Google Drive API サービス
        project_name: プロジェクト名
        local_project_path: ローカルのプロジェクトフォルダパス
        parent_folder_id: 親フォルダID
        logger: ロガー
    
    Returns:
        str: フォルダの共有リンク、失敗時は None
    """
    if not os.path.exists(local_project_path):
        logger.log(f"🚨 エラー: ローカルのプロジェクトフォルダが見つかりません: {local_project_path}")
        return None

    logger.log(f"🔄 Drive上に '{project_name}' フォルダを作成中...")
    
    # プロジェクトフォルダを検索または作成
    try:
        query = f"name='{project_name}' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id, webViewLink)').execute()
        existing_folders = results.get('files', [])
        
        if existing_folders:
            # 既存フォルダを使用
            folder_id = existing_folders[0]['id']
            folder_link = existing_folders[0]['webViewLink']
            logger.log(f"✅ 既存フォルダを使用します (ID: {folder_id})")
        else:
            # 新規作成
            folder_metadata = {
                'name': project_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_folder_id]
            }
            folder = service.files().create(body=folder_metadata, fields='id, webViewLink').execute()
            folder_id = folder.get('id')
            folder_link = folder.get('webViewLink')
            logger.log(f"✅ フォルダを作成しました (ID: {folder_id})")
    
    except Exception as e:
        logger.log(f"🚨 Driveのフォルダ作成中にエラー: {e}")
        return None

    # テキストファイルのアップロード（.txt と .jsonl）
    text_files = [
        f for f in os.listdir(local_project_path) 
        if f.lower().endswith(('.txt', '.jsonl')) and os.path.isfile(os.path.join(local_project_path, f))
    ]
    
    for filename in text_files:
        logger.log(f"  - アップロード中: {filename}")
        local_file_path = os.path.join(local_project_path, filename)
        
        # 既存ファイルを検索
        query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        existing_files = results.get('files', [])
        
        # MIMEタイプを決定
        if filename.endswith('.jsonl'):
            mimetype = 'application/json'
        else:
            mimetype = 'text/plain'
        
        if existing_files:
            # 既存ファイルを更新
            file_id = existing_files[0]['id']
            media = MediaFileUpload(local_file_path, mimetype=mimetype)
            service.files().update(fileId=file_id, media_body=media).execute()
            logger.log(f"    ✓ 既存ファイルを更新しました: {filename}")
        else:
            # 新規作成
            file_metadata = {'name': filename, 'parents': [folder_id]}
            media = MediaFileUpload(local_file_path, mimetype=mimetype)
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            logger.log(f"    ✓ 新規アップロードしました: {filename}")

    # 画像フォルダと画像のアップロード
    local_image_path = os.path.join(local_project_path, "images")
    if os.path.exists(local_image_path):
        logger.log(f"🔄 Drive上に 'images' サブフォルダを作成中...")
        
        # images フォルダを検索または作成
        query = f"name='images' and '{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        existing_image_folders = results.get('files', [])
        
        if existing_image_folders:
            image_folder_id = existing_image_folders[0]['id']
            logger.log(f"  ✓ 既存の images フォルダを使用します")
        else:
            image_folder_metadata = {
                'name': 'images',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [folder_id]
            }
            image_folder = service.files().create(body=image_folder_metadata, fields='id').execute()
            image_folder_id = image_folder.get('id')
            logger.log(f"  ✓ images フォルダを作成しました")
        
        # 画像ファイルをアップロード
        image_files = sorted([
            f for f in os.listdir(local_image_path) 
            if f.lower().endswith('.png')
        ])
        
        for i, filename in enumerate(image_files):
            logger.log(f"  - 画像アップロード中 ({i+1}/{len(image_files)}): {filename}")
            local_file_path = os.path.join(local_image_path, filename)
            
            # 既存ファイルを検索
            query = f"name='{filename}' and '{image_folder_id}' in parents and trashed=false"
            results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
            existing_files = results.get('files', [])
            
            if existing_files:
                # 既存ファイルを更新
                file_id = existing_files[0]['id']
                media = MediaFileUpload(local_file_path, mimetype='image/png')
                service.files().update(fileId=file_id, media_body=media).execute()
            else:
                # 新規作成
                file_metadata = {'name': filename, 'parents': [image_folder_id]}
                media = MediaFileUpload(local_file_path, mimetype='image/png')
                service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    # 動画フォルダと動画のアップロード
    local_video_path = os.path.join(local_project_path, "videos")
    if os.path.exists(local_video_path):
        logger.log(f"🔄 Drive上に 'videos' サブフォルダを作成中...")
        
        # videos フォルダを検索または作成
        query = f"name='videos' and '{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        existing_video_folders = results.get('files', [])
        
        if existing_video_folders:
            video_folder_id = existing_video_folders[0]['id']
            logger.log(f"  ✓ 既存の videos フォルダを使用します")
        else:
            video_folder_metadata = {
                'name': 'videos',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [folder_id]
            }
            video_folder = service.files().create(body=video_folder_metadata, fields='id').execute()
            video_folder_id = video_folder.get('id')
            logger.log(f"  ✓ videos フォルダを作成しました")
        
        # 動画ファイルをアップロード
        video_files = sorted([
            f for f in os.listdir(local_video_path) 
            if f.lower().endswith(('.mp4', '.webm'))
        ])
        
        for i, filename in enumerate(video_files):
            logger.log(f"  - 動画アップロード中 ({i+1}/{len(video_files)}): {filename}")
            local_file_path = os.path.join(local_video_path, filename)
            
            # 既存ファイルを検索
            query = f"name='{filename}' and '{video_folder_id}' in parents and trashed=false"
            results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
            existing_files = results.get('files', [])
            
            if existing_files:
                file_id = existing_files[0]['id']
                media = MediaFileUpload(local_file_path, mimetype='video/mp4')
                service.files().update(fileId=file_id, media_body=media).execute()
            else:
                file_metadata = {'name': filename, 'parents': [video_folder_id]}
                media = MediaFileUpload(local_file_path, mimetype='video/mp4')
                service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    return folder_link


def main():
    """メインの処理フロー"""
    project_name, model_name, script_full_path = read_project_info()

    if not project_name:
        sys.exit(1)

    log_file = os.path.join(LOGS_DIR, f"{LOG_PREFIX_ERROR}{project_name}{LOG_SUFFIX_PHASE3}")
    logger = DualLogger(log_file)
    error_occurred = False

    try:
        logger.log(f"\n{'='*60}")
        logger.log(f"--- Phase 3 (GDrive Upload): '{project_name}' のアップロードを開始します ---")
        logger.log(f"{'='*60}")
        
        parent_folder_id = os.getenv("GDRIVE_PARENT_FOLDER_ID")
        if not parent_folder_id:
            logger.log("🚨 エラー: .envファイルに GDRIVE_PARENT_FOLDER_ID が設定されていません。")
            error_occurred = True
        else:
            credentials = authenticate_gdrive(logger)
            if not credentials:
                error_occurred = True
            else:
                drive_service = build('drive', 'v3', credentials=credentials)
                
                local_project_folder = get_output_dir(project_name, model_name)
                
                # 台本ファイルをプロジェクトフォルダにコピー
                if script_full_path and os.path.exists(script_full_path):
                    import shutil
                    script_filename = os.path.basename(script_full_path)
                    destination = os.path.join(local_project_folder, script_filename)
                    
                    # まだコピーされていない場合のみコピー
                    if not os.path.exists(destination):
                        shutil.copy2(script_full_path, destination)
                        logger.log(f"📄 台本ファイルをコピーしました: {script_filename}")
                    else:
                        logger.log(f"📄 台本ファイルは既に存在します: {script_filename}")
                else:
                    logger.log(f"⚠️ 台本ファイルが見つかりません: {script_full_path}")

                gdrive_folder_link = upload_folder_to_drive(
                    drive_service, project_name, local_project_folder, parent_folder_id, logger
                )

                if gdrive_folder_link:
                    logger.log(f"\n✅ アップロード完了: {gdrive_folder_link}")
                    
                    # コストデータを読み込み
                    cost_summary = load_cost_data(project_name, local_project_folder, logger)
                    
                    # コスト情報付きでDiscord通知（直接送信）
                    notify_discord_direct(project_name, gdrive_folder_link, cost_summary, logger)
                    
                    logger.log("\n--- Phase 3 (GDrive Upload) が正常に完了しました ---")
                else:
                    error_occurred = True

    except Exception as e:
        logger.log(f"\n🚨🚨🚨 Phase 3 (GDrive Upload) で予期せぬエラーが発生しました 🚨🚨🚨")
        logger.log(traceback.format_exc())
        error_occurred = True

    if error_occurred:
        logger.save_on_error()
        sys.exit(1)


if __name__ == '__main__':
    main()