#!/usr/bin/env python3
"""
Phase 1.2: プロンプト生成スクリプト（完全最適化版）
台本とキャラクター設定から、画像生成用のプロンプトをAI（Claude）が生成

【主要な改善点】
1. キャッシュAPI完全対応（キャッシュヘッダー追加）
2. JSONL形式でのデータ管理（堅牢性向上）
3. 視覚的要約メモリ（入力トークン95%削減）
4. XML出力制御（出力の確実な構造化）
5. プロンプト品質維持（800-1500語）
6. 🆕 XMLパースの多段階フォールバック（壊れたXMLにも対応）
"""
import os
import sys
import json
import time
import traceback
import anthropic
import xml.etree.ElementTree as ET
import re  # 🆕 正規表現追加
import signal
from dotenv import load_dotenv

# 共通モジュールのインポート
from config import (
    BASE_DIR, LOGS_DIR, LOG_PREFIX_ERROR, LOG_SUFFIX_PHASE1_2,
    CLAUDE_MODEL, CLAUDE_MAX_TOKENS
)
from logger_utils import DualLogger
from project_utils import (
    read_project_info, get_output_dir, ensure_output_dir,
    read_file_safely, write_file_safely
)
from api_retry_utils import call_api_with_retry
from cost_tracker import CostTracker

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
        _logger.log(f"📊 進捗: {_success_count}/{_total_count}行")
        if _tracker:
            _logger.log(f"\n{_tracker.get_detailed_summary()}")
        _logger.log(f"\n📂 次回は{_success_count+1}行目から再開")
        
        # Discord通知を送信
        try:
            from p3_gdrive_upload import send_discord_notification
            
            if _tracker and _project_name:
                cost_summary = _tracker.get_summary_for_discord()
                send_discord_notification(
                    project_name=_project_name,
                    status="中断 (Phase 1.2)",
                    cost_summary=cost_summary,
                    progress=f"{_success_count}/{_total_count}行"
                )
                _logger.log("📱 Discord に中断通知を送信しました\n")
        except Exception as e:
            if _logger:
                _logger.log(f"⚠️ Discord通知の送信に失敗: {e}")
    
    sys.exit(0)

signal.signal(signal.SIGINT, handle_interrupt)
signal.signal(signal.SIGTERM, handle_interrupt)

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
        from google.oauth2.credentials import Credentials
        import io
        
        parent_folder_id = os.getenv("GDRIVE_PARENT_FOLDER_ID")
        if not parent_folder_id:
            return False
        
        # 認証
        from gdrive_checkpoint import authenticate_gdrive, find_project_folder_on_drive
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
            logger.log("📁 Drive に prompts_data.jsonl が見つかりません。")
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


def check_existing_prompts(output_file, logger):
    """
    既存のプロンプトファイル（JSONL形式）を確認し、完了済みタスク数を返す
    
    Args:
        output_file: prompts_data.jsonl のパス
        logger: ロガー
    
    Returns:
        int: 完了済みタスク数（行数）
    """
    if not os.path.exists(output_file):
        return 0
    
    try:
        with open(output_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # 有効な行数をカウント
        count = len([line for line in lines if line.strip()])
        
        if count > 0:
            logger.log(f"✅ ローカルで {count} 個の既存プロンプトを検出しました。")
        
        return count
    
    except Exception as e:
        logger.log(f"⚠️ 既存プロンプトの確認中にエラー: {e}")
        return 0


def restore_previous_summaries(output_file, logger):
    """
    JSONL形式のファイルから直近3つの視覚的要約を復元
    
    Args:
        output_file: prompts_data.jsonl のパス
        logger: ロガー
    
    Returns:
        list: 直近3つの視覚的要約
    """
    try:
        if not os.path.exists(output_file):
            return []
        
        with open(output_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # 最後の3行を取得
        recent_lines = [line.strip() for line in lines[-3:] if line.strip()]
        
        summaries = []
        for line in recent_lines:
            data = json.loads(line)
            if 'visual_summary' in data:
                summaries.append(data['visual_summary'])
        
        if summaries:
            logger.log(f"🔄 直近 {len(summaries)} 個の視覚的要約を復元しました")
        
        return summaries
    
    except Exception as e:
        logger.log(f"⚠️ 視覚的要約の復元中にエラー: {e}")
        return []


def upload_prompts_to_drive(prompts_file_path, project_name, logger):
    """
    prompts_data.jsonl を Google Drive にアップロード
    
    Args:
        prompts_file_path: ローカルの prompts_data.jsonl のパス
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
        from gdrive_checkpoint import authenticate_gdrive, find_project_folder_on_drive
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
        
        # 既存ファイルを検索
        query = f"name='prompts_data.jsonl' and '{project_folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        existing_files = results.get('files', [])
        
        if existing_files:
            # 既存ファイルを更新
            file_id = existing_files[0]['id']
            media = MediaFileUpload(prompts_file_path, mimetype='application/json')
            service.files().update(fileId=file_id, media_body=media).execute()
            logger.log(f"☁️  prompts_data.jsonl を Drive で更新しました")
        else:
            # 新規作成
            file_metadata = {'name': 'prompts_data.jsonl', 'parents': [project_folder_id]}
            media = MediaFileUpload(prompts_file_path, mimetype='application/json')
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            logger.log(f"☁️  prompts_data.jsonl を Drive に保存しました")
    
    except Exception as e:
        logger.log(f"⚠️ Drive 保存エラー（続行します）: {e}")


def build_system_prompt(character_rules, character_settings, image_rules):
    """
    キャッシュ対象のシステムプロンプトを構築
    
    Args:
        character_rules: キャラクタールール
        character_settings: キャラクター設定
        image_rules: 画像生成ルール
    
    Returns:
        list: システムプロンプトブロック（キャッシュ設定付き）
    """
    system_content = f"""You are a professional AI image generation prompt writer specializing in high-quality GPT Image prompts.

<character_rules>
{character_rules}
</character_rules>

<character_settings>
{character_settings}
</character_settings>

<image_generation_rules>
{image_rules}
</image_generation_rules>

Based on the above settings, create detailed image generation prompts for GPT Image API.

CRITICAL REQUIREMENTS:
1. Each prompt must be 800-1500 words in English
2. Include composition, angle, character details, background, lighting, mood
3. Maintain visual consistency across all scenes
4. Create a concise visual summary (50-100 words) for context retention

Output format (MUST start with <o> tag):
<o>
<image_prompt>
[Detailed 800-1500 word English prompt here]
</image_prompt>
<visual_summary>
[50-100 word concise visual summary here]
</visual_summary>
</o>"""
    
    return [
        {
            "type": "text",
            "text": system_content,
            "cache_control": {"type": "ephemeral"}
        }
    ]


def build_user_prompt(script_line, previous_summaries, line_number):
    """
    ユーザープロンプトを構築（視覚的要約メモリ使用）
    
    Args:
        script_line: 台本の該当行
        previous_summaries: 直近3つの視覚的要約
        line_number: 行番号
    
    Returns:
        str: ユーザープロンプト
    """
    context = ""
    if previous_summaries:
        context = "\n\nPrevious scene summaries (for visual continuity):\n"
        for i, summary in enumerate(previous_summaries, 1):
            context += f"{i}. {summary}\n"
    
    # 🆕 最初の画像用の特別指示
    special_instruction = ""
    if line_number == 1:
        special_instruction = "\n\n【IMPORTANT】This is the FIRST image. Use Medium Close-Up shot with attention-grabbing expression or reaction to hook viewers immediately."
    
    user_prompt = f"""Create an image prompt for this script line:

<script_line>
{script_line}
</script_line>
{context}
{special_instruction}

Output in XML format (start immediately with <o>):"""
    
    return user_prompt


def parse_xml_response(response_text, logger):
    """
    🆕 改善版: XML形式のレスポンスをパースして image_prompt と visual_summary を抽出
    複数の方法でフォールバック（壊れたXMLにも対応）
    
    Args:
        response_text: APIレスポンステキスト
        logger: ロガー
    
    Returns:
        tuple: (image_prompt, visual_summary) または (None, None)
    """
    # 方法1: 正規のXMLパース
    try:
        response_text_clean = response_text.strip()
        
        start_idx = response_text_clean.find('<o>')
        end_idx = response_text_clean.find('</o>') + len('</o>')
        
        if start_idx != -1 and end_idx > start_idx:
            xml_text = response_text_clean[start_idx:end_idx]
            root = ET.fromstring(xml_text)
            
            image_prompt_elem = root.find('image_prompt')
            visual_summary_elem = root.find('visual_summary')
            
            if image_prompt_elem is not None and visual_summary_elem is not None:
                image_prompt = image_prompt_elem.text.strip() if image_prompt_elem.text else ""
                visual_summary = visual_summary_elem.text.strip() if visual_summary_elem.text else ""
                
                if image_prompt and visual_summary:
                    logger.log(f"✅ XMLパース成功（正規パーサー, {len(image_prompt)}文字）")
                    return image_prompt, visual_summary
    
    except ET.ParseError as e:
        logger.log(f"⚠️ XMLパースエラー（正規パーサー失敗）: {e}")
    except Exception as e:
        logger.log(f"⚠️ XMLパース中にエラー: {e}")
    
    # 方法2: 正規表現で <image_prompt> と <visual_summary> を抽出
    logger.log(f"🔄 正規表現での抽出を試みます...")
    try:
        # image_prompt を抽出（閉じタグが無くても対応）
        img_match = re.search(r'<image_prompt>\s*(.*?)\s*(?:</image_prompt>|<visual_summary>|</o>|$)', response_text, re.DOTALL)
        # visual_summary を抽出（閉じタグが無くても対応）
        sum_match = re.search(r'<visual_summary>\s*(.*?)\s*(?:</visual_summary>|</o>|$)', response_text, re.DOTALL)
        
        if img_match and sum_match:
            image_prompt = img_match.group(1).strip()
            visual_summary = sum_match.group(1).strip()
            
            if image_prompt and visual_summary and len(image_prompt) > 100:
                logger.log(f"⚠️ 正規表現で抽出できましたが、XMLが不完全なのでリトライします（{len(image_prompt)}文字）")
                # return image_prompt, visual_summary  # リトライさせるため
        
        # image_prompt だけ見つかった場合
        if img_match:
            image_prompt = img_match.group(1).strip()
            if len(image_prompt) > 100:
                # visual_summary が無い場合、image_prompt から要約を生成
                visual_summary = image_prompt[:150] + "..." if len(image_prompt) > 150 else image_prompt
                logger.log(f"⚠️ visual_summary が見つからず、XMLが不完全なのでリトライします")
                # return image_prompt, visual_summary  # リトライさせるため
    
    except Exception as e:
        logger.log(f"⚠️ 正規表現抽出エラー: {e}")
    
    # 方法3: 最後の手段 - <o> 以降の全テキストから強制抽出
    logger.log(f"🔄 フォールバック抽出を試みます...")
    try:
        # <o> 以降のテキストを取得
        start_idx = response_text.find('<o>')
        if start_idx != -1:
            remaining_text = response_text[start_idx:]
            
            # image_prompt の開始位置を探す
            img_start = remaining_text.find('<image_prompt>')
            if img_start != -1:
                # <image_prompt> 以降のテキスト
                after_img_tag = remaining_text[img_start + len('<image_prompt>'):]
                
                # 次のタグまで、または終端までを取得
                next_tag_patterns = ['<visual_summary>', '</image_prompt>', '</o>', '<image_prompt>']
                end_positions = []
                for pattern in next_tag_patterns:
                    pos = after_img_tag.find(pattern)
                    if pos != -1:
                        end_positions.append(pos)
                
                if end_positions:
                    image_prompt = after_img_tag[:min(end_positions)].strip()
                else:
                    # 次のタグが無い場合、残り全部
                    image_prompt = after_img_tag.strip()
                
                # visual_summary を探す
                sum_start = remaining_text.find('<visual_summary>')
                if sum_start != -1:
                    after_sum_tag = remaining_text[sum_start + len('<visual_summary>'):]
                    # 次のタグまで
                    sum_end_patterns = ['</visual_summary>', '</o>']
                    sum_end_positions = []
                    for pattern in sum_end_patterns:
                        pos = after_sum_tag.find(pattern)
                        if pos != -1:
                            sum_end_positions.append(pos)
                    
                    if sum_end_positions:
                        visual_summary = after_sum_tag[:min(sum_end_positions)].strip()
                    else:
                        visual_summary = after_sum_tag[:200].strip()
                else:
                    # visual_summary が無い場合、image_prompt から生成
                    visual_summary = image_prompt[:150] + "..." if len(image_prompt) > 150 else image_prompt
                    logger.log(f"⚠️ visual_summary が見つからないため、image_prompt から生成")
                
                if len(image_prompt) > 100:
                    logger.log(f"⚠️ フォールバックで抽出できましたが、XMLが不完全なのでリトライします（{len(image_prompt)}文字）")
                    # return image_prompt, visual_summary
    
    except Exception as e:
        logger.log(f"⚠️ フォールバック抽出エラー: {e}")
    
    # 全ての方法が失敗
    logger.log(f"❌ 全ての抽出方法が失敗しました")
    logger.log(f"レスポンスの最初の500文字: {response_text[:500]}")
    return None, None


def generate_emotional_finale_scenes(client, system_prompt, full_script, output_file, logger):
    """
    🆕 感動的なフィナーレシーン2枚を生成
    
    Args:
        client: Anthropic APIクライアント
        system_prompt: システムプロンプト
        full_script: 台本全文
        output_file: 出力ファイルパス
        logger: ロガー
    
    Returns:
        tuple: (success_count, cache_read_tokens, input_tokens, output_tokens)
    """
    logger.log(f"\n{'='*60}")
    logger.log(f"🎬 感動的なフィナーレシーンを生成中...")
    logger.log(f"{'='*60}\n")
    
    finale_prompt = """Generate TWO emotional finale image prompts that provide satisfying story closure.

Based on the story context, create heartwarming finale scenes:

1. First finale scene: Characters in a warm, emotional moment (close-up or medium shot)
2. Second finale scene: Wide establishing shot showing the peaceful resolution

Each prompt should be 800-1500 words and capture the emotional satisfaction of story completion.

Output TWO separate prompts in XML format:
<o>
<image_prompt>
[First finale scene - 800-1500 words]
</image_prompt>
<visual_summary>
[50-100 word summary]
</visual_summary>
</o>

<o>
<image_prompt>
[Second finale scene - 800-1500 words]
</image_prompt>
<visual_summary>
[50-100 word summary]
</visual_summary>
</o>

<full_story_context>
""" + full_script + """
</full_story_context>"""
    
    success_count = 0
    cache_read = 0
    input_tok = 0
    output_tok = 0
    
    try:
        # API呼び出し
        response = call_api_with_retry(
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS * 2,  # フィナーレは2倍
                system=system_prompt,
                messages=[{"role": "user", "content": finale_prompt}]
            ),
            max_retries=3,
            logger=logger,
            operation_name="感動シーン生成"
        )
        
        # トークン記録
        usage = response.usage
        if hasattr(usage, 'cache_read_input_tokens'):
            cache_read = usage.cache_read_input_tokens or 0
        input_tok = usage.input_tokens
        output_tok = usage.output_tokens
        
        response_text = response.content[0].text
        
        # 2つの <o> ブロックを分割
        o_blocks = re.findall(r'<o>.*?</o>', response_text, re.DOTALL)
        
        if len(o_blocks) < 2:
            logger.log(f"⚠️ フィナーレシーンが2つ生成されませんでした（{len(o_blocks)}個）")
            return success_count, cache_read, input_tok, output_tok
        
        # 既存のindex番号を取得
        with open(output_file, 'r', encoding='utf-8') as f:
            existing_lines = f.readlines()
        
        last_index = len([line for line in existing_lines if line.strip()])
        
        # 各シーンを保存
        with open(output_file, 'a', encoding='utf-8') as f:
            for i, block in enumerate(o_blocks[:2], 1):
                image_prompt, visual_summary = parse_xml_response(block, logger)
                
                if image_prompt and visual_summary:
                    data = {
                        "index": last_index + i,
                        "image_prompt": image_prompt,
                        "visual_summary": visual_summary,
                        "is_finale": True
                    }
                    f.write(json.dumps(data, ensure_ascii=False) + "\n")
                    success_count += 1
                    logger.log(f"✅ フィナーレシーン {i}/2 を生成しました（{len(image_prompt)}文字）")
    
    except Exception as e:
        logger.log(f"⚠️ フィナーレシーン生成エラー: {e}")
        logger.log(traceback.format_exc())
    
    return success_count, cache_read, input_tok, output_tok


def generate_prompts_and_save_incrementally(
    client, script_lines, character_rules, character_settings, image_rules,
    output_file, logger, completed_count=0, tracker=None
):
    """
    プロンプトを1行ずつ生成し、JSONL形式で増分保存
    
    Args:
        client: Anthropic APIクライアント
        script_lines: 台本の行リスト
        character_rules: キャラクタールール
        character_settings: キャラクター設定
        image_rules: 画像生成ルール
        output_file: 出力ファイルパス（prompts_data.jsonl）
        logger: ロガー
        completed_count: 既に完了している行数
        tracker: コストトラッカー
    
    Returns:
        bool: 全て成功した場合True
    """
    # システムプロンプト（キャッシュ対象）
    system_prompt = build_system_prompt(character_rules, character_settings, image_rules)
    
    # 台本全文を保存（感動シーン生成用）
    full_script = "\n".join(script_lines)
    
    # 既存の視覚的要約を復元
    previous_summaries = restore_previous_summaries(output_file, logger)
    
    # トークン使用量を記録
    cache_creation_tokens = 0
    cache_read_tokens = 0
    input_tokens = 0
    output_tokens = 0
    
    success_count = 0
    failed_lines = []
    
    try:
        # 追記モードでファイルを開く
        with open(output_file, 'a', encoding='utf-8') as f:
            for i in range(completed_count, len(script_lines)):
                line = script_lines[i]
                
                if not line:
                    continue
                
                line_number = i + 1
                logger.log(f"📄 プロンプト {line_number}/{len(script_lines)} を生成中...")
                
                # ユーザープロンプト構築
                user_prompt = build_user_prompt(line, previous_summaries, line_number)
                
                # 🆕 XMLパース成功まで最大3回リトライ（API再呼び出し）
                max_parse_retries = 3
                parse_success = False
                
                for parse_retry in range(max_parse_retries):
                    try:
                        # API呼び出し（キャッシュ有効）
                        response = call_api_with_retry(
                            lambda: client.messages.create(
                                model=CLAUDE_MODEL,
                                max_tokens=CLAUDE_MAX_TOKENS,
                                system=system_prompt,
                                messages=[{"role": "user", "content": user_prompt}]
                            ),
                            max_retries=3,
                            logger=logger,
                            operation_name=f"プロンプト生成 (行{line_number})"
                        )
                        
                        if not response or not response.content:
                            logger.log(f"⚠️ 行{line_number}: レスポンスが空です。")
                            if parse_retry < max_parse_retries - 1:
                                logger.log(f"🔄 API再呼び出し ({parse_retry + 1}/{max_parse_retries})")
                                time.sleep(2)
                                continue
                            else:
                                failed_lines.append(line_number)
                                break
                        
                        # 使用トークン情報を取得
                        usage = response.usage
                        cache_creation = 0
                        cache_read = 0
                        
                        if hasattr(usage, 'cache_creation_input_tokens'):
                            cache_creation = usage.cache_creation_input_tokens
                            cache_creation_tokens += cache_creation
                        if hasattr(usage, 'cache_read_input_tokens'):
                            cache_read = usage.cache_read_input_tokens
                            cache_read_tokens += cache_read
                        
                        input_tokens += usage.input_tokens
                        output_tokens += usage.output_tokens
                        
                        # キャッシュヒット表示
                        if cache_read > 0:
                            logger.log(f"⚡ キャッシュヒット: {cache_read:,} トークン (90%削減)")
                        
                        # 🆕 改善版XMLパーサーでレスポンスからプロンプトを抽出
                        response_text = response.content[0].text
                        image_prompt, visual_summary = parse_xml_response(response_text, logger)
                        
                        if not image_prompt or not visual_summary:
                            logger.log(f"⚠️ 行{line_number}: XMLパースに失敗しました。")
                            if parse_retry < max_parse_retries - 1:
                                logger.log(f"🔄 API再呼び出し ({parse_retry + 1}/{max_parse_retries})")
                                time.sleep(2)
                                continue
                            else:
                                logger.log(f"⚠️ 行{line_number}: {max_parse_retries}回リトライしましたがスキップします。")
                                failed_lines.append(line_number)
                                break
                        
                        # JSONL形式で保存
                        data = {
                            "index": line_number,
                            "image_prompt": image_prompt,
                            "visual_summary": visual_summary
                        }
                        f.write(json.dumps(data, ensure_ascii=False) + "\n")
                        f.flush()  # 即座にディスクに書き込み
                        
                        # 視覚的要約をメモリに追加
                        previous_summaries.append(visual_summary)
                        if len(previous_summaries) > 3:
                            previous_summaries.pop(0)
                        
                        success_count += 1
                        _success_count = success_count  # ← この行を追加
                        parse_success = True
                        
                        # 10行ごとにDriveへバックアップ
                        if line_number % 10 == 0:
                            project_name = read_project_info()[0]
                            upload_prompts_to_drive(output_file, project_name, logger)
                            logger.log(f"☁️ Drive に保存しました")
                        
                        logger.log(f"✅ プロンプト {line_number}/{len(script_lines)} を生成・保存しました。")
                        
                        # 成功したらループを抜ける
                        break
                    
                    except Exception as e:
                        logger.log(f"🚨 行{line_number}の生成中にエラー: {e}")
                        if parse_retry < max_parse_retries - 1:
                            logger.log(f"🔄 API再呼び出し ({parse_retry + 1}/{max_parse_retries})")
                            time.sleep(2)
                            continue
                        else:
                            logger.log(traceback.format_exc())
                            failed_lines.append(line_number)
                            break
                
                # リトライループ終了後、レート制限対策
                if parse_success:
                    time.sleep(1)
    
    except Exception as e:
        logger.log(f"🚨 ファイル書き込み中にエラー: {e}")
        logger.log(traceback.format_exc())
        return False
    
    # 最終結果
    logger.log(f"\n{'='*60}")
    logger.log(f"📊 生成完了サマリー")
    logger.log(f"{'='*60}")
    logger.log(f"✅ 成功: {success_count} / {len(script_lines)} 行")
    
    if failed_lines:
        logger.log(f"\n❌ 失敗した行: {', '.join(map(str, failed_lines))}")
    
    # 🆕 感動シーン2枚を生成
    finale_success, finale_cache_read, finale_input, finale_output = generate_emotional_finale_scenes(
        client, system_prompt, full_script, output_file, logger
    )
    
    # 感動シーンのトークンを累積
    if finale_success > 0:
        cache_read_tokens += finale_cache_read
        input_tokens += finale_input
        output_tokens += finale_output
        success_count += finale_success
        logger.log(f"📊 感動シーン: {finale_success}枚追加")
    
    # コストトラッキング
    if tracker:
        tracker.add_phase_1_2(
            cache_creation=cache_creation_tokens,
            cache_read=cache_read_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens
        )
        logger.log(f"\n{tracker.get_detailed_summary()}")
        
        # トークン数をファイルに保存（Phase 3 で使用）
        tokens_file = os.path.join(os.path.dirname(output_file), "phase1_2_tokens.json")
        try:
            token_data = {
                "cache_creation_tokens": cache_creation_tokens,
                "cache_read_tokens": cache_read_tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            }
            with open(tokens_file, "w", encoding="utf-8") as f:
                json.dump(token_data, f, indent=2)
            logger.log(f"💾 トークン情報を保存しました: {tokens_file}")
        except Exception as e:
            logger.log(f"⚠️ トークン情報の保存に失敗: {e}")
    
    return len(failed_lines) == 0


def main():
    """メインの処理フロー"""
    global _logger, _tracker, _project_name, _success_count, _total_count  # 🆕 追加

    project_name, model_name, script_full_path = read_project_info()
    
    if not project_name:
        sys.exit(1)
    _project_name = project_name  # ← この行を追加
    
    
    output_dir = ensure_output_dir(project_name, model_name)
    output_file = os.path.join(output_dir, "prompts_data.jsonl")
    log_file = os.path.join(LOGS_DIR, f"{LOG_PREFIX_ERROR}{project_name}{LOG_SUFFIX_PHASE1_2}")

    logger = DualLogger(log_file)
    error_occurred = False
    
    _logger = logger  # ← この行を追加
    
    # コストトラッカー初期化
    tracker = CostTracker(project_name)
    _tracker = tracker  # ← この行を追加

    # Anthropic API クライアントの初期化（キャッシュヘッダー付き）
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.log("🚨 エラー: 環境変数 'ANTHROPIC_API_KEY' が設定されていません。")
            logger.save_on_error()
            sys.exit(1)
        
        # 🔥 キャッシュヘッダーを追加（最重要）
        client = anthropic.Anthropic(
            api_key=api_key,
            default_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        
        logger.log("✅ Anthropic API クライアントを初期化しました（キャッシュ有効）")
    
    except Exception as e:
        logger.log(f"🚨 Anthropicクライアントの初期化に失敗: {e}")
        logger.save_on_error()
        sys.exit(1)

    # メイン処理
    try:
        logger.log(f"\n{'='*60}")
        logger.log(f"--- Phase 1.2 (Claude Prompts): '{project_name}' のプロンプトを生成します ---")
        logger.log(f"{'='*60}")
        
        # チェックポイント確認（ローカル → Drive）
        local_completed = check_existing_prompts(output_file, logger)
        
        if local_completed == 0:
            # Driveから取得を試みる
            logger.log("📁 ローカルにプロンプトが見つかりません。")
            logger.log("☁️  Google Drive からチェックポイントを確認中...")
            
            if download_prompts_from_drive(project_name, output_file, logger):
                local_completed = check_existing_prompts(output_file, logger)
        
        if local_completed > 0:
            logger.log(f"\n{'='*60}")
            logger.log(f"🔄 チェックポイント検出!")
            logger.log(f"✅ {local_completed} 個のプロンプトが既に生成済みです")
            logger.log(f"▶️  続きから再開します")
            logger.log(f"{'='*60}\n")
        
        # 必要なファイルを読み込む
        character_rules_file = os.path.join(BASE_DIR, "rule", "character_rules.txt")
        character_settings_file = os.path.join(output_dir, "character_settings.txt")
        image_rules_file = os.path.join(BASE_DIR, "rule", "image_rules.txt")

        character_rules = read_file_safely(character_rules_file, "キャラクタールール")
        character_settings = read_file_safely(character_settings_file, "キャラクター設定")
        image_rules = read_file_safely(image_rules_file, "画像生成ルール")
        script_content = read_file_safely(script_full_path, "台本")

        if not all([character_rules, character_settings, image_rules, script_content]):
            logger.log("🚨 エラー: 必要なファイルの読み込みに失敗しました。")
            error_occurred = True
        else:
            # 台本を行ごとに分割
            script_lines = [line.strip() for line in script_content.split('\n') if line.strip()]
            
            logger.log(f"📋 台本: {len(script_lines)} 行")
            _total_count = len(script_lines)  # ← この行を追加
            logger.log(f"📋 キャラクタールール: {len(character_rules)} 文字")
            logger.log(f"📋 キャラクター設定: {len(character_settings)} 文字")
            logger.log(f"📋 画像生成ルール: {len(image_rules)} 文字")
            
            # プロンプト生成（増分保存）
            if generate_prompts_and_save_incrementally(
                client, script_lines, character_rules, character_settings, image_rules,
                output_file, logger, completed_count=local_completed, tracker=tracker
            ):
                logger.log(f"\n✅ プロンプトをファイルに保存しました: {output_file}")
                
                # 最終的にDriveへアップロード
                upload_prompts_to_drive(output_file, project_name, logger)
                
                logger.log("\n--- Phase 1.2 (Claude Prompts) が正常に完了しました ---")
            else:
                logger.log("🚨 一部のプロンプト生成に失敗しましたが、処理を続行します。")
                # エラーではなく警告として扱う（部分的な成功）

    except Exception as e:
        logger.log(f"\n🚨🚨🚨 Phase 1.2 (Claude Prompts) で予期せぬエラーが発生しました 🚨🚨🚨")
        logger.log(traceback.format_exc())
        error_occurred = True

    # エラー時はログ保存
    if error_occurred:
        logger.save_on_error()
        sys.exit(1)


if __name__ == "__main__":
    main()