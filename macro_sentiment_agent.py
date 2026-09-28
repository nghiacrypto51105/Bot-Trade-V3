"""
macro_sentiment_agent.py
------------------------
Module Người Gác Cổng Vĩ Mô (Macro Sentiment Gatekeeper).
Chịu trách nhiệm:
- Sử dụng mô hình NLP ProsusAI/finbert từ Hugging Face Transformers để phân tích cảm xúc tin tức tài chính.
- Tổng hợp điểm cảm xúc và xuất ra trạng thái vĩ mô:
  * DEFENSIVE: Tin tiêu cực chiếm ưu thế -> Khóa không cho vào lệnh để bảo vệ sinh mạng.
  * AGGRESSIVE: Tin tích cực chiếm ưu thế -> Cho phép tấn công/vào lệnh.
  * NEUTRAL: Trạng thái trung lập.
"""

import logging
from typing import List, Dict, Optional

logger = logging.getLogger("MacroGatekeeper")


class MacroSentimentAgent:
    """
    Macro Sentiment Agent: Đánh giá tâm lý vĩ mô từ tin tức tài chính bằng FinBERT.
    """

    def __init__(self, model_name: str = "ProsusAI/finbert", use_mock_if_unavailable: bool = True):
        self.model_name = model_name
        self.use_mock = use_mock_if_unavailable
        self.classifier = None
        self._initialize_pipeline()

    def _initialize_pipeline(self):
        """Khởi tạo Transformers pipeline với FinBERT."""
        try:
            from transformers import pipeline, AutoTokenizer, AutoModelForSequenceClassification
            logger.info(f"[NLP LOADING] Đang nạp mô hình {self.model_name}...")
            
            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            self.classifier = pipeline("sentiment-analysis", model=model, tokenizer=tokenizer)
            logger.info(f"[NLP READY] Mô hình {self.model_name} đã sẵn sàng.")
        except Exception as e:
            logger.warning(
                f"[NLP FALLBACK] Không thể nạp mô hình Transformers trực tiếp ({e}). "
                "Sử dụng Heuristic Fallback Analyzer để đảm bảo tính liên tục của hệ thống."
            )
            self.classifier = None

    def analyze_headlines(self, headlines: List[str]) -> Dict[str, float]:
        """
        Phân tích danh sách các tiêu đề tin tức và trả về tỷ lệ [positive, negative, neutral].
        """
        if not headlines:
            return {"positive": 0.0, "negative": 0.0, "neutral": 1.0}

        if self.classifier:
            try:
                results = self.classifier(headlines)
                counts = {"positive": 0, "negative": 0, "neutral": 0}
                for res in results:
                    label = res["label"].lower()
                    if label in counts:
                        counts[label] += 1
                
                total = len(headlines)
                return {k: v / total for k, v in counts.items()}
            except Exception as e:
                logger.error(f"[NLP INFERENCE ERROR] Lỗi phân tích FinBERT: {e}")

        # Fallback Heuristic Phân tích từ khóa tài chính nếu mô hình chưa tải xong
        logger.debug("[NLP HEURISTIC] Đang phân tích cảm xúc qua từ khóa vĩ mô...")
        negative_keywords = ["crash", "ban", "inflation", "war", "sec", "liquidation", "bear", "hike", "default", "crisis", "fall", "dump"]
        positive_keywords = ["surge", "etf", "approval", "rally", "bull", "cut", "stimulus", "adoption", "record", "gain", "pump", "breakout"]

        pos_count = 0
        neg_count = 0
        neu_count = 0

        for text in headlines:
            lower_text = text.lower()
            is_neg = any(w in lower_text for w in negative_keywords)
            is_pos = any(w in lower_text for w in positive_keywords)

            if is_neg and not is_pos:
                neg_count += 1
            elif is_pos and not is_neg:
                pos_count += 1
            else:
                neu_count += 1

        total = len(headlines)
        return {
            "positive": pos_count / total,
            "negative": neg_count / total,
            "neutral": neu_count / total
        }

    def get_macro_state(self, sample_headlines: Optional[List[str]] = None) -> str:
        """
        Xác định cờ trạng thái Vĩ Mô:
        - DEFENSIVE: Nếu tỷ lệ tin tiêu cực > 0.40
        - AGGRESSIVE: Nếu tỷ lệ tin tích cực > 0.40
        - NEUTRAL: Các trường hợp còn lại
        """
        headlines = sample_headlines or self._get_sample_headlines()
        sentiment_scores = self.analyze_headlines(headlines)

        neg_score = sentiment_scores.get("negative", 0.0)
        pos_score = sentiment_scores.get("positive", 0.0)
        neu_score = sentiment_scores.get("neutral", 0.0)

        logger.info(
            f"[MACRO EVAL] Tin tức: {len(headlines)} mục | "
            f"Tích cực: {pos_score:.2%} | Tiêu cực: {neg_score:.2%} | Trung lập: {neu_score:.2%}"
        )

        if neg_score >= 0.40:
            state = "DEFENSIVE"
        elif pos_score >= 0.40:
            state = "AGGRESSIVE"
        else:
            state = "NEUTRAL"

        logger.info(f"[MACRO STATE] Trạng thái Người Gác Cổng hiện tại: [{state}]")
        return state

    def _get_sample_headlines(self) -> List[str]:
        """Danh sách tin tức mẫu giả lập trong điều kiện thị trường biến động."""
        return [
            "Federal Reserve signals unexpected interest rate hike amid persistent inflation concerns",
            "SEC initiates investigation into major crypto derivative liquidity providers",
            "Bitcoin breaks past resistance level with significant institutional inflows",
            "Global supply chain disruption triggers broader market uncertainty",
            "Tech giants report stronger-than-expected Q3 earnings reports"
        ]
