"""
API呼び出しのリトライ機能を提供するユーティリティ
"""
import time
from typing import Callable, Any, Optional

# OpenAI のエラー型をインポート（存在する場合のみ）
try:
    from openai import BadRequestError, RateLimitError, APIConnectionError, AuthenticationError
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Anthropic のエラー型をインポート（存在する場合のみ）
try:
    from anthropic import BadRequestError as AnthropicBadRequestError, RateLimitError as AnthropicRateLimitError
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


def call_api_with_retry(
    api_call: Callable,
    max_retries: int = 3,
    base_delay: int = 5,
    logger: Optional[Any] = None,
    operation_name: str = "API呼び出し"
) -> Any:
    """
    API呼び出しを自動リトライする
    
    Args:
        api_call: 実行する関数（引数なしのlambda推奨）
        max_retries: 最大リトライ回数（デフォルト: 3回）
        base_delay: 基本待機時間（秒）。リトライごとに増加
        logger: ロガーインスタンス（オプション）
        operation_name: 操作名（ログ用）
    
    Returns:
        API呼び出しの結果
    
    Raises:
        Exception: 最大リトライ回数を超えた場合
    
    Example:
        >>> response = call_api_with_retry(
        ...     lambda: client.messages.create(model="claude-sonnet-4-5", ...),
        ...     logger=logger
        ... )
    """
    last_exception = None
    
    for retry in range(max_retries):
        try:
            # API呼び出しを実行
            result = api_call()
            
            # 成功した場合、リトライ情報をログ出力
            if retry > 0 and logger:
                logger.log(f"✅ {operation_name}が成功しました（リトライ {retry}回目で成功）")
            
            return result
            
        except Exception as e:
            last_exception = e
            
            # モデレーションエラーはリトライしない
            if "content_policy" in str(e).lower() or "moderation" in str(e).lower():
                if logger:
                    logger.log(f"🚫 モデレーションエラー: {operation_name}をスキップします")
                raise
            
            # クレジット不足エラーもリトライしない
            if "credit" in str(e).lower() or "billing" in str(e).lower():
                if logger:
                    logger.log(f"💳 クレジット不足エラー: {operation_name}を中断します")
                raise
            
            # 最後のリトライでなければ待機して再試行
            if retry < max_retries - 1:
                wait_time = base_delay * (retry + 1)  # 5秒、10秒、15秒...
                
                if logger:
                    logger.log(f"⚠️ {operation_name}でエラー: {e}")
                    logger.log(f"🔄 {wait_time}秒後にリトライします（{retry + 1}/{max_retries}回目）")
                else:
                    print(f"⚠️ リトライ {retry + 1}/{max_retries}: {e}")
                
                time.sleep(wait_time)
            else:
                # 最後のリトライも失敗
                if logger:
                    logger.log(f"🚨 {operation_name}が{max_retries}回のリトライ後も失敗しました")
                    logger.log(f"最終エラー: {e}")
                raise
    
    # ここには到達しないはずだが、念のため
    if last_exception:
        raise last_exception


def is_retryable_error(error: Exception) -> bool:
    """
    リトライ可能なエラーかどうかを判定
    
    Args:
        error: 発生した例外
    
    Returns:
        bool: リトライ可能な場合True
    """
    error_str = str(error).lower()
    
    # リトライ不可能なエラー
    non_retryable = [
        "content_policy",
        "moderation",
        "credit",
        "billing",
        "invalid_api_key",
        "authentication"
    ]
    
    for keyword in non_retryable:
        if keyword in error_str:
            return False
    
    # リトライ可能なエラー
    retryable = [
        "timeout",
        "connection",
        "network",
        "503",  # Service Unavailable
        "502",  # Bad Gateway
        "500",  # Internal Server Error
        "429"   # Rate Limit
    ]
    
    for keyword in retryable:
        if keyword in error_str:
            return True
    
    # デフォルトはリトライする
    return True