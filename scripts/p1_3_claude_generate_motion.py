#!/usr/bin/env python3
"""
Phase 1.3: モーションプロンプト生成スクリプト
画像プロンプト（prompts_data.jsonl）から、動画生成用のモーションプロンプトを生成

【入力】prompts_data.jsonl（Phase 1.2の出力）
【出力】motion_prompts_list.txt（1行1モーションプロンプト）
【キャッシュ】video_motion_rules.txt のみ
【毎回送信】画像プロンプト + 直近3つのカメラワーク結果
"""
import os
import sys
import json
import time
import traceback
import anthropic
import re
import signal
from dotenv import load_dotenv

# 共通モジュールのインポート
from config import (
    BASE_DIR, LOGS_DIR, LOG_PREFIX_ERROR,
    CLAUDE_MODEL, CLAUDE_MAX_TOKENS,
    TEST_MODE_LIMIT
)
from logger_utils import DualLogger
from project_utils import (
    read_project_info, get_output_dir, ensure_output_dir,
    read_file_safely
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
        _logger.log("\n⚠️ Phase 1.3 処理が中断されました")
        _logger.log(f"📊 進捗: {_success_count}/{_total_count}")
        if _tracker:
            _logger.log(f"\n{_tracker.get_detailed_summary()}")
    
    sys.exit(0)

signal.signal(signal.SIGINT, handle_interrupt)
signal.signal(signal.SIGTERM, handle_interrupt)


def load_image_prompts(jsonl_file, logger):
    """
    prompts_data.jsonl から画像プロンプトを読み込む
    
    Args:
        jsonl_file: prompts_data.jsonl のパス
        logger: ロガー
    
    Returns:
        list: [{"index": 1, "image_prompt": "..."}, ...]
    """
    prompts = []
    
    if not os.path.exists(jsonl_file):
        logger.log(f"🚨 ファイルが見つかりません: {jsonl_file}")
        return prompts
    
    try:
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    prompts.append({
                        "index": data.get("index", len(prompts) + 1),
                        "image_prompt": data.get("image_prompt", "")
                    })
                except json.JSONDecodeError as e:
                    logger.log(f"⚠️ JSONパースエラー: {e}")
                    continue
        
        logger.log(f"📋 {len(prompts)} 個の画像プロンプトを読み込みました")
    
    except Exception as e:
        logger.log(f"🚨 ファイル読み込みエラー: {e}")
    
    return prompts


def check_existing_motion_prompts(output_file, logger):
    """
    既存のモーションプロンプトを確認し、完了済み数を返す
    
    Args:
        output_file: motion_prompts_list.txt のパス
        logger: ロガー
    
    Returns:
        int: 完了済み数
    """
    if not os.path.exists(output_file):
        return 0
    
    try:
        with open(output_file, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
        
        count = len(lines)
        if count > 0:
            logger.log(f"✅ {count} 個の既存モーションプロンプトを検出")
        
        return count
    
    except Exception as e:
        logger.log(f"⚠️ 既存ファイル確認エラー: {e}")
        return 0


def restore_previous_camera_works(output_file, logger):
    """
    既存のモーションプロンプトから直近3つのカメラワークを復元
    
    Args:
        output_file: motion_prompts_list.txt のパス
        logger: ロガー
    
    Returns:
        list: 直近3つのモーションプロンプト
    """
    try:
        if not os.path.exists(output_file):
            return []
        
        with open(output_file, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
        
        recent = lines[-3:] if len(lines) >= 3 else lines
        
        if recent:
            logger.log(f"🔄 直近 {len(recent)} 個のカメラワークを復元")
        
        return recent
    
    except Exception as e:
        logger.log(f"⚠️ カメラワーク復元エラー: {e}")
        return []


def build_system_prompt(motion_rules):
    """
    キャッシュ対象のシステムプロンプトを構築
    
    Args:
        motion_rules: video_motion_rules.txt の内容
    
    Returns:
        list: システムプロンプトブロック（キャッシュ設定付き）
    """
    system_content = f"""You are a professional video motion director specializing in anime-style Image-to-Video (I2V) generation using Hailuo 2.3 Fast.

Your task: Given an image generation prompt, create a concise motion prompt that describes camera movement and character/environment animation for a 6-second video clip.

<video_motion_rules>
{motion_rules}
</video_motion_rules>

CRITICAL REQUIREMENTS:
1. Output ONLY the motion prompt (50-150 words in English)
2. Start with camera command(s) in [brackets]
3. Describe natural, subtle movements appropriate for I2V
4. Include lip movement for dialogue scenes
5. Avoid movements that would break the static image (no 180° turns, no new characters appearing)
6. Vary camera work to avoid repetition with recent scenes

Output format (MUST start with <motion> tag):
<motion>
[Camera command] Description of character movement, environmental animation, and mood...
</motion>"""
    
    return [
        {
            "type": "text",
            "text": system_content,
            "cache_control": {"type": "ephemeral"}
        }
    ]


def build_user_prompt(image_prompt, previous_camera_works, prompt_index, total_count):
    """
    ユーザープロンプトを構築
    
    Args:
        image_prompt: 画像生成プロンプト
        previous_camera_works: 直近3つのモーションプロンプト
        prompt_index: 現在のインデックス
        total_count: 総数
    
    Returns:
        str: ユーザープロンプト
    """
    context = ""
    if previous_camera_works:
        context = "\n\nRecent camera works (AVOID repeating the same camera commands):\n"
        for i, work in enumerate(previous_camera_works, 1):
            # 長い場合は先頭100文字のみ
            truncated = work[:100] + "..." if len(work) > 100 else work
            context += f"{i}. {truncated}\n"
    
    position_hint = ""
    if prompt_index == 1:
        position_hint = "\n\n【IMPORTANT】This is the FIRST scene. Use an attention-grabbing camera movement like [Push in] or [Zoom in] to hook viewers."
    elif prompt_index == total_count:
        position_hint = "\n\n【IMPORTANT】This is the FINAL scene. Use a slow, emotional camera movement like [Zoom in] or [Pull out] for a satisfying ending."
    
    user_prompt = f"""Create a motion prompt for this image (scene {prompt_index}/{total_count}):

<image_prompt>
{image_prompt}
</image_prompt>
{context}
{position_hint}

Output the motion prompt (start immediately with <motion>):"""
    
    return user_prompt


def parse_motion_response(response_text, logger):
    """
    レスポンスからモーションプロンプトを抽出
    
    Args:
        response_text: APIレスポンステキスト
        logger: ロガー
    
    Returns:
        str or None: モーションプロンプト
    """
    # 方法1: <motion>タグで抽出
    try:
        match = re.search(r'<motion>\s*(.*?)\s*</motion>', response_text, re.DOTALL)
        if match:
            motion = match.group(1).strip()
            if len(motion) > 20:
                logger.log(f"✅ モーションプロンプト抽出成功（{len(motion)}文字）")
                return motion
    except Exception as e:
        logger.log(f"⚠️ 正規表現パースエラー: {e}")
    
    # 方法2: <motion>タグの開始のみ見つかった場合
    try:
        start_idx = response_text.find('<motion>')
        if start_idx != -1:
            after_tag = response_text[start_idx + len('<motion>'):].strip()
            # </motion>があればそこまで、なければ全部
            end_idx = after_tag.find('</motion>')
            if end_idx != -1:
                motion = after_tag[:end_idx].strip()
            else:
                motion = after_tag.strip()
            
            if len(motion) > 20:
                logger.log(f"⚠️ フォールバックで抽出（{len(motion)}文字）")
                return motion
    except Exception as e:
        logger.log(f"⚠️ フォールバック抽出エラー: {e}")
    
    # 方法3: タグなしでもカメラコマンドで始まるテキストを探す
    try:
        match = re.search(r'(\[(?:Truck|Pan|Push|Pull|Pedestal|Tilt|Zoom|Shake|Tracking|Static)[^\]]*\].*)', response_text, re.DOTALL)
        if match:
            motion = match.group(1).strip()
            # 最初の段落のみ
            motion = motion.split('\n\n')[0].strip()
            if len(motion) > 20:
                logger.log(f"⚠️ カメラコマンド検出で抽出（{len(motion)}文字）")
                return motion
    except Exception as e:
        logger.log(f"⚠️ カメラコマンド検出エラー: {e}")
    
    logger.log(f"❌ モーションプロンプト抽出失敗")
    logger.log(f"レスポンスの最初の300文字: {response_text[:300]}")
    return None


def generate_motion_prompts(
    client, image_prompts, motion_rules,
    output_file, logger, completed_count=0, tracker=None
):
    """
    モーションプロンプトを1つずつ生成し、増分保存
    
    Args:
        client: Anthropic APIクライアント
        image_prompts: 画像プロンプトのリスト
        motion_rules: モーションルールテキスト
        output_file: 出力ファイルパス
        logger: ロガー
        completed_count: 既に完了している数
        tracker: コストトラッカー
    
    Returns:
        bool: 全て成功した場合True
    """
    global _success_count
    
    # システムプロンプト（キャッシュ対象）
    system_prompt = build_system_prompt(motion_rules)
    
    # 既存のカメラワークを復元
    previous_camera_works = restore_previous_camera_works(output_file, logger)
    
    # トークン使用量
    cache_creation_tokens = 0
    cache_read_tokens = 0
    input_tokens = 0
    output_tokens = 0
    
    success_count = 0
    failed_indices = []
    total = len(image_prompts)
    
    try:
        with open(output_file, 'a', encoding='utf-8') as f:
            for i in range(completed_count, total):
                prompt_data = image_prompts[i]
                image_prompt = prompt_data["image_prompt"]
                prompt_index = i + 1
                
                logger.log(f"\n📄 モーション {prompt_index}/{total} を生成中...")
                
                # ユーザープロンプト構築
                user_prompt = build_user_prompt(
                    image_prompt, previous_camera_works, prompt_index, total
                )
                
                # 最大3回リトライ
                max_retries = 3
                parse_success = False
                
                for retry in range(max_retries):
                    try:
                        # API呼び出し
                        response = call_api_with_retry(
                            lambda: client.messages.create(
                                model=CLAUDE_MODEL,
                                max_tokens=512,  # モーションプロンプトは短い
                                system=system_prompt,
                                messages=[{"role": "user", "content": user_prompt}]
                            ),
                            max_retries=3,
                            logger=logger,
                            operation_name=f"モーション生成 ({prompt_index})"
                        )
                        
                        if not response or not response.content:
                            logger.log(f"⚠️ {prompt_index}: レスポンスが空")
                            if retry < max_retries - 1:
                                time.sleep(2)
                                continue
                            else:
                                failed_indices.append(prompt_index)
                                break
                        
                        # トークン記録
                        usage = response.usage
                        if hasattr(usage, 'cache_creation_input_tokens'):
                            cache_creation_tokens += usage.cache_creation_input_tokens or 0
                        if hasattr(usage, 'cache_read_input_tokens'):
                            cache_read = usage.cache_read_input_tokens or 0
                            cache_read_tokens += cache_read
                            if cache_read > 0:
                                logger.log(f"⚡ キャッシュヒット: {cache_read:,} トークン")
                        
                        input_tokens += usage.input_tokens
                        output_tokens += usage.output_tokens
                        
                        # モーションプロンプト抽出
                        response_text = response.content[0].text
                        motion_prompt = parse_motion_response(response_text, logger)
                        
                        if not motion_prompt:
                            if retry < max_retries - 1:
                                logger.log(f"🔄 リトライ ({retry + 1}/{max_retries})")
                                time.sleep(2)
                                continue
                            else:
                                failed_indices.append(prompt_index)
                                break
                        
                        # 1行として保存（改行を除去）
                        clean_prompt = motion_prompt.replace('\n', ' ').strip()
                        f.write(clean_prompt + "\n")
                        f.flush()
                        
                        # カメラワーク履歴を更新
                        previous_camera_works.append(clean_prompt)
                        if len(previous_camera_works) > 3:
                            previous_camera_works.pop(0)
                        
                        success_count += 1
                        _success_count = success_count
                        parse_success = True
                        
                        logger.log(f"✅ モーション {prompt_index}/{total} 完了")
                        break
                    
                    except Exception as e:
                        logger.log(f"🚨 {prompt_index} 生成エラー: {e}")
                        if retry < max_retries - 1:
                            time.sleep(2)
                            continue
                        else:
                            logger.log(traceback.format_exc())
                            failed_indices.append(prompt_index)
                            break
                
                # レート制限対策
                if parse_success:
                    time.sleep(0.5)
    
    except Exception as e:
        logger.log(f"🚨 ファイル書き込みエラー: {e}")
        logger.log(traceback.format_exc())
        return False
    
    # 結果サマリー
    logger.log(f"\n{'='*60}")
    logger.log(f"📊 Phase 1.3 生成完了サマリー")
    logger.log(f"{'='*60}")
    logger.log(f"✅ 成功: {success_count} / {total}")
    
    if failed_indices:
        logger.log(f"❌ 失敗: {', '.join(map(str, failed_indices))}")
    
    # コストトラッキング
    if tracker:
        # Phase 1.3 用のトラッキング（Phase 1.2 と同じメソッドを流用）
        tracker.add_phase_1_2(
            cache_creation=cache_creation_tokens,
            cache_read=cache_read_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens
        )
        logger.log(f"\n{tracker.get_detailed_summary()}")
        
        # トークン情報保存
        tokens_file = os.path.join(os.path.dirname(output_file), "phase1_3_tokens.json")
        try:
            token_data = {
                "cache_creation_tokens": cache_creation_tokens,
                "cache_read_tokens": cache_read_tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            }
            with open(tokens_file, "w", encoding="utf-8") as f:
                json.dump(token_data, f, indent=2)
            logger.log(f"💾 トークン情報を保存: {tokens_file}")
        except Exception as e:
            logger.log(f"⚠️ トークン情報の保存に失敗: {e}")
    
    return len(failed_indices) == 0


def main():
    """メインの処理フロー"""
    global _logger, _tracker, _project_name, _total_count
    
    project_name, model_name, script_full_path = read_project_info()
    
    if not project_name:
        sys.exit(1)
    _project_name = project_name
    
    output_dir = ensure_output_dir(project_name, model_name)
    
    # 入出力ファイルパス
    input_file = os.path.join(output_dir, "prompts_data.jsonl")
    output_file = os.path.join(output_dir, "motion_prompts_list.txt")
    log_file = os.path.join(LOGS_DIR, f"{LOG_PREFIX_ERROR}{project_name}_phase1_3_motion.txt")
    
    logger = DualLogger(log_file)
    _logger = logger
    error_occurred = False
    
    # コストトラッカー
    tracker = CostTracker(project_name)
    _tracker = tracker
    
    # Anthropic API クライアント初期化
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.log("🚨 エラー: 環境変数 'ANTHROPIC_API_KEY' が設定されていません。")
            logger.save_on_error()
            sys.exit(1)
        
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
        logger.log(f"--- Phase 1.3 (Motion Prompts): '{project_name}' ---")
        logger.log(f"{'='*60}")
        
        # 画像プロンプトを読み込み
        image_prompts = load_image_prompts(input_file, logger)
        
        if not image_prompts:
            logger.log("🚨 画像プロンプトが見つかりません。Phase 1.2 を先に実行してください。")
            error_occurred = True
        else:
            # テストモード制限
            if TEST_MODE_LIMIT > 0 and len(image_prompts) > TEST_MODE_LIMIT:
                logger.log(f"⚠️ テストモード: {TEST_MODE_LIMIT}個のみ生成")
                image_prompts = image_prompts[:TEST_MODE_LIMIT]
            
            _total_count = len(image_prompts)
            
            # チェックポイント確認
            completed = check_existing_motion_prompts(output_file, logger)
            
            if completed > 0:
                logger.log(f"\n🔄 チェックポイント: {completed}/{_total_count} 完了済み")
                logger.log(f"▶️  続きから再開します")
            
            # モーションルール読み込み
            motion_rules_file = os.path.join(BASE_DIR, "rule", "video_motion_rules.txt")
            motion_rules = read_file_safely(motion_rules_file, "モーションルール")
            
            if not motion_rules:
                logger.log("🚨 video_motion_rules.txt が見つかりません。")
                error_occurred = True
            else:
                logger.log(f"📋 モーションルール: {len(motion_rules)} 文字")
                
                # モーションプロンプト生成
                if generate_motion_prompts(
                    client, image_prompts, motion_rules,
                    output_file, logger, completed_count=completed, tracker=tracker
                ):
                    logger.log(f"\n✅ モーションプロンプトを保存しました: {output_file}")
                    logger.log("--- Phase 1.3 (Motion Prompts) が正常に完了しました ---")
                else:
                    logger.log("🚨 一部のモーションプロンプト生成に失敗しましたが、処理を続行します。")
    
    except Exception as e:
        logger.log(f"\n🚨🚨🚨 Phase 1.3 で予期せぬエラーが発生しました 🚨🚨🚨")
        logger.log(traceback.format_exc())
        error_occurred = True
    
    if error_occurred:
        logger.save_on_error()
        sys.exit(1)


if __name__ == "__main__":
    main()
