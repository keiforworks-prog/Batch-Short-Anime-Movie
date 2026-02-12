"""
XMLパース用ユーティリティ（リトライ機能付き）
Claudeのレスポンスから画像プロンプトを確実に抽出
"""
import xml.etree.ElementTree as ET
import re


def parse_xml_response(response_text, logger):
    """
    Claude APIのXMLレスポンスからプロンプトを抽出（リトライ対応）
    
    Args:
        response_text (str): Claude APIのレスポンステキスト
        logger: ロガーインスタンス
    
    Returns:
        str: 抽出されたプロンプト、失敗時は None
    """
    # まず正規表現で抽出を試みる（壊れたXMLにも対応）
    prompt = extract_prompt_with_regex(response_text, logger)
    
    if prompt:
        # プロンプトの品質チェック
        if len(prompt) < 100:
            logger.log(f"⚠️ プロンプトが短すぎます（{len(prompt)}文字）- リトライ推奨")
            return None
        
        return prompt
    
    # 正規表現でも抽出できない場合
    logger.log("⚠️ プロンプトの抽出に完全に失敗しました")
    return None


def extract_prompt_with_regex(text, logger):
    """
    正規表現で <image_prompt> タグの内容を抽出
    
    Args:
        text (str): Claude APIのレスポンステキスト
        logger: ロガーインスタンス
    
    Returns:
        str: 抽出されたプロンプト、失敗時は None
    """
    # パターン1: 完全なXMLタグ（通常ケース）
    match = re.search(r'<image_prompt>\s*(.*?)\s*</image_prompt>', text, re.DOTALL)
    if match:
        prompt = match.group(1).strip()
        logger.log(f"✅ プロンプト抽出成功（{len(prompt)}文字、完全なXML）")
        return prompt
    
    # パターン2: 閉じタグが無い（途中で切れた）
    match = re.search(r'<image_prompt>\s*(.*?)(?=\n\n|$)', text, re.DOTALL)
    if match:
        prompt = match.group(1).strip()
        logger.log(f"⚠️ プロンプト抽出成功（{len(prompt)}文字、途中で切断）")
        return prompt
    
    # パターン3: タグ無し（生テキスト）
    # output タグだけある場合
    match = re.search(r'<output>\s*(.*?)\s*(?:</output>|$)', text, re.DOTALL)
    if match:
        content = match.group(1).strip()
        # image_prompt タグを除去
        content = re.sub(r'</?image_prompt>', '', content).strip()
        if content:
            logger.log(f"⚠️ プロンプト抽出成功（{len(content)}文字、outputタグのみ）")
            return content
    
    return None


def validate_prompt_quality(prompt, min_length=100, max_length=2000):
    """
    プロンプトの品質をチェック
    
    Args:
        prompt (str): 検証するプロンプト
        min_length (int): 最小文字数
        max_length (int): 最大文字数
    
    Returns:
        tuple: (is_valid, reason)
    """
    if not prompt:
        return False, "プロンプトが空です"
    
    prompt_length = len(prompt)
    
    if prompt_length < min_length:
        return False, f"プロンプトが短すぎます（{prompt_length}文字 < {min_length}文字）"
    
    if prompt_length > max_length:
        return False, f"プロンプトが長すぎます（{prompt_length}文字 > {max_length}文字）"
    
    # 基本的なキーワードチェック（画像プロンプトとして妥当か）
    keywords = ['shot', 'angle', 'style', 'scene', 'character', 'background']
    has_keywords = any(keyword in prompt.lower() for keyword in keywords)
    
    if not has_keywords:
        return False, "画像プロンプトとして不適切な内容です"
    
    return True, "OK"