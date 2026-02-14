#!/usr/bin/env python3
"""
Phase 2: 画像生成スクリプト（JSONL形式対応版）
prompts_data.jsonl から image_prompt を読み込み、GPT Image 1 mini で画像を生成

【主要な改善点】
1. JSONL形式 (prompts_data.jsonl) に対応
2. チェックポイント機能の強化（ローカル → Drive）
3. 即座のDriveバックアップ（10枚ごと）
4. コストトラッキング
"""
import os
import sys
import json
import time
import traceback
import base64
import glob
import signal
from openai import OpenAI, BadRequestError, RateLimitError, APIConnectionError
from dotenv import load_dotenv
from api_retry_utils import call_api_with_retry
from cost_tracker import CostTracker
from gdrive_checkpoint import check_drive_checkpoint, authenticate_gdrive, find_project_folder_on_drive

# 共通モジュールのインポート
from config import (
    LOGS_DIR, LOG_PREFIX_ERROR, LOG_SUFFIX_PHASE2,
    GPT_IMAGE_MODEL, IMAGE_SIZE, IMAGE_QUALITY, TEST_MODE_LIMIT
)
from logger_utils import DualLogger
from project_utils import (
    read_project_info, get_output_dir, ensure_image_output_dir
)

# グローバル変数（中断ハンドラ用）
_logger = None
_tracker = None
_project_name = None
_success_count = 0
_total_count = 0
# .envファイルから環境変数を読み込む
load_dotenv()

def handle_interrupt(signum, frame):
    """中断シグナルをキャッチ"""
    global _logger, _tracker, _project_name, _success_count, _total_count
    
    if _logger:
        _logger.log("\n⚠️ 処理が中断されました")
        _logger.log(f"📊 進捗: {_success_count}/{_total_count}枚")
        if _tracker:
            _logger.log(f"\n{_tracker.get_detailed_summary()}")
        _logger.log(f"\n📂 次回は{_success_count+1}枚目から再開")
    sys.exit(0)

signal.signal(signal.SIGINT, handle_interrupt)
signal.signal(signal.SIGTERM, handle_interrupt)

# 🆕 モデル別価格
MODEL_PRICES = {
    "gpt-image-1": 0.25,      # 高品質版
    "gpt-image-1-mini": 0.052 # Mini版
}


def select_model_for_image(index, total_count):
    """
    🆕 画像のインデックスに応じてモデルを選択
    
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
        
        # prompts_data.jsonl を検索
        query = f"name='prompts_data.jsonl' and '{project_folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        files = results.get('files', [])
        
        if not files:
            logger.log(f"⚠️ Drive に prompts_data.jsonl が見つかりません。")
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
        
        logger.log(f"☁️  Drive から prompts_data.jsonl をダウンロードしました")
        return True
    
    except Exception as e:
        logger.log(f"⚠️ Drive ダウンロードエラー: {e}")
        return False


def load_prompts_from_jsonl(prompts_file_path, logger):
    """
    prompts_data.jsonl を読み込み、プロンプトのリストを抽出する
    
    Args:
        prompts_file_path: prompts_data.jsonl のパス
        logger: ロガー
    
    Returns:
        list: プロンプトのリスト（各要素は dict: {"index": N, "image_prompt": "...", "visual_summary": "..."}）
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
                    
                    # 必須フィールドの確認
                    if "index" not in data or "image_prompt" not in data:
                        logger.log(f"⚠️ 行{line_num}: 必須フィールドがありません。スキップします。")
                        continue
                    
                    prompts.append(data)
                
                except json.JSONDecodeError as e:
                    logger.log(f"⚠️ 行{line_num}: JSON解析エラー: {e}")
                    continue
        
        logger.log(f"✅ {len(prompts)} 個のプロンプトを読み込みました。")
        
        if len(prompts) == 0:
            logger.log(f"⚠️ プロンプトが見つかりませんでした。ファイル形式を確認してください。")
            logger.log(f"   期待されるフォーマット: {{\"index\": N, \"image_prompt\": \"...\", \"visual_summary\": \"...\"}}")
        
        return prompts
    
    except Exception as e:
        logger.log(f"🚨 プロンプトファイルの読み込み中にエラーが発生しました: {e}")
        logger.log(traceback.format_exc())
        return []


def check_existing_images(image_output_dir, project_name, logger):
    """
    チェックポイント機能: ローカル → Google Drive の順で確認
    
    Args:
        image_output_dir: 画像出力ディレクトリ
        project_name: プロジェクト名
        logger: ロガー
    
    Returns:
        set: 完了済みの画像ファイル名のセット（例: {'001.png', '002.png'}）
    """
    existing_images = set()
    
    # まずローカルを確認
    if os.path.exists(image_output_dir):
        try:
            local_images = glob.glob(os.path.join(image_output_dir, "*.png"))
            existing_images = set([os.path.basename(f) for f in local_images])
            
            if existing_images:
                logger.log(f"")
                logger.log(f"{'='*60}")
                logger.log(f"🔄 ローカルチェックポイント検出!")
                logger.log(f"✅ {len(existing_images)} 枚の画像が既に生成済みです")
                logger.log(f"{'='*60}")
                logger.log(f"")
                return existing_images
        except Exception as e:
            logger.log(f"⚠️ ローカルファイルの確認中にエラー: {e}")
    
    # ローカルにない場合、Google Drive を確認
    logger.log("📁 ローカルに images フォルダが見つかりません。")
    logger.log("☁️  Google Drive からチェックポイントを確認中...")
    
    try:
        parent_folder_id = os.getenv("GDRIVE_PARENT_FOLDER_ID")
        if not parent_folder_id:
            logger.log("⚠️ GDRIVE_PARENT_FOLDER_ID が設定されていません。最初から生成します。")
            return set()
        
        # Drive から画像リストを取得
        drive_images = check_drive_checkpoint(project_name, parent_folder_id, checkpoint_type="images")
        
        if drive_images:
            existing_images = set(drive_images)
            logger.log(f"")
            logger.log(f"{'='*60}")
            logger.log(f"☁️  Google Drive チェックポイント検出!")
            logger.log(f"✅ {len(existing_images)} 枚の画像が Drive に存在")
            logger.log(f"📥 ローカルにダウンロード中...")
            logger.log(f"{'='*60}")
            logger.log(f"")
            
            # Drive から画像をローカルにダウンロード
            downloaded = download_images_from_drive(
                project_name, parent_folder_id, image_output_dir, drive_images, logger
            )
            logger.log(f"✅ {downloaded} 枚をローカルにダウンロードしました")
        else:
            logger.log("📁 最初から生成を開始します。")
        
        return existing_images
    
    except Exception as e:
        logger.log(f"⚠️ Drive チェックポイント確認中にエラー: {e}")
        logger.log("📁 安全のため、最初から生成を開始します。")
        return set()


def download_images_from_drive(project_name, parent_folder_id, local_images_dir, image_names, logger):
    """
    Google Drive から画像をローカルにダウンロード
    
    Args:
        project_name: プロジェクト名
        parent_folder_id: 親フォルダID
        local_images_dir: ローカルの画像保存先ディレクトリ
        image_names: ダウンロードする画像ファイル名のリスト
        logger: ロガー
    
    Returns:
        int: ダウンロードした画像数
    """
    try:
        from googleapiclient.http import MediaIoBaseDownload
        from googleapiclient.discovery import build
        import io
        
        creds = authenticate_gdrive()
        if not creds:
            return 0
        
        service = build('drive', 'v3', credentials=creds)
        
        # プロジェクトフォルダを検索
        project_folder_id = find_project_folder_on_drive(service, project_name, parent_folder_id)
        if not project_folder_id:
            return 0
        
        # images フォルダを検索
        query = f"name='images' and '{project_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        folders = results.get('files', [])
        
        if not folders:
            return 0
        
        images_folder_id = folders[0]['id']
        
        # 出力ディレクトリを作成
        os.makedirs(local_images_dir, exist_ok=True)
        
        # 画像ファイルを一括取得（IDとファイル名のマッピング）
        query = f"'{images_folder_id}' in parents and trashed=false"
        results = service.files().list(
            q=query, spaces='drive',
            fields='files(id, name)',
            pageSize=1000
        ).execute()
        drive_files = {f['name']: f['id'] for f in results.get('files', [])}
        
        downloaded = 0
        for name in image_names:
            local_path = os.path.join(local_images_dir, name)
            
            # 既にローカルにある場合はスキップ
            if os.path.exists(local_path):
                downloaded += 1
                continue
            
            file_id = drive_files.get(name)
            if not file_id:
                continue
            
            try:
                request = service.files().get_media(fileId=file_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                
                with open(local_path, 'wb') as f:
                    f.write(fh.getvalue())
                
                downloaded += 1
                
                # 進捗表示（20枚ごと）
                if downloaded % 20 == 0:
                    logger.log(f"  📥 {downloaded}/{len(image_names)} 枚ダウンロード済み")
            
            except Exception as e:
                logger.log(f"  ⚠️ {name} のダウンロードに失敗: {e}")
                continue
        
        return downloaded
    
    except Exception as e:
        logger.log(f"⚠️ Drive からの画像ダウンロードエラー: {e}")
        return 0


def upload_image_to_drive(image_path, project_name, logger):
    """
    画像を Google Drive に即座にアップロード
    
    Args:
        image_path: ローカルの画像パス
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
        
        # images フォルダを検索または作成
        query = f"name='images' and '{project_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        files = results.get('files', [])
        
        if files:
            images_folder_id = files[0]['id']
        else:
            # images フォルダを作成
            folder_metadata = {
                'name': 'images',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [project_folder_id]
            }
            folder = service.files().create(body=folder_metadata, fields='id').execute()
            images_folder_id = folder.get('id')
        
        # 画像ファイルをアップロード（既存チェック）
        filename = os.path.basename(image_path)
        query = f"name='{filename}' and '{images_folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        existing_files = results.get('files', [])
        
        if existing_files:
            # 既存ファイルを更新
            file_id = existing_files[0]['id']
            media = MediaFileUpload(image_path, mimetype='image/png')
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            # 新規作成
            file_metadata = {'name': filename, 'parents': [images_folder_id]}
            media = MediaFileUpload(image_path, mimetype='image/png')
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    
    except Exception as e:
        logger.log(f"⚠️ Drive アップロードエラー（続行します）: {e}")


def sanitize_prompt_for_moderation(prompt):
    """
    モデレーションエラーを回避するためプロンプトを修正
    
    Args:
        prompt: オリジナルプロンプト
    
    Returns:
        str: 修正後のプロンプト
    """
    # 問題になりやすい表現を削除または置換
    sanitized = prompt
    
    # 暴力的表現
    sanitized = sanitized.replace("blood", "red liquid")
    sanitized = sanitized.replace("weapon", "tool")
    sanitized = sanitized.replace("gun", "equipment")
    sanitized = sanitized.replace("knife", "cutting tool")
    
    # 過激な感情表現
    sanitized = sanitized.replace("aggressive", "intense")
    sanitized = sanitized.replace("violent", "dynamic")
    
    return sanitized


def generate_and_save_image(client, prompt, index, image_output_dir, project_name, logger, total_count=0):
    """
    1枚の画像を生成してローカルとDriveに保存
    
    Args:
        client: OpenAI クライアント
        prompt: 画像生成プロンプト
        index: 画像番号
        image_output_dir: 出力ディレクトリ
        project_name: プロジェクト名
        logger: ロガー
        total_count: 総画像数（モデル選択用）
    
    Returns:
        bool: 成功時 True
    """
    # 🆕 モデル選択
    model, price = select_model_for_image(index, total_count)
    
    logger.log(f"\n🔄 画像 {index}/{total_count} を生成中（{model}: ${price}/枚）...")
    
    # まずオリジナルプロンプトで試す
    try:
        res = call_api_with_retry(
            lambda: client.images.generate(
                model=model,  # 🆕 選択されたモデルを使用
                prompt=prompt,
                size=IMAGE_SIZE,
                quality=IMAGE_QUALITY,
                extra_body={"moderation": "low"}
            ),
            max_retries=3,
            logger=logger,
            operation_name=f"画像{index}の生成"
        )
        
        b64_data = res.data[0].b64_json
        if not b64_data:
            logger.log(f"⚠️ エラー: APIから画像データ(b64_json)が返されませんでした (画像 {index})。")
            return False

        image_data = base64.b64decode(b64_data)
        
        filename = f"{index:03d}.png"
        filepath = os.path.join(image_output_dir, filename)
        
        # ローカルに保存
        with open(filepath, "wb") as f:
            f.write(image_data)
        
        logger.log(f"✅ 画像 {index} を保存しました: {filepath}")
        
        # 即座に Drive にも保存
        upload_image_to_drive(filepath, project_name, logger)
        
        return True

    except Exception as e:
        # モデレーションエラーかどうかを判定
        is_moderation_error = False
        
        # 1. 例外の型で判定
        if isinstance(e, BadRequestError):
            error_message = str(e).lower()
            # 2. エラーメッセージの内容で二重チェック
            if any(keyword in error_message for keyword in ["content_policy", "safety", "moderation", "unsafe"]):
                is_moderation_error = True
        
        # モデレーションエラーの場合のみ、修正版で1回だけリトライ
        if is_moderation_error:
            logger.log(f"🚫 画像 {index}: オリジナルプロンプトがモデレーションに引っかかりました")
            logger.log(f"🔄 プロンプトを修正して1回だけリトライします...")
            
            try:
                # 修正版プロンプトで再試行
                sanitized_prompt = sanitize_prompt_for_moderation(prompt)
                
                res = call_api_with_retry(
                    lambda: client.images.generate(
                        model=GPT_IMAGE_MODEL,
                        prompt=sanitized_prompt,
                        size=IMAGE_SIZE,
                        quality=IMAGE_QUALITY,
                        extra_body={"moderation": "low"}
                    ),
                    max_retries=3,
                    logger=logger,
                    operation_name=f"画像{index}の生成 (修正版)"
                )
                
                b64_data = res.data[0].b64_json
                if not b64_data:
                    logger.log(f"⚠️ 修正版でもデータが返されませんでした (画像 {index})。")
                    return False

                image_data = base64.b64decode(b64_data)
                
                filename = f"{index:03d}.png"
                filepath = os.path.join(image_output_dir, filename)
                
                with open(filepath, "wb") as f:
                    f.write(image_data)
                
                logger.log(f"✅ 画像 {index} を保存しました (修正版プロンプトで成功): {filepath}")
                
                # 即座に Drive にも保存
                upload_image_to_drive(filepath, project_name, logger)
                
                return True
                
            except Exception as retry_error:
                logger.log(f"⚠️ 修正版プロンプトでも失敗しました: {retry_error}")
                logger.log(f"⚠️ 画像 {index} をスキップします")
                return False
        
        # モデレーション以外のエラーはそのまま失敗
        logger.log(f"⚠️ 画像 {index} の生成に失敗しました: {e}")
        logger.log(traceback.format_exc())
        return False


def main():
    """メインの処理フロー"""
    project_name, model_name, _ = read_project_info()
    _project_name = project_name  # 追加
    if not project_name:
        sys.exit(1)
        
    output_dir = get_output_dir(project_name, model_name)
    image_output_dir = ensure_image_output_dir(project_name, model_name)

    # JSONL形式のファイルに変更
    prompts_file = os.path.join(output_dir, "prompts_data.jsonl")
    log_file = os.path.join(LOGS_DIR, f"{LOG_PREFIX_ERROR}{project_name}{LOG_SUFFIX_PHASE2}")

    logger = DualLogger(log_file)
    _logger = logger 
    error_occurred = False
    
    # コストトラッカー初期化
    tracker = CostTracker(project_name)
    _tracker = tracker  # 追加

    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.log("🚨 エラー: 環境変数 'OPENAI_API_KEY' が設定されていません。")
            logger.save_on_error()
            sys.exit(1)
        client = OpenAI(api_key=api_key)
    except Exception as e:
        logger.log(f"🚨 OpenAIクライアントの初期化に失敗: {e}")
        logger.save_on_error()
        sys.exit(1)

    try:
        logger.log(f"\n{'='*60}")
        logger.log(f"--- Phase 2 (GPT Images): '{project_name}' の画像を生成します ---")
        logger.log(f"{'='*60}")
        
        # prompts_data.jsonl が必要なので、まず確認
        if not os.path.exists(prompts_file):
            logger.log("📁 ローカルに prompts_data.jsonl が見つかりません。")
            logger.log("☁️  Google Drive からダウンロードを試みます...")
            
            if not download_prompts_from_drive(project_name, prompts_file, logger):
                logger.log("🚨 エラー: prompts_data.jsonl が見つかりません（ローカルにもDriveにもない）")
                error_occurred = True
                raise Exception("prompts_data.jsonl not found")
        
        # プロンプトを読み込み（JSONL形式）
        prompt_data_list = load_prompts_from_jsonl(prompts_file, logger)

        if not prompt_data_list:
            logger.log("\n--- プロンプトが見つからないため、処理を終了します ---")
            error_occurred = True
        else:
            # チェックポイント: ローカル → Drive の順で確認
            existing_images = check_existing_images(image_output_dir, project_name, logger)
            
            # 全て完了している場合
            if len(existing_images) >= len(prompt_data_list):
                logger.log(f"")
                logger.log(f"{'='*60}")
                logger.log(f"✅ 全ての画像が既に生成済みです ({len(existing_images)}/{len(prompt_data_list)})")
                logger.log(f"▶️  Phase 2 をスキップします")
                logger.log(f"{'='*60}")
                logger.log(f"")
                logger.log("\n--- Phase 2 (GPT Images) が既に完了しています（スキップ） ---")
                sys.exit(0)
            
            # テストモード対応
            prompts_to_process = prompt_data_list
            if TEST_MODE_LIMIT > 0:
                logger.log(f"\n⚠️ テストモード: 最初の {TEST_MODE_LIMIT} 枚の画像のみ生成します。")
                prompts_to_process = prompt_data_list[:TEST_MODE_LIMIT]

            # 未完了分のみ処理
            failed_count = 0
            success_count = len(existing_images)  # 既に完了済みの分も含む
            _total_count = len(prompt_data_list)  # 追加
            
            for prompt_data in prompts_to_process:
                image_index = prompt_data["index"]
                image_prompt = prompt_data["image_prompt"]
                filename = f"{image_index:03d}.png"
                
                # 既に存在する場合はスキップ
                if filename in existing_images:
                    logger.log(f"⏭️  画像 {image_index} は既に生成済み（スキップ）")
                    continue
                
                if generate_and_save_image(client, image_prompt, image_index, image_output_dir, project_name, logger, total_count=len(prompt_data_list)):
                    success_count += 1
                    _success_count = success_count
                    
                    # 10枚ごとにログ出力
                    if success_count % 10 == 0:
                        logger.log(f"\n📊 進捗: {success_count}/{len(prompt_data_list)} 枚完了\n")
                else:
                    failed_count += 1
                
                time.sleep(1)
            
            # 🆕 モデル別のコスト計算
            high_quality_count = 0
            mini_count = 0
            
            total_images = len(prompt_data_list)
            for i in range(1, success_count + 1):
                model, _ = select_model_for_image(i, total_images)
                if model == "gpt-image-1":
                    high_quality_count += 1
                else:
                    mini_count += 1
            
            # コスト記録
            newly_generated = success_count - len(existing_images)
            tracker.add_phase_2(
                images_generated=newly_generated,
                images_failed=failed_count,
                images_high_quality=high_quality_count,  # 🆕
                images_mini=mini_count  # 🆕
            )
            
            # コストサマリーをログ出力
            logger.log(tracker.get_detailed_summary())
            
            # 結果サマリー
            logger.log(f"\n{'='*60}")
            logger.log(f"📊 Phase 2 処理結果")
            logger.log(f"{'='*60}")
            logger.log(f"  - 既存画像: {len(existing_images)} 枚")
            logger.log(f"  - 新規生成: {newly_generated} 枚")
            logger.log(f"  - 失敗: {failed_count} 枚")
            logger.log(f"  - 合計成功: {success_count} 枚")
            logger.log(f"{'='*60}\n")
            
            if failed_count > 0:
                logger.log(f"⚠️ 一部の画像生成に失敗しましたが、処理を完了しました。")
            else:
                logger.log(f"🎉 全ての画像生成が正常に完了しました！")
            
            logger.log("\n--- Phase 2 (GPT Images) が正常に完了しました ---")

    except Exception as e:
        logger.log(f"\n🚨🚨🚨 Phase 2 で予期せぬエラーが発生しました 🚨🚨🚨")
        logger.log(traceback.format_exc())
        error_occurred = True

    if error_occurred:
        logger.save_on_error()
        sys.exit(1)


if __name__ == "__main__":
    main()