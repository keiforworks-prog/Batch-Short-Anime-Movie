"""
プロジェクト全体の設定を一元管理するモジュール
クラウド環境への移行やバッチAPI利用を見据えた設計
"""
import os

# --- 環境判定 ---
IS_CLOUD_RUN = os.getenv("K_SERVICE") is not None  # Cloud Run環境かどうか

# --- ディレクトリ構成 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

# ログディレクトリ（Cloud Runでは/tmpを使用）
if IS_CLOUD_RUN:
    LOGS_DIR = "/tmp/logs"
else:
    LOGS_DIR = os.path.join(BASE_DIR, "logs")

# --- 共有ファイル ---
CONTACT_NOTE_FILE = os.path.join(BASE_DIR, "_current_project.json")

# --- Google Drive API ---
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
GDRIVE_SCOPES = ['https://www.googleapis.com/auth/drive']

# --- AI モデル設定 ---
# Claude API
CLAUDE_MODEL = "claude-sonnet-4-5"
CLAUDE_MAX_TOKENS = 4096

# OpenAI Image API
# 用途別モデル選択
USE_HIGH_QUALITY = False  # True: 高品質版($0.04/枚), False: mini版($0.01/枚)

if USE_HIGH_QUALITY:
    GPT_IMAGE_MODEL = "gpt-image-1"
else:
    GPT_IMAGE_MODEL = "gpt-image-1-mini"

IMAGE_SIZE = "1024x1536"
IMAGE_QUALITY = "high"

# --- Phase 1.3: モーションプロンプト設定 ---
# Hailuo 2.3 Fast
HAILUO_MODEL = "MiniMax-Hailuo-2.3-Fast"
HAILUO_DURATION = 6  # 秒
HAILUO_RESOLUTION = "768P"

# --- Phase 2: 画像生成設定 ---
# テストモード: 0=全画像生成, 1以上=指定枚数のみ生成
TEST_MODE_LIMIT = 0




# --- バッチAPI設定（将来用） ---
BATCH_API_ENABLED = False  # バッチAPI利用時にTrueに変更
BATCH_CHECK_INTERVAL = 300  # 5分ごとにステータス確認
BATCH_MAX_WAIT_TIME = 86400  # 最大24時間待機

# --- クラウド環境設定（将来用） ---
CLOUD_STORAGE_ENABLED = False  # クラウドストレージ利用時にTrueに
CLOUD_STORAGE_BUCKET = ""  # GCS/S3バケット名

# --- ログ設定 ---
LOG_PREFIX_ERROR = "ERROR_"
LOG_SUFFIX_PHASE1_1 = "_phase1_1_claude_settings.txt"
LOG_SUFFIX_PHASE1_2 = "_phase1_2_claude_prompts.txt"
LOG_SUFFIX_PHASE2 = "_phase2_gpt_images.txt"
LOG_SUFFIX_PHASE3 = "_phase3_gdrive.txt"

# --- API リトライ設定 ---
API_RETRY_COUNT = 3
API_RETRY_DELAY = 2  # 秒
MAX_RETRIES = 3  # エラー3回でアウト

# --- 並列処理設定（将来用） ---
MAX_WORKERS = 4  # 並列処理時の最大ワーカー数

# --- Phase別タイムアウト設定 ---
# 🔧 修正: main_pipeline.py で使用される正確な名前に合わせる
PHASE_TIMEOUTS = {
    "Phase 1.1 (Character Settings)": 600,     # 10分
    "Phase 1.2 (Claude Prompts)": 86400,       # 24時間 (プロンプト生成) 🔧 延長
    "Phase 2 (GPT Images)": 86400,             # 24時間 (画像生成) 🔧 延長
    "Phase 3 (Google Drive Upload)": 11800,     # 30分
    "Phase 1.3 (Motion Prompts)": 86400,      # 24時間
    "Phase 2.5 (Video Generation)": 86400,    # 24時間
    

    # 🆕 バッチAPI用（将来実装）
    "Phase 1.2-A (Batch Submit)": 300,         # 5分
    "Phase 1.2-B (Batch Retrieve)": 86400,     # 24時間
    "Phase 2-A (GPT Batch Submit)": 300,       # 5分
    "Phase 2-B (GPT Batch Retrieve)": 86400,   # 24時間
}

# ==================================================
# config.py に追加する設定（既存の設定の末尾に追加）
# ==================================================

# --- Hailuo (MiniMax) 動画生成設定 ---
HAILUO_MODEL = "MiniMax-Hailuo-2.3-Fast"    # Fastモデル (I2Vのみ、高速・低コスト)
HAILUO_RESOLUTION = "768P"                   # 768P or 1080P (1080Pは6秒まで)
HAILUO_DURATION = 6                          # 動画の長さ (秒): 6 or 10
HAILUO_POLL_INTERVAL = 15                    # ポーリング間隔 (秒)
HAILUO_MAX_WAIT_TIME = 300                   # タイムアウト (秒): 5分

# --- .env に追加する環境変数 ---
# MINIMAX_API_KEY=your-api-key-here

# --- Secret Manager に追加 (本番環境) ---
# gcloud secrets create MINIMAX_API_KEY --data-file=-






