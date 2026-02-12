"""
ロガーユーティリティ（Windows絵文字対応版）
ログをコンソールとファイルに出力し、エラー時のみファイルを保存
"""
import os
import sys
import unicodedata


class DualLogger:
    """
    コンソールとメモリにログを記録し、エラー時のみファイルに保存するロガー
    Windows環境で絵文字が使えない問題に対応
    """
    
    def __init__(self, log_file_path):
        """
        Args:
            log_file_path (str): エラー時に保存するログファイルのパス
        """
        self.log_file_path = log_file_path
        self.log_buffer = []
    
    @staticmethod
    def remove_emojis(text):
        """
        絵文字を除去してWindows互換の文字列にする
        
        Args:
            text (str): 元の文字列
        
        Returns:
            str: 絵文字を除去した文字列
        """
        # 絵文字マッピング（よく使う絵文字のみ）
        emoji_map = {
            '📁': '[Folder]',
            '☁️': '[Cloud]',
            '✅': '[OK]',
            '🔄': '[Loading]',
            '⚠️': '[Warning]',
            '🚨': '[Error]',
            '💰': '[Cost]',
            '📊': '[Chart]',
            '🔥': '[Fire]',
            '🎯': '[Target]',
            '📋': '[List]',
            '🎉': '[Party]',
            '🚀': '[Rocket]',
            '💡': '[Idea]',
            '⏱️': '[Timer]',
            '▶️': '[Play]',
            '⏭️': '[Skip]',
            '🔧': '[Tool]',
            '📝': '[Memo]',
            '💪': '[Muscle]',
            '👍': '[ThumbsUp]',
            '❌': '[X]',
            '🆕': '[New]',
            '📦': '[Package]',
            '🔍': '[Search]',
            '⏳': '[Hourglass]',
            '📥': '[Download]',
            '📤': '[Upload]',
        }
        
        # マッピングに基づいて置換
        result = text
        for emoji, replacement in emoji_map.items():
            result = result.replace(emoji, replacement)
        
        # それ以外の絵文字を除去
        # Unicode の Emoji カテゴリをチェック
        result = ''.join(
            char for char in result
            if not (
                '\U0001F300' <= char <= '\U0001F9FF' or  # 絵文字
                '\U0001FA00' <= char <= '\U0001FAFF' or  # 拡張絵文字
                '\U00002600' <= char <= '\U000027BF' or  # 記号
                '\U0001F000' <= char <= '\U0001F2FF'     # 追加記号
            )
        )
        
        return result
    
    def log(self, message):
        """
        ログメッセージをコンソールとメモリに記録（Windows対応・リアルタイム表示）
        
        Args:
            message (str): ログメッセージ
        """
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        
        # メモリに保存（元のメッセージ）
        self.log_buffer.append(formatted_message)
        
        # コンソール出力用に絵文字を除去
        safe_message = self.remove_emojis(formatted_message)
        
        try:
            # まず通常出力を試みる
            print(formatted_message, flush=True)  # flush=True でバッファをクリア
        except UnicodeEncodeError:
            # 失敗したら絵文字を除去して出力
            try:
                print(safe_message, flush=True)
            except Exception:
                # それでもダメなら ASCII のみ
                ascii_message = safe_message.encode('ascii', 'replace').decode('ascii')
                print(ascii_message, flush=True)
    
    def save_on_error(self):
        """
        エラー時にログをファイルに保存（絵文字を除去して保存）
        """
        try:
            os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
            
            # ファイル保存時も絵文字を除去
            with open(self.log_file_path, "w", encoding="utf-8") as f:
                for line in self.log_buffer:
                    safe_line = self.remove_emojis(line)
                    f.write(safe_line + "\n")
            
            safe_path = self.remove_emojis(self.log_file_path)
            print(f"\n[Error] ログをファイルに保存しました: {safe_path}")
        except Exception as e:
            print(f"\n[Error] ログファイルの保存に失敗: {e}")