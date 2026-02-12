"""
Google Drive チェックポイント機能（JSONL形式対応版）
Cloud Run での冪等性を実現するため、Drive から既存ファイルを一括取得
"""
import os
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from config import BASE_DIR, CREDENTIALS_FILE, TOKEN_FILE, GDRIVE_SCOPES


def authenticate_gdrive():
    """
    Google Drive API の認証を行う（gdrive_token.json 対応）
    
    Returns:
        Credentials: 認証情報、失敗時は None
    """
    creds = None
    token_path = os.path.join(BASE_DIR, "gdrive_token.json")  # ← 変更
    credentials_path = os.path.join(BASE_DIR, CREDENTIALS_FILE)

    if not os.path.exists(credentials_path):
        print(f"🚨 エラー: {CREDENTIALS_FILE} が見つかりません。")
        return None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, GDRIVE_SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
            except Exception:
                print("🚨 トークンのリフレッシュに失敗しました。")
                return None
        else:
            print(f"🚨 有効な認証情報(gdrive_token.json)がありません。")
            return None

        with open(token_path, 'w') as token:
            token.write(creds.to_json())
            
    return creds


def find_project_folder_on_drive(service, project_name, parent_folder_id):
    """
    Google Drive 上でプロジェクトフォルダを検索
    
    Args:
        service: Google Drive API サービス
        project_name: プロジェクト名
        parent_folder_id: 親フォルダID
    
    Returns:
        str: フォルダID、見つからない場合は None
    """
    try:
        query = f"name='{project_name}' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        files = results.get('files', [])
        if files:
            return files[0]['id']
        return None
    
    except Exception as e:
        print(f"⚠️ プロジェクトフォルダの検索中にエラー: {e}")
        return None


def get_existing_prompts_count(service, project_folder_id):
    """
    Google Drive 上の prompts_data.jsonl から既存プロンプト数を取得
    JSONL形式の行数をカウントして正確な数を返す
    
    Args:
        service: Google Drive API サービス
        project_folder_id: プロジェクトフォルダID
    
    Returns:
        int: 既存プロンプト数（ファイルが存在しない場合は 0）
    """
    try:
        # prompts_data.jsonl を検索
        query = f"name='prompts_data.jsonl' and '{project_folder_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id)'
        ).execute()
        
        files = results.get('files', [])
        if not files:
            print("📁 Drive に prompts_data.jsonl が見つかりません。最初から生成します。")
            return 0
        
        # ファイル内容をダウンロード
        file_id = files[0]['id']
        from googleapiclient.http import MediaIoBaseDownload
        import io
        
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        # 内容を解析（JSONL形式 = 行数をカウント）
        content = fh.getvalue().decode('utf-8')
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        
        # 有効なJSON行のみカウント
        count = 0
        for line in lines:
            try:
                data = json.loads(line)
                if 'index' in data and 'image_prompt' in data:
                    count += 1
            except json.JSONDecodeError:
                continue
        
        print(f"✅ Drive から {count} 個の既存プロンプトを検出しました。")
        return count
    
    except Exception as e:
        print(f"⚠️ prompts_data.jsonl の取得中にエラー: {e}")
        print("📁 安全のため、最初から生成します。")
        return 0


def get_existing_images_list(service, project_folder_id):
    """
    Google Drive 上の images フォルダから既存画像リストを取得
    
    Args:
        service: Google Drive API サービス
        project_folder_id: プロジェクトフォルダID
    
    Returns:
        list: 既存画像ファイル名のリスト（例: ['001.png', '002.png']）
    """
    try:
        # images フォルダを検索
        query = f"name='images' and '{project_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id)'
        ).execute()
        
        files = results.get('files', [])
        if not files:
            print("📁 Drive に images フォルダが見つかりません。最初から生成します。")
            return []
        
        images_folder_id = files[0]['id']
        
        # images フォルダ内の .png ファイルを全て取得
        query = f"'{images_folder_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(name)',
            pageSize=1000  # 最大1000ファイル
        ).execute()
        
        image_files = results.get('files', [])
        image_names = [f['name'] for f in image_files if f['name'].endswith('.png')]
        
        print(f"✅ Drive から {len(image_names)} 枚の既存画像を検出しました。")
        return sorted(image_names)
    
    except Exception as e:
        print(f"⚠️ 画像リストの取得中にエラー: {e}")
        print("📁 安全のため、最初から生成します。")
        return []


def check_drive_checkpoint(project_name, parent_folder_id, checkpoint_type="prompts"):
    """
    Google Drive からチェックポイント情報を取得（一括）
    
    Args:
        project_name: プロジェクト名
        parent_folder_id: 親フォルダID
        checkpoint_type: "prompts" または "images"
    
    Returns:
        int または list: 
            - prompts の場合: 既存プロンプト数（int）
            - images の場合: 既存画像ファイル名リスト（list）
    """
    try:
        # 認証
        creds = authenticate_gdrive()
        if not creds:
            print("⚠️ Drive 認証に失敗しました。チェックポイントなしで実行します。")
            return 0 if checkpoint_type == "prompts" else []
        
        service = build('drive', 'v3', credentials=creds)
        
        # プロジェクトフォルダを検索
        project_folder_id = find_project_folder_on_drive(service, project_name, parent_folder_id)
        
        if not project_folder_id:
            print(f"📁 Drive にプロジェクト '{project_name}' が見つかりません。最初から生成します。")
            return 0 if checkpoint_type == "prompts" else []
        
        print(f"✅ Drive でプロジェクトフォルダを発見: {project_name}")
        
        # チェックポイント取得
        if checkpoint_type == "prompts":
            return get_existing_prompts_count(service, project_folder_id)
        elif checkpoint_type == "images":
            return get_existing_images_list(service, project_folder_id)
        else:
            raise ValueError(f"不正な checkpoint_type: {checkpoint_type}")
    
    except Exception as e:
        print(f"⚠️ Drive チェックポイント取得中にエラー: {e}")
        print("📁 安全のため、最初から生成します。")
        return 0 if checkpoint_type == "prompts" else []