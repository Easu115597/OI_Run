import joblib
import numpy as np
import pandas as pd

class TradeAuditor:
    def __init__(self, model_path='crypto_prob_model.pkl'):
        try:
            self.model = joblib.load(model_path)
            self.features = [
                'pr_5m', 'oi_5m', 'taker_ratio', 'vol_spike', 
                'oi_1h', 'oi_4h', 'funding', 'rsi', 'bb_pos', 'state_count'
            ]
            print("🤖 AI 審計員已就位。")
        except:
            self.model = None
            print("⚠️ 找不到 AI 模型檔案，審計員離線。")

    def predict_win_rate(self, data):
        if self.model is None:
            return 0.5
        try:
            # 1. 準備數據
            input_data = [data.get(f, 0) for f in self.features]
            
            # 2. ✨ 將 list 轉為 DataFrame 並指定欄位名稱，消除 UserWarning
            input_df = pd.DataFrame([input_data], columns=self.features)
            
            # 3. 預測
            prob = self.model.predict_proba(input_df)[0]
            return prob[1]
        except Exception as e:
            # print(f"AI預測異常: {e}")
            return 0.5