#!/usr/bin/env python3
"""
Phase 1.1: キャラクター設定生成スクリプト
台本とキャラクタールールから character_settings.txt を生成
チェックポイント機能: 既に生成済みの場合はスキップ
"""
import os
import sys
import shutil
import traceback
import anthropic
from dotenv import load_dotenv

# 共通モジュールのインポート
from config import (
    BASE_DIR, LOGS_DIR, LOG_PREFIX_ERROR, LOG_SUFFIX_PHASE1_1,
    CLAUDE_MODEL, CLAUDE_MAX_TOKENS
)
from logger_utils import DualLogger
from project_utils import (
    read_project_info, get_output_dir, ensure_output_dir,
    read_file_safely, write_file_safely
)
from api_retry_utils import call_api_with_retry
from cost_tracker import CostTracker  # 🆕 コストトラッカー追加
from gdrive_checkpoint import authenticate_gdrive, find_project_folder_on_drive  # 🆕 Drive チェックポイント

# .envファイルから環境変数を読み込む
load_dotenv()


def download_character_settings_from_drive(project_name, output_file_path, logger):
    """
    🆕 Google Drive から character_settings.txt をダウンロード
    
    Args:
        project_name: プロジェクト名
        output_file_path: ローカル保存先のパス
        logger: ロガー
    
    Returns:
        bool: 成功時 True
    """
    try:
        from googleapiclient.http import MediaIoBaseDownload
        from googleapiclient.discovery import build
        import io
        
        parent_folder_id = os.getenv("GDRIVE_PARENT_FOLDER_ID")
        if not parent_folder_id:
            return False
        
        # 認証
        creds = authenticate_gdrive()
        if not creds:
            return False
        
        service = build('drive', 'v3', credentials=creds)
        
        # プロジェクトフォルダを検索
        project_folder_id = find_project_folder_on_drive(service, project_name, parent_folder_id)
        
        if not project_folder_id:
            return False
        
        # character_settings.txt を検索
        query = f"name='character_settings.txt' and '{project_folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        files = results.get('files', [])
        
        if not files:
            return False
        
        # ファイルをダウンロード
        file_id = files[0]['id']
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        # ローカルに保存
        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
        with open(output_file_path, 'wb') as f:
            f.write(fh.getvalue())
        
        logger.log(f"☁️  Drive から character_settings.txt をダウンロードしました")
        return True
    
    except Exception as e:
        logger.log(f"⚠️ Drive ダウンロードエラー: {e}")
        return False


def check_drive_for_character_settings(project_name, logger):
    """
    🆕 Google Drive で character_settings.txt の存在を確認
    
    Returns:
        bool: 存在する場合 True
    """
    try:
        parent_folder_id = os.getenv("GDRIVE_PARENT_FOLDER_ID")
        if not parent_folder_id:
            return False
        
        # 認証
        creds = authenticate_gdrive()
        if not creds:
            return False
        
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        
        # プロジェクトフォルダを検索
        project_folder_id = find_project_folder_on_drive(service, project_name, parent_folder_id)
        
        if not project_folder_id:
            logger.log("📁 Drive にプロジェクトフォルダが見つかりません。")
            return False
        
        # character_settings.txt を検索
        query = f"name='character_settings.txt' and '{project_folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        files = results.get('files', [])
        
        if files:
            logger.log("✅ Drive で character_settings.txt を発見しました。")
            return True
        
        return False
    
    except Exception as e:
        logger.log(f"⚠️ Drive 確認中にエラー: {e}")
        return False


def upload_character_settings_to_drive(settings_file_path, project_name, logger):
    """
    🆕 character_settings.txt を Google Drive にアップロード
    
    Args:
        settings_file_path: ローカルの character_settings.txt のパス
        project_name: プロジェクト名
        logger: ロガー
    """
    try:
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.discovery import build
        
        parent_folder_id = os.getenv("GDRIVE_PARENT_FOLDER_ID")
        if not parent_folder_id:
            return
        
        # 認証
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
        
        # character_settings.txt をアップロード
        file_metadata = {'name': 'character_settings.txt', 'parents': [project_folder_id]}
        media = MediaFileUpload(settings_file_path, mimetype='text/plain')
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        
        logger.log(f"☁️  character_settings.txt を Drive に保存しました")
    
    except Exception as e:
        logger.log(f"⚠️ Drive 保存エラー（続行します）: {e}")


def copy_script_to_output(script_path, output_dir, logger):
    """
    元の台本ファイルをアウトプットフォルダにコピーする
    既に存在する場合はスキップ（チェックポイント機能）
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        destination_path = os.path.join(output_dir, os.path.basename(script_path))
        
        # チェックポイント: 既に存在する場合はスキップ
        if os.path.exists(destination_path):
            logger.log(f"✅ 台本ファイルが既に存在します (スキップ): {destination_path}")
            return True
        
        # 存在しない場合のみコピー
        shutil.copy(script_path, destination_path)
        logger.log(f"✅ 元の台本を保存しました: {destination_path}")
        return True
    
    except Exception as e:
        logger.log(f"🚨 台本ファイルのコピー中にエラー: {e}")
        return False


def load_input_files(script_full_path, logger):
    """台本とキャラクタールールを読み込む"""
    character_rules_file = os.path.join(BASE_DIR, "rule", "character_rules.txt")

    script_content = read_file_safely(script_full_path, "台本")
    rules_content = read_file_safely(character_rules_file, "キャラクタールール")

    if not all([script_content, rules_content]):
        logger.log("🚨 エラー: 必要なファイルの読み込みに失敗しました。")
        return None, None

    return script_content, rules_content


def generate_character_settings(client, script, character_rules, logger):
    """AIを呼び出してキャラクター設定を生成する"""
    logger.log("🔄 AIを呼び出してキャラクター設定を生成中...")
    
    try:
        response = call_api_with_retry(
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                system="You are a professional scriptwriter. Analyze the provided script and base rules to create concise character settings.",
                messages=[{
                    "role": "user", 
                    "content": f"<script>\n{script}\n</script>\n\n<base_rules>\n{character_rules}\n</base_rules>"
                }]
            ),
            max_retries=3,
            logger=logger,
            operation_name="キャラクター設定の生成"
        )
        
        if not response or not response.content:
            logger.log("🚨 エラー: AIからの応答が空です (キャラクター設定)。")
            return None
        
        logger.log("✅ キャラクター設定を生成しました。")
        return response.content[0].text
    
    except Exception as e:
        logger.log(f"🚨 AI呼び出しエラー (キャラクター設定): {e}")
        raise


def main():
    """メインの処理フロー"""
    project_name, model_name, script_full_path = read_project_info()
    
    if not project_name:
        sys.exit(1)
    
    output_dir = ensure_output_dir(project_name, model_name)
    output_settings_file = os.path.join(output_dir, "character_settings.txt")
    log_file = os.path.join(LOGS_DIR, f"{LOG_PREFIX_ERROR}{project_name}{LOG_SUFFIX_PHASE1_1}")

    logger = DualLogger(log_file)
    error_occurred = False
    
    # 🆕 コストトラッカー初期化
    tracker = CostTracker(project_name)

    # 🎯 チェックポイント: ローカル → Drive の順で確認
    local_exists = os.path.exists(output_settings_file)
    drive_exists = False
    
    if local_exists:
        logger.log(f"")
        logger.log(f"{'='*60}")
        logger.log(f"🔄 ローカルチェックポイント検出!")
        logger.log(f"✅ character_settings.txt が既に存在します")
        logger.log(f"▶️  Phase 1.1 をスキップします")
        logger.log(f"{'='*60}")
        logger.log(f"")
        logger.log("\n--- Phase 1.1 (Claude Settings) が既に完了しています（スキップ） ---")
        sys.exit(0)
    
    # 🆕 ローカルにない場合、Drive を確認
    logger.log("📁 ローカルに character_settings.txt が見つかりません。")
    logger.log("☁️  Google Drive からチェックポイントを確認中...")
    
    drive_exists = check_drive_for_character_settings(project_name, logger)
    
    if drive_exists:
        logger.log(f"")
        logger.log(f"{'='*60}")
        logger.log(f"☁️  Google Drive チェックポイント検出!")
        logger.log(f"✅ character_settings.txt が既に生成済みです")
        logger.log(f"☁️  Drive からダウンロード中...")
        logger.log(f"{'='*60}")
        logger.log(f"")
        
        # 🆕 Drive からダウンロード
        if download_character_settings_from_drive(project_name, output_settings_file, logger):
            logger.log("\n--- Phase 1.1 (Claude Settings) - Drive からダウンロードして完了 ---")
            sys.exit(0)
        else:
            logger.log("⚠️ Drive からのダウンロードに失敗しました。再生成します。")
            # ダウンロード失敗 → 生成処理に進む

    # Anthropic API クライアントの初期化
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.log("🚨 エラー: 環境変数 'ANTHROPIC_API_KEY' が .env ファイルに設定されていません。")
            logger.save_on_error()
            sys.exit(1)
        
        client = anthropic.Anthropic(api_key=api_key)
    
    except Exception as e:
        logger.log(f"🚨 Anthropicクライアントの初期化に失敗: {e}")
        logger.save_on_error()
        sys.exit(1)

    # メイン処理
    try:
        logger.log(f"--- Phase 1.1 (Claude Settings): '{project_name}' のキャラクター設定を生成します ---")
        
        # 入力ファイルの読み込み
        script_content, rules_content = load_input_files(script_full_path, logger)

        if script_content and rules_content:
            # 台本をアウトプットフォルダにコピー（既存チェック付き）
            if not copy_script_to_output(script_full_path, output_dir, logger):
                error_occurred = True
            else:
                # キャラクター設定を生成
                character_settings = generate_character_settings(
                    client, script_content, rules_content, logger
                )
                
                if character_settings:
                    # 設定をファイルに保存
                    if write_file_safely(output_settings_file, character_settings, "キャラクター設定"):
                        logger.log(f"✅ 設定をファイルに保存しました: {output_settings_file}")
                        
                        # 🆕 即座に Drive にも保存
                        upload_character_settings_to_drive(output_settings_file, project_name, logger)
                        
                        # 🆕 コスト記録
                        tracker.add_phase_1_1(api_calls=1)
                        
                        # 🆕 コストサマリーをログ出力
                        logger.log(tracker.get_detailed_summary())
                        
                        logger.log("\n--- Phase 1.1 (Claude Settings) が正常に完了しました ---")
                    else:
                        logger.log("🚨 キャラクター設定の保存に失敗しました。")
                        error_occurred = True
                else:
                    logger.log("🚨 キャラクター設定が生成されませんでした。処理を中断します。")
                    error_occurred = True
        else:
            logger.log("🚨 必須ファイルの読み込みに失敗しました。処理を中断します。")
            error_occurred = True

    except Exception as e:
        logger.log(f"\n🚨🚨🚨 Phase 1.1 (Claude Settings) で予期せぬエラーが発生しました 🚨🚨🚨")
        logger.log(traceback.format_exc())
        error_occurred = True

    # エラー時はログ保存
    if error_occurred:
        logger.save_on_error()
        sys.exit(1)


if __name__ == "__main__":
    main()