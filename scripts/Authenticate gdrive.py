#!/usr/bin/env python3
"""
Google Drive 認証スクリプト（gdrive_token.json 対応）
credentials.json から gdrive_token.json を生成
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# 設定
SCOPES = ['https://www.googleapis.com/auth/drive.file']
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'gdrive_token.json'  # ← 変更

def authenticate():
    """Google Drive API の認証を行う"""
    
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"❌ エラー: {CREDENTIALS_FILE} が見つかりません。")
        print("   Google Cloud Console から credentials.json をダウンロードしてください。")
        return False
    
    creds = None
    
    # 既存の token.json があれば読み込む
    if os.path.exists(TOKEN_FILE):
        print(f"📁 既存の {TOKEN_FILE} を読み込み中...")
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    
    # 認証が無効または期限切れの場合
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("🔄 トークンをリフレッシュ中...")
            try:
                creds.refresh(Request())
                print("✅ トークンのリフレッシュに成功しました。")
            except Exception as e:
                print(f"❌ リフレッシュ失敗: {e}")
                print("🔄 再認証します...")
                creds = None
        
        if not creds:
            print("\n🌐 ブラウザで認証を行います...")
            print("   1. ブラウザが自動的に開きます")
            print("   2. Google アカウントでログイン")
            print("   3. 「許可」をクリック")
            print("   4. 認証完了まで待機\n")
            
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)
            print("\n✅ 認証に成功しました！")
        
        # token.json に保存
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
        print(f"✅ {TOKEN_FILE} を保存しました。")
    else:
        print("✅ 既存の認証情報が有効です。")
    
    return True


if __name__ == '__main__':
    print("="*50)
    print("🔐 Google Drive 認証")
    print("="*50)
    
    if authenticate():
        print("\n" + "="*50)
        print("✅ 認証完了！")
        print("="*50)
        print("\n次のコマンドで実行できます:")
        print("  python main_pipeline.py normal")
    else:
        print("\n❌ 認証に失敗しました。")