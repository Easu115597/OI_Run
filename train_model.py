import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
import joblib

def train_probability_model(csv_file):
    print(f"正在讀取數據庫: {csv_file}")
    
    # 讀取數據
    df = pd.read_csv(csv_file, low_memory=False)

    # 去掉空格並全部轉為小寫
    df.columns = [c.strip().lower() for c in df.columns]
    print(f"✅ 已識別欄位: {list(df.columns[:15])}...") # 印出前15個欄位確認

    #定義特徵 (對齊小寫)
    features = [
        'pr_5m', 'oi_5m', 'taker_ratio', 'vol_spike', 
        'oi_1h', 'oi_4h', 'funding', 'rsi', 'bb_pos', 'state_count'
    ]
    
    # 檢查特徵是否都在 CSV 中
    missing_features = [f for f in features if f not in df.columns]
    if missing_features:
        print(f"❌ 錯誤：CSV 中缺少以下欄位: {missing_features}")
        # 嘗試幫你找相近的欄位
        return
    
    # 處理標籤 (Result_15m 也要轉小寫對齊)
    target_col = 'result_15m'
    if target_col not in df.columns:
        print(f"❌ 錯誤：找不到結果欄位 {target_col}")
        return

    # 過濾出有 TP/SL 結果的數據進行訓練
    df_train = df[df[target_col].isin(['TP', 'SL'])].copy()
    
    # 轉換標籤：TP = 1, SL = 0
    df_train['target'] = df_train[target_col].map({'TP': 1, 'SL': 0})
    
    X = df_train[features].fillna(0)
    y = df_train['target']
    
    print(f"有效訓練樣本數: {len(X)} (TP: {sum(y==1)}, SL: {sum(y==0)})")

    if len(X) < 100:
        print("⚠️ 樣本數太少，模型可能不準確。建議累積更多數據再訓練。")

     # 3. 訓練模型
    print(" 正在訓練隨機森林模型...")
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    model = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42)
    model.fit(X_train, y_train)
    
    accuracy = model.score(X_test, y_test)
    print(f"✅ 訓練完成！模型準確率: {accuracy:.2%}")
    
    # 4. 儲存模型
    joblib.dump(model, 'crypto_prob_model.pkl')
    print(" 模型已儲存為 crypto_prob_model.pkl")

if __name__ == "__main__":
    train_probability_model('MASTER_SIGNALS_DATABASE.csv')

