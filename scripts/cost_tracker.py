"""
コスト計算モジュール（修正版）
Discord通知用のコストサマリーを生成
Mini版（$0.052）と通常版（$0.25）の混在計算に対応
"""

from datetime import datetime


class CostTracker:
    """各PhaseのAPIコストを追跡・計算するクラス"""
    
    # 料金設定（2025年10月時点）
    CLAUDE_PRICES = {
        "input": 3.00 / 1_000_000,           # $3.00 per 1M tokens
        "cache_creation": 3.75 / 1_000_000,  # $3.75 per 1M tokens
        "cache_read": 0.30 / 1_000_000,      # $0.30 per 1M tokens
        "output": 15.00 / 1_000_000          # $15.00 per 1M tokens
    }
    
    # 🆕 画像生成の価格（2種類）
    GPT_IMAGE_PRICES = {
        "mini": 0.052,    # GPT Image 1 Mini（標準品質）
        "standard": 0.25  # GPT Image 1（高品質）
    }
    
    # 🆕 Cloud Run 料金設定
    CLOUD_RUN_PRICES = {
        "cpu": 0.000024,      # $0.000024 per vCPU-second
        "memory": 0.0000025   # $0.0000025 per GiB-second
    }
    
    USD_TO_JPY = 150        # 為替レート
    
    def __init__(self, project_name):
        """
        Args:
            project_name: プロジェクト名
        """
        self.project_name = project_name
        self.timestamp = datetime.now()
        
        # Phase別コスト
        self.phase_1_1_cost = 0.0
        self.phase_1_2_cost = 0.0
        self.phase_2_cost = 0.0
        self.cloud_run_cost = 0.0  # 🆕 Cloud Run コスト
        
        # Phase 1.2 詳細
        self.cache_creation_tokens = 0
        self.cache_read_tokens = 0
        self.input_tokens = 0
        self.output_tokens = 0
        
        # Phase 2 詳細
        self.images_generated = 0
        self.images_failed = 0
        
        # 🆕 Phase 2 詳細（モデル別）
        self.images_high_quality = 0  # 高品質版の枚数
        self.images_mini = 0          # Mini版の枚数
        
        # 🆕 Cloud Run 詳細
        self.cloud_run_duration = 0  # 実行時間（秒）
    
    def add_phase_1_1(self, api_calls=1):
        """
        Phase 1.1（キャラクター設定生成）のコストを記録
        
        Args:
            api_calls: API呼び出し回数（通常1回）
        """
        # Phase 1.1は固定コストで概算（実際のトークン数は取得しない）
        estimated_cost = 0.02 * api_calls
        self.phase_1_1_cost = estimated_cost
    
    def add_phase_1_2(self, cache_creation, cache_read, input_tokens, output_tokens):
        """
        Phase 1.2（プロンプト生成）のコストを記録
        
        Args:
            cache_creation: キャッシュ作成トークン数
            cache_read: キャッシュ読込トークン数
            input_tokens: 通常入力トークン数
            output_tokens: 出力トークン数
        """
        self.cache_creation_tokens = cache_creation
        self.cache_read_tokens = cache_read
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        
        # コスト計算
        cost = 0.0
        cost += cache_creation * self.CLAUDE_PRICES["cache_creation"]
        cost += cache_read * self.CLAUDE_PRICES["cache_read"]
        cost += input_tokens * self.CLAUDE_PRICES["input"]
        cost += output_tokens * self.CLAUDE_PRICES["output"]
        
        self.phase_1_2_cost = cost
    
    def add_phase_2(self, images_generated, images_failed, images_high_quality=0, images_mini=0):
        """
        🆕 Phase 2（画像生成）のコストを記録（モデル別対応）
        
        Args:
            images_generated: 生成成功した画像枚数（総数）
            images_failed: 生成失敗した画像枚数
            images_high_quality: 高品質版（gpt-image-1）の枚数（オプション）
            images_mini: Mini版（gpt-image-1-mini）の枚数（オプション）
        """
        self.images_generated = images_generated
        self.images_failed = images_failed
        self.images_high_quality = images_high_quality
        self.images_mini = images_mini
        
        # モデル別枚数が指定されていない場合は、全てMiniとして計算（後方互換性）
        if images_high_quality == 0 and images_mini == 0:
            self.images_mini = images_generated
            self.phase_2_cost = images_generated * self.GPT_IMAGE_PRICES["mini"]
        else:
            # モデル別でコスト計算
            cost_high = images_high_quality * self.GPT_IMAGE_PRICES["standard"]
            cost_mini = images_mini * self.GPT_IMAGE_PRICES["mini"]
            self.phase_2_cost = cost_high + cost_mini
    
    def add_cloud_run_cost(self, duration_seconds, cpu=4, memory_gib=8):
        """
        🆕 Cloud Run のコストを概算
        
        Args:
            duration_seconds: 実行時間（秒）
            cpu: vCPU数（デフォルト: 4）
            memory_gib: メモリ容量（GiB）（デフォルト: 8）
        """
        self.cloud_run_duration = duration_seconds
        
        # コスト計算
        cpu_cost = duration_seconds * cpu * self.CLOUD_RUN_PRICES["cpu"]
        memory_cost = duration_seconds * memory_gib * self.CLOUD_RUN_PRICES["memory"]
        
        self.cloud_run_cost = cpu_cost + memory_cost
    
    def get_total_cost_usd(self):
        """総コスト（USD）を取得"""
        return self.phase_1_1_cost + self.phase_1_2_cost + self.phase_2_cost + self.cloud_run_cost
    
    def get_total_cost_jpy(self):
        """総コスト（JPY）を取得"""
        return round(self.get_total_cost_usd() * self.USD_TO_JPY)
    
    def get_phase_1_cost_usd(self):
        """Phase 1合計コスト（USD）を取得"""
        return self.phase_1_1_cost + self.phase_1_2_cost
    
    def get_summary_for_discord(self):
        """
        Discord通知用のサマリーデータを生成
        
        Returns:
            dict: Discord通知用データ
        """
        total_usd = self.get_total_cost_usd()
        total_jpy = self.get_total_cost_jpy()
        phase_1_usd = self.get_phase_1_cost_usd()
        phase_2_usd = self.phase_2_cost
        cloud_run_usd = self.cloud_run_cost
        
        return {
            "total_usd": round(total_usd, 2),
            "total_jpy": total_jpy,
            "phase_1_usd": round(phase_1_usd, 2),
            "phase_2_usd": round(phase_2_usd, 2),
            "cloud_run_usd": round(cloud_run_usd, 2),  # 🆕
            "cloud_run_duration": round(self.cloud_run_duration / 60, 1),  # 🆕 分単位
            "images_generated": self.images_generated,
            "images_failed": self.images_failed,
            "images_high_quality": self.images_high_quality,  # 🆕
            "images_mini": self.images_mini,  # 🆕
            "prompts_count": self.images_generated + self.images_failed
        }
    
    def get_detailed_summary(self):
        """
        詳細サマリー（ログ出力用）
        
        Returns:
            str: 詳細サマリーテキスト
        """
        total_usd = self.get_total_cost_usd()
        total_jpy = self.get_total_cost_jpy()
        
        # 🆕 Phase 2 の詳細（モデル別）
        phase_2_breakdown = ""
        if self.images_high_quality > 0 or self.images_mini > 0:
            cost_high = self.images_high_quality * self.GPT_IMAGE_PRICES["standard"]
            cost_mini = self.images_mini * self.GPT_IMAGE_PRICES["mini"]
            phase_2_breakdown = f"""  - 高品質版（gpt-image-1）: {self.images_high_quality}枚 × ${self.GPT_IMAGE_PRICES["standard"]:.3f} = ${cost_high:.2f}
  - Mini版（gpt-image-1-mini）: {self.images_mini}枚 × ${self.GPT_IMAGE_PRICES["mini"]:.3f} = ${cost_mini:.2f}"""
        else:
            phase_2_breakdown = f"""  - 画像生成: {self.images_generated}枚
  - 失敗: {self.images_failed}枚"""
        
        summary = f"""
{'='*60}
{self.project_name} - コストサマリー ({self.timestamp.strftime('%Y-%m-%d %H:%M:%S')})
{'='*60}

Phase 1.1: ${self.phase_1_1_cost:.2f} (約{int(self.phase_1_1_cost * self.USD_TO_JPY)}円)

Phase 1.2: ${self.phase_1_2_cost:.2f} (約{int(self.phase_1_2_cost * self.USD_TO_JPY)}円)
  - キャッシュ作成: {self.cache_creation_tokens:,} tokens
  - キャッシュ読込: {self.cache_read_tokens:,} tokens
  - 通常入力: {self.input_tokens:,} tokens
  - 出力: {self.output_tokens:,} tokens

Phase 2: ${self.phase_2_cost:.2f} (約{int(self.phase_2_cost * self.USD_TO_JPY)}円)
{phase_2_breakdown}

Cloud Run: ${self.cloud_run_cost:.2f} (約{int(self.cloud_run_cost * self.USD_TO_JPY)}円)
  - 実行時間: {self.cloud_run_duration:.0f}秒 ({self.cloud_run_duration/60:.1f}分)

{'-'*60}
総合計: ${total_usd:.2f} (約{total_jpy}円)
{'='*60}
"""
        return summary