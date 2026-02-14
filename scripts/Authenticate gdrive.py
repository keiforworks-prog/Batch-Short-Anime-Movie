#!/usr/bin/env python3
"""
Google Drive 隱崎ｨｼ繧ｹ繧ｯ繝ｪ繝励ヨ・・drive_token.json 蟇ｾ蠢懶ｼ・
credentials.json 縺九ｉ gdrive_token.json 繧堤函謌・
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# 險ｭ螳・
SCOPES = ['https://www.googleapis.com/auth/drive']
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'gdrive_token.json'  # 竊・螟画峩

def authenticate():
    """Google Drive API 縺ｮ隱崎ｨｼ繧定｡後≧"""
    
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"笶・繧ｨ繝ｩ繝ｼ: {CREDENTIALS_FILE} 縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ縲・)
        print("   Google Cloud Console 縺九ｉ credentials.json 繧偵ム繧ｦ繝ｳ繝ｭ繝ｼ繝峨＠縺ｦ縺上□縺輔＞縲・)
        return False
    
    creds = None
    
    # 譌｢蟄倥・ token.json 縺後≠繧後・隱ｭ縺ｿ霎ｼ繧
    if os.path.exists(TOKEN_FILE):
        print(f"刀 譌｢蟄倥・ {TOKEN_FILE} 繧定ｪｭ縺ｿ霎ｼ縺ｿ荳ｭ...")
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    
    # 隱崎ｨｼ縺檎┌蜉ｹ縺ｾ縺溘・譛滄剞蛻・ｌ縺ｮ蝣ｴ蜷・
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("売 繝医・繧ｯ繝ｳ繧偵Μ繝輔Ξ繝・す繝･荳ｭ...")
            try:
                creds.refresh(Request())
                print("笨・繝医・繧ｯ繝ｳ縺ｮ繝ｪ繝輔Ξ繝・す繝･縺ｫ謌仙粥縺励∪縺励◆縲・)
            except Exception as e:
                print(f"笶・繝ｪ繝輔Ξ繝・す繝･螟ｱ謨・ {e}")
                print("売 蜀崎ｪ崎ｨｼ縺励∪縺・..")
                creds = None
        
        if not creds:
            print("\n倹 繝悶Λ繧ｦ繧ｶ縺ｧ隱崎ｨｼ繧定｡後＞縺ｾ縺・..")
            print("   1. 繝悶Λ繧ｦ繧ｶ縺瑚・蜍慕噪縺ｫ髢九″縺ｾ縺・)
            print("   2. Google 繧｢繧ｫ繧ｦ繝ｳ繝医〒繝ｭ繧ｰ繧､繝ｳ")
            print("   3. 縲瑚ｨｱ蜿ｯ縲阪ｒ繧ｯ繝ｪ繝・け")
            print("   4. 隱崎ｨｼ螳御ｺ・∪縺ｧ蠕・ｩ歃n")
            
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)
            print("\n笨・隱崎ｨｼ縺ｫ謌仙粥縺励∪縺励◆・・)
        
        # token.json 縺ｫ菫晏ｭ・
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
        print(f"笨・{TOKEN_FILE} 繧剃ｿ晏ｭ倥＠縺ｾ縺励◆縲・)
    else:
        print("笨・譌｢蟄倥・隱崎ｨｼ諠・ｱ縺梧怏蜉ｹ縺ｧ縺吶・)
    
    return True


if __name__ == '__main__':
    print("="*50)
    print("柏 Google Drive 隱崎ｨｼ")
    print("="*50)
    
    if authenticate():
        print("\n" + "="*50)
        print("笨・隱崎ｨｼ螳御ｺ・ｼ・)
        print("="*50)
        print("\n谺｡縺ｮ繧ｳ繝槭Φ繝峨〒螳溯｡後〒縺阪∪縺・")
        print("  python main_pipeline.py normal")
    else:
        print("\n笶・隱崎ｨｼ縺ｫ螟ｱ謨励＠縺ｾ縺励◆縲・)
