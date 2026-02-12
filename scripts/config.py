"""
繝励Ο繧ｸ繧ｧ繧ｯ繝亥・菴薙・險ｭ螳壹ｒ荳蜈・ｮ｡逅・☆繧九Δ繧ｸ繝･繝ｼ繝ｫ
繧ｯ繝ｩ繧ｦ繝臥腸蠅・∈縺ｮ遘ｻ陦後ｄ繝舌ャ繝、PI蛻ｩ逕ｨ繧定ｦ区紺縺医◆險ｭ險・
"""
import os

# --- 迺ｰ蠅・愛螳・---
IS_CLOUD_RUN = os.getenv("K_SERVICE") is not None  # Cloud Run迺ｰ蠅・°縺ｩ縺・°

# --- 繝・ぅ繝ｬ繧ｯ繝医Μ讒区・ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

# 繝ｭ繧ｰ繝・ぅ繝ｬ繧ｯ繝医Μ・・loud Run縺ｧ縺ｯ/tmp繧剃ｽｿ逕ｨ・・
if IS_CLOUD_RUN:
    LOGS_DIR = "/tmp/logs"
else:
    LOGS_DIR = os.path.join(BASE_DIR, "logs")

# --- 蜈ｱ譛峨ヵ繧｡繧､繝ｫ ---
CONTACT_NOTE_FILE = os.path.join(BASE_DIR, "_current_project.json")

# --- Google Drive API ---
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
GDRIVE_SCOPES = ['https://www.googleapis.com/auth/drive']

# --- AI 繝｢繝・Ν險ｭ螳・---
# Claude API
CLAUDE_MODEL = "claude-sonnet-4-5"
CLAUDE_MAX_TOKENS = 4096

# OpenAI Image API
# 逕ｨ騾泌挨繝｢繝・Ν驕ｸ謚・
USE_HIGH_QUALITY = False  # True: 鬮伜刀雉ｪ迚・$0.04/譫・, False: mini迚・$0.01/譫・

if USE_HIGH_QUALITY:
    GPT_IMAGE_MODEL = "gpt-image-1"
else:
    GPT_IMAGE_MODEL = "gpt-image-1-mini"

IMAGE_SIZE = "1024x1536"
IMAGE_QUALITY = "high"

# --- Phase 1.3: 繝｢繝ｼ繧ｷ繝ｧ繝ｳ繝励Ο繝ｳ繝励ヨ險ｭ螳・---
# Hailuo 2.3 Fast
HAILUO_MODEL = "MiniMax-Hailuo-2.3-Fast"
HAILUO_DURATION = 6  # 遘・
HAILUO_RESOLUTION = "768P"

# --- Phase 2: 逕ｻ蜒冗函謌占ｨｭ螳・---
# 繝・せ繝医Δ繝ｼ繝・ 0=蜈ｨ逕ｻ蜒冗函謌・ 1莉･荳・謖・ｮ壽椢謨ｰ縺ｮ縺ｿ逕滓・
TEST_MODE_LIMIT = 0



# --- 繝舌ャ繝、PI險ｭ螳夲ｼ亥ｰ・擂逕ｨ・・---
BATCH_API_ENABLED = False  # 繝舌ャ繝、PI蛻ｩ逕ｨ譎ゅ↓True縺ｫ螟画峩
BATCH_CHECK_INTERVAL = 300  # 5蛻・＃縺ｨ縺ｫ繧ｹ繝・・繧ｿ繧ｹ遒ｺ隱・
BATCH_MAX_WAIT_TIME = 86400  # 譛螟ｧ24譎る俣蠕・ｩ・

# --- 繧ｯ繝ｩ繧ｦ繝臥腸蠅・ｨｭ螳夲ｼ亥ｰ・擂逕ｨ・・---
CLOUD_STORAGE_ENABLED = False  # 繧ｯ繝ｩ繧ｦ繝峨せ繝医Ξ繝ｼ繧ｸ蛻ｩ逕ｨ譎ゅ↓True縺ｫ
CLOUD_STORAGE_BUCKET = ""  # GCS/S3繝舌こ繝・ヨ蜷・

# --- 繝ｭ繧ｰ險ｭ螳・---
LOG_PREFIX_ERROR = "ERROR_"
LOG_SUFFIX_PHASE1_1 = "_phase1_1_claude_settings.txt"
LOG_SUFFIX_PHASE1_2 = "_phase1_2_claude_prompts.txt"
LOG_SUFFIX_PHASE2 = "_phase2_gpt_images.txt"
LOG_SUFFIX_PHASE3 = "_phase3_gdrive.txt"

# --- API 繝ｪ繝医Λ繧､險ｭ螳・---
API_RETRY_COUNT = 3
API_RETRY_DELAY = 2  # 遘・
MAX_RETRIES = 3  # 繧ｨ繝ｩ繝ｼ3蝗槭〒繧｢繧ｦ繝・

# --- 荳ｦ蛻怜・逅・ｨｭ螳夲ｼ亥ｰ・擂逕ｨ・・---
MAX_WORKERS = 4  # 荳ｦ蛻怜・逅・凾縺ｮ譛螟ｧ繝ｯ繝ｼ繧ｫ繝ｼ謨ｰ

# --- Phase蛻･繧ｿ繧､繝繧｢繧ｦ繝郁ｨｭ螳・---
# 肌 菫ｮ豁｣: main_pipeline.py 縺ｧ菴ｿ逕ｨ縺輔ｌ繧区ｭ｣遒ｺ縺ｪ蜷榊燕縺ｫ蜷医ｏ縺帙ｋ
PHASE_TIMEOUTS = {
    "Phase 1.1 (Character Settings)": 600,     # 10蛻・
    "Phase 1.2 (Claude Prompts)": 86400,       # 24譎る俣 (繝励Ο繝ｳ繝励ヨ逕滓・) 肌 蟒ｶ髟ｷ
    "Phase 2 (GPT Images)": 86400,             # 24譎る俣 (逕ｻ蜒冗函謌・ 肌 蟒ｶ髟ｷ
    "Phase 3 (Google Drive Upload)": 1800,     # 30蛻・
    "Phase 1.3 (Motion Prompts)": 86400,      # 24譎る俣
    "Phase 2.5 (Video Generation)": 86400,    # 24譎る俣
    

    # ・ 繝舌ャ繝、PI逕ｨ・亥ｰ・擂螳溯｣・ｼ・
    "Phase 1.2-A (Batch Submit)": 300,         # 5蛻・
    "Phase 1.2-B (Batch Retrieve)": 86400,     # 24譎る俣
    "Phase 2-A (GPT Batch Submit)": 300,       # 5蛻・
    "Phase 2-B (GPT Batch Retrieve)": 86400,   # 24譎る俣
}

# ==================================================
# config.py 縺ｫ霑ｽ蜉縺吶ｋ險ｭ螳夲ｼ域里蟄倥・險ｭ螳壹・譛ｫ蟆ｾ縺ｫ霑ｽ蜉・・
# ==================================================

# --- Hailuo (MiniMax) 蜍慕判逕滓・險ｭ螳・---
HAILUO_MODEL = "MiniMax-Hailuo-2.3-Fast"    # Fast繝｢繝・Ν (I2V縺ｮ縺ｿ縲・ｫ倬溘・菴弱さ繧ｹ繝・
HAILUO_RESOLUTION = "768P"                   # 768P or 1080P (1080P縺ｯ6遘偵∪縺ｧ)
HAILUO_DURATION = 6                          # 蜍慕判縺ｮ髟ｷ縺・(遘・: 6 or 10
HAILUO_POLL_INTERVAL = 15                    # 繝昴・繝ｪ繝ｳ繧ｰ髢馴囈 (遘・
HAILUO_MAX_WAIT_TIME = 300                   # 繧ｿ繧､繝繧｢繧ｦ繝・(遘・: 5蛻・

# --- .env 縺ｫ霑ｽ蜉縺吶ｋ迺ｰ蠅・､画焚 ---
# MINIMAX_API_KEY=your-api-key-here

# --- Secret Manager 縺ｫ霑ｽ蜉 (譛ｬ逡ｪ迺ｰ蠅・ ---
# gcloud secrets create MINIMAX_API_KEY --data-file=-






