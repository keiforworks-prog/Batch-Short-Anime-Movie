"""
プロジェクト情報の読み書きを管理する共通ユーティリティ
"""
import json
import os
from config import CONTACT_NOTE_FILE, PROJECT_ROOT

def read_project_info():
    """
    連絡ノート(_current_project.json)からプロジェクト情報を読み込む
    
    Returns:
        tuple: (project_name, model_name, script_full_path)
               読み込み失敗時は (None, None, None)
    """
    try:
        with open(CONTACT_NOTE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        project_name = data.get("project_name")
        model_name = data.get("model_name")
        script_full_path = data.get("script_full_path")
        
        return project_name, model_name, script_full_path
        
    except FileNotFoundError:
        print(f"🚨 エラー: 連絡ノートが見つかりません: {CONTACT_NOTE_FILE}")
        return None, None, None
    except (KeyError, json.JSONDecodeError) as e:
        print(f"🚨 エラー: 連絡ノートの形式が正しくありません: {e}")
        return None, None, None
    except Exception as e:
        print(f"🚨 エラー: 連絡ノートの読み込み中に予期せぬ問題が発生しました: {e}")
        return None, None, None

def get_output_dir(project_name, model_name):
    """
    プロジェクトの出力ディレクトリパスを取得
    
    Args:
        project_name (str): プロジェクト名
        model_name (str): モデル名（例: 'claude'）
    
    Returns:
        str: 出力ディレクトリの絶対パス
    """
    return os.path.join(PROJECT_ROOT, "output", model_name, project_name)

def ensure_output_dir(project_name, model_name):
    """
    出力ディレクトリが存在しない場合は作成
    
    Args:
        project_name (str): プロジェクト名
        model_name (str): モデル名
    
    Returns:
        str: 出力ディレクトリの絶対パス
    """
    output_dir = get_output_dir(project_name, model_name)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir

def get_image_output_dir(project_name, model_name):
    """
    画像出力ディレクトリパスを取得
    
    Args:
        project_name (str): プロジェクト名
        model_name (str): モデル名
    
    Returns:
        str: 画像出力ディレクトリの絶対パス
    """
    return os.path.join(get_output_dir(project_name, model_name), "images")

def ensure_image_output_dir(project_name, model_name):
    """
    画像出力ディレクトリが存在しない場合は作成
    
    Args:
        project_name (str): プロジェクト名
        model_name (str): モデル名
    
    Returns:
        str: 画像出力ディレクトリの絶対パス
    """
    image_dir = get_image_output_dir(project_name, model_name)
    os.makedirs(image_dir, exist_ok=True)
    return image_dir

def read_file_safely(file_path, file_description="ファイル"):
    """
    ファイルを安全に読み込む（エラーハンドリング付き）
    
    Args:
        file_path (str): ファイルパス
        file_description (str): エラーメッセージ用のファイル説明
    
    Returns:
        str: ファイル内容（失敗時はNone）
    """
    try:
        if not os.path.exists(file_path):
            print(f"🚨 エラー: {file_description}が見つかりません: {file_path}")
            return None
        
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
            
    except Exception as e:
        print(f"🚨 エラー: {file_description}の読み込みに失敗: {e}")
        return None

def write_file_safely(file_path, content, file_description="ファイル"):
    """
    ファイルに安全に書き込む（エラーハンドリング付き）
    
    Args:
        file_path (str): ファイルパス
        content (str): 書き込む内容
        file_description (str): エラーメッセージ用のファイル説明
    
    Returns:
        bool: 成功時True、失敗時False
    """
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"🚨 エラー: {file_description}の書き込みに失敗: {e}")
        return False
        
def get_current_project_info():
    """
    現在のプロジェクト情報を取得（バッチAPI用）
    
    Returns:
        tuple: (project_name, project_folder)
    
    Raises:
        Exception: プロジェクト情報の取得に失敗した場合
    """
    project_name, model_name, script_path = read_project_info()
    
    if not project_name or not model_name:
        raise Exception("プロジェクト情報の読み込みに失敗しました")
    
    project_folder = get_output_dir(project_name, model_name)
    
    if not os.path.exists(project_folder):
        raise Exception(f"プロジェクトフォルダが見つかりません: {project_folder}")
    
    return project_name, project_folder