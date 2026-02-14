#!/usr/bin/env python3
"""
AI画像生成パイプライン - メインスクリプト (Docker & Windows対応)
scripts/input/*.txt を順次処理し、Claude → GPT → Google Drive の全フローを実行
"""
import os
import sys
import json
import time
import subprocess
import traceback
from config import BATCH_API_ENABLED
from pathlib import Path
from gcs_utils import list_gcs_scripts, download_gcs_script

# --- 設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(BASE_DIR, "input")
CONTACT_NOTE_FILE = os.path.join(BASE_DIR, "_current_project.json")
MODEL_NAME = "claude"  # 出力フォルダ名に使用

# 各フェーズのスクリプトパス
PHASE1_1_SCRIPT = os.path.join(BASE_DIR, "p1_claude_generate_settings.py")
PHASE1_2_SCRIPT = os.path.join(BASE_DIR, "p1_claude_generate_prompts.py")
PHASE1_3_SCRIPT = os.path.join(BASE_DIR, "p1_3_claude_generate_motion.py")
PHASE2_SCRIPT = os.path.join(BASE_DIR, "p2_gpt_generate_images.py")
PHASE2_5_SCRIPT = os.path.join(BASE_DIR, "p2_5_hailuo_generate_videos.py")
PHASE3_SCRIPT = os.path.join(BASE_DIR, "p3_gdrive_upload.py")


def find_script_files(input_dir):
    """
    GCS または ローカルから .txt ファイルを取得
    
    Returns:
        tuple: (script_files, is_gcs)
    """
    # GCS を優先
    try:
        gcs_files = list_gcs_scripts()
        if gcs_files:
            print(f"📦 GCS から {len(gcs_files)} 個の台本を検出")
            return gcs_files, True
    except Exception as e:
        print(f"⚠️ GCS アクセスエラー（ローカルを使用）: {e}")
        traceback.print_exc()
    
    # ローカルフォールバック
    if not os.path.exists(input_dir):
        print(f"⚠️ ローカル INPUT_DIR が存在しません: {input_dir}")
        return [], False
    
    txt_files = [
        f for f in os.listdir(input_dir) 
        if f.endswith('.txt') and os.path.isfile(os.path.join(input_dir, f))
    ]
    
    print(f"📁 ローカルから {len(txt_files)} 個の台本を検出")
    return sorted(txt_files), False


def select_script_interactive(script_files, is_gcs):
    """
    対話式でスクリプトファイルを選択
    
    Args:
        script_files (list): ファイル名のリスト
        is_gcs (bool): GCS から取得したか
    
    Returns:
        tuple: (selected_filename, is_gcs)
    """
    print("\n" + "="*50)
    print(f"📄 利用可能な台本 {'(GCS)' if is_gcs else '(ローカル)'}:")
    print("="*50)
    
    for i, filename in enumerate(script_files, 1):
        print(f"  [{i}] {filename}")
    
    print("\n  [0] キャンセル")
    print("="*50)
    
    while True:
        try:
            choice = input("\n処理する台本の番号を入力: ").strip()
            choice_num = int(choice)
            
            if choice_num == 0:
                return None, is_gcs
            
            if 1 <= choice_num <= len(script_files):
                selected = script_files[choice_num - 1]
                print(f"\n✅ 選択: {selected}")
                return selected, is_gcs
            else:
                print(f"⚠️ 1〜{len(script_files)} の範囲で入力してください。")
        except (ValueError, KeyboardInterrupt):
            return None, is_gcs


def write_contact_note(project_name, model_name, script_path, start_time):
    """
    連絡ノート (_current_project.json) を作成
    
    Args:
        project_name (str): プロジェクト名（拡張子なしのファイル名）
        model_name (str): モデル名
        script_path (str): 台本ファイルの絶対パス
        start_time (float): 処理開始時刻（time.time()）
    
    Returns:
        bool: 成功時 True
    """
    try:
        data = {
            "project_name": project_name,
            "model_name": model_name,
            "script_full_path": script_path,
            "start_time": start_time
        }
        
        with open(CONTACT_NOTE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"📝 連絡ノートを作成: {CONTACT_NOTE_FILE}")
        return True
    
    except Exception as e:
        print(f"🚨 連絡ノート作成エラー: {e}")
        traceback.print_exc()
        return False


def run_phase_script(script_path, phase_name):
    """
    各フェーズのスクリプトを実行（Windows文字コード対応版）
    
    Args:
        script_path (str): 実行するスクリプトのパス
        phase_name (str): フェーズ名（ログ用）
    
    Returns:
        bool: 成功時 True、失敗時 False
    """
    if not os.path.exists(script_path):
        print(f"🚨 エラー: スクリプトが見つかりません: {script_path}")
        return False
    
    # Phase ごとのタイムアウトを取得
    from config import PHASE_TIMEOUTS
    timeout = PHASE_TIMEOUTS.get(phase_name, 1800)
    
    print(f"\n{'='*50}")
    print(f"▶️ {phase_name} を実行中... (タイムアウト: {timeout}秒)")
    print(f"{'='*50}")
    
    try:
        # 🔥 Windowsの文字コード問題に対応
        # encoding='cp932' (Shift-JIS) を指定し、エラーを無視
        import locale
        system_encoding = locale.getpreferredencoding() or 'utf-8'
        
        process = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=system_encoding,  # システムのデフォルトエンコーディング
            errors='replace',  # デコードエラーを '?' に置き換え
            bufsize=1,
            universal_newlines=True
        )
        
        # 標準出力をリアルタイムで表示
        stdout_lines = []
        stderr_lines = []
        
        # 標準出力のみリアルタイム表示
        while True:
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
            if output:
                print(output, end='')
                stdout_lines.append(output)
        
        # プロセス終了を待つ
        return_code = process.wait(timeout=timeout)
        
        # 標準エラーを取得（エラー無視）
        try:
            stderr = process.stderr.read()
            if stderr:
                stderr_lines.append(stderr)
        except UnicodeDecodeError:
            # デコードエラーは無視
            stderr = "[標準エラーの文字コード変換に失敗しました]"
            stderr_lines.append(stderr)
        
        if return_code == 0:
            print(f"\n✅ {phase_name} が正常に完了しました。")
            return True
        else:
            print(f"\n🚨 {phase_name} でエラーが発生しました (終了コード: {return_code})")
            if stderr_lines:
                print("📋 標準エラー:")
                for line in stderr_lines:
                    print(line)
            return False
    
    except subprocess.TimeoutExpired:
        process.kill()
        print(f"\n🚨 {phase_name} がタイムアウトしました ({timeout}秒)")
        return False
    
    except Exception as e:
        print(f"\n🚨 {phase_name} で予期せぬエラーが発生: {e}")
        traceback.print_exc()
        return False


def process_single_project(script_filename, is_gcs):
    """
    1つのプロジェクトを全フェーズ実行
    
    Args:
        script_filename (str): 台本ファイル名
        is_gcs (bool): GCS から取得したか
    """
    project_name = os.path.splitext(script_filename)[0]
    
    # 処理開始時刻を記録
    start_time = time.time()
    
    print("\n" + "="*50)
    print(f"🚀 プロジェクト '{project_name}' の処理を開始")
    print("="*50)
    
    # GCS からダウンロード
    if is_gcs:
        local_input_dir = os.path.join(BASE_DIR, "input")
        script_path = os.path.join(local_input_dir, script_filename)
        
        print(f"📥 GCS からダウンロード: {script_filename}")
        if not download_gcs_script(script_filename, script_path):
            print(f"🚨 GCS ダウンロードに失敗: {script_filename}")
            return False
    else:
        script_path = os.path.join(INPUT_DIR, script_filename)
    
    # 連絡ノートを作成（開始時刻を含む）
    if not write_contact_note(project_name, MODEL_NAME, script_path, start_time):
        return False
    
    # Phase 1.1: キャラクター設定生成
    if not run_phase_script(PHASE1_1_SCRIPT, "Phase 1.1 (Character Settings)"):
        return False
    
    # Phase 1.2: プロンプト生成
    if BATCH_API_ENABLED:
        # バッチAPI モード
        print("\n🔄 バッチAPIモードで実行します")
        
        # バッチ送信
        batch_submit_script = os.path.join(BASE_DIR, "p1_claude_batch_submit.py")
        if not run_phase_script(batch_submit_script, "Phase 1.2-A (Batch Submit)"):
            return False
        
        # バッチ取得
        batch_retrieve_script = os.path.join(BASE_DIR, "p1_claude_batch_retrieve.py")
        if not run_phase_script(batch_retrieve_script, "Phase 1.2-B (Batch Retrieve)"):
            return False
    else:
        # リアルタイムAPI モード
        print("\n⚡ リアルタイムAPIモードで実行します")
        if not run_phase_script(PHASE1_2_SCRIPT, "Phase 1.2 (Claude Prompts)"):
            return False
    
    # Phase 2: 画像生成
    if BATCH_API_ENABLED:
        # バッチAPI モード
        print("\n🔄 バッチAPIモードで実行します（画像生成）")
        
        # バッチ送信
        gpt_batch_submit_script = os.path.join(BASE_DIR, "p2_gpt_batch_submit.py")
        if not run_phase_script(gpt_batch_submit_script, "Phase 2-A (GPT Batch Submit)"):
            return False
        
        # バッチ取得
        gpt_batch_retrieve_script = os.path.join(BASE_DIR, "p2_gpt_batch_retrieve.py")
        if not run_phase_script(gpt_batch_retrieve_script, "Phase 2-B (GPT Batch Retrieve)"):
            return False
    else:
        # リアルタイムAPI モード
        print("\n⚡ リアルタイムAPIモードで実行します（画像生成）")
        if not run_phase_script(PHASE2_SCRIPT, "Phase 2 (GPT Images)"):
            return False
    
    # Phase 1.3: モーションプロンプト生成
    if not run_phase_script(PHASE1_3_SCRIPT, "Phase 1.3 (Motion Prompts)"):
        return False
    
    # Phase 2.5: 動画生成 (Hailuo)
    phase2_5_success = run_phase_script(PHASE2_5_SCRIPT, "Phase 2.5 (Video Generation)")
    if not phase2_5_success:
        print("⚠️ Phase 2.5 に失敗しましたが、アップロードは続行します")
    
    # Phase 3: Google Drive アップロード（常に実行）
    if not run_phase_script(PHASE3_SCRIPT, "Phase 3 (Google Drive Upload)"):
        return False
    
    # 処理終了時刻を記録
    end_time = time.time()
    duration = end_time - start_time
    
    print("\n" + "="*50)
    print(f"✅ プロジェクト '{project_name}' の全処理が完了しました!")
    print(f"⏱️  実行時間: {duration/60:.1f}分")
    print("="*50)
    
    return True


def process_normal_mode():
    """
    Normal モード: ユーザーが1ファイルを選択して処理
    """
    print("\n🔍 Normal モード: 台本ファイルを選択してください")
    
    script_files, is_gcs = find_script_files(INPUT_DIR)
    
    if not script_files:
        print("🚨 エラー: 処理可能な .txt ファイルが見つかりません。")
        return False
    
    selected_file, is_gcs = select_script_interactive(script_files, is_gcs)
    
    if not selected_file:
        return False
    
    return process_single_project(selected_file, is_gcs)


def process_batch_mode():
    """
    Batch モード: input フォルダ内の全 .txt ファイルを自動処理
    """
    print("\n🔄 Batch モード: 全台本ファイルを自動処理します")
    
    # デバッグ情報
    print(f"\n📂 環境情報:")
    print(f"  BASE_DIR: {BASE_DIR}")
    print(f"  INPUT_DIR: {INPUT_DIR}")
    print(f"  IS_CLOUD_RUN: {os.getenv('K_SERVICE') is not None}")
    print(f"  GCS_BUCKET: {os.getenv('GCS_BUCKET_NAME', 'Not Set')}")
    
    try:
        script_files, is_gcs = find_script_files(INPUT_DIR)
    except Exception as e:
        print(f"🚨 find_script_files() でエラー: {e}")
        traceback.print_exc()
        return False
    
    if not script_files:
        print("🚨 エラー: 処理可能な .txt ファイルが見つかりません。")
        print(f"   - GCS チェック済み: はい")
        print(f"   - ローカル INPUT_DIR 存在: {os.path.exists(INPUT_DIR)}")
        return False
    
    print(f"\n📋 {len(script_files)} 件の台本が見つかりました:")
    for i, filename in enumerate(script_files, 1):
        print(f"  {i}. {filename}")
    
    print("\n" + "="*50)
    print("⏳ 5秒後に自動的に処理を開始します...")
    print("="*50)
    
    # Docker環境では自動開始（対話式入力ができないため）
    time.sleep(5)
    
    success_count = 0
    failed_projects = []
    
    for i, script_filename in enumerate(script_files, 1):
        project_name = os.path.splitext(script_filename)[0]
        
        print(f"\n\n{'#'*60}")
        print(f"# [{i}/{len(script_files)}] {project_name}")
        print(f"{'#'*60}")
        
        if process_single_project(script_filename, is_gcs):
            success_count += 1
        else:
            failed_projects.append(project_name)
            print(f"\n⚠️ '{project_name}' の処理に失敗しました。次のプロジェクトに進みます...")
    
    # 最終結果サマリー
    print("\n\n" + "="*60)
    print("📊 バッチ処理完了サマリー")
    print("="*60)
    print(f"✅ 成功: {success_count} / {len(script_files)} 件")
    
    if failed_projects:
        print(f"\n❌ 失敗したプロジェクト:")
        for name in failed_projects:
            print(f"  - {name}")
    
    print("="*60)
    
    return len(failed_projects) == 0


def main():
    """
    メインエントリーポイント
    """
    print("\n" + "="*60)
    print("🎨 AI Image Generation Pipeline")
    print(f"📋 起動引数: {sys.argv}")
    print("="*60)
    
    # コマンドライン引数でモード判定
    if len(sys.argv) < 2:
        print("🚨 エラー: 実行モードを指定してください。")
        print("\n使い方:")
        print("  python main_pipeline.py normal  # 1ファイル選択モード")
        print("  python main_pipeline.py batch   # 全ファイル自動処理")
        sys.exit(1)
    
    mode = sys.argv[1].lower()
    
    try:
        if mode == "normal":
            success = process_normal_mode()
        elif mode == "batch":
            success = process_batch_mode()
        else:
            print(f"🚨 エラー: 不正なモード '{mode}'")
            print("   'normal' または 'batch' を指定してください。")
            sys.exit(1)
    except Exception as e:
        print(f"\n🚨🚨🚨 予期せぬエラーが発生しました 🚨🚨🚨")
        print(f"エラー: {e}")
        traceback.print_exc()
        sys.exit(1)
    
    # 終了コード
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()