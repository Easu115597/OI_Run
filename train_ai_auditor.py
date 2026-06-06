import pandas as pd
import numpy as np
import joblib
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.preprocessing import StandardScaler

# ==========================================
# ⚙️ 配置區：定義我們所有「值錢」的指標
# ==========================================
FEATURES = [
    'pr_5m', 'oi_5m', 'taker_ratio', 'vol_spike', 
    'oi_1h', 'oi_4h', 'funding', 'rsi', 'bb_pos', 'state_count',
    'sb_rate', 'ob_ratio', 'pr_1'
]

def train_advanced_model(csv_file):
    print(f"📂 正在讀取數據庫: {csv_file}")
    df = pd.read_csv(csv_file).rename(columns=lambda x: x.strip())
    
    # 1. 🛠️ 數據清洗與預處理
    # 只訓練已有結果 (TP/SL) 的數據
    df = df[df['Result_15m'].isin(['TP', 'SL'])].copy()
    
    # 轉換目標：TP = 1, SL = 0
    df['target'] = df['Result_15m'].map({'TP': 1, 'SL': 0})
    
    # 處理分類變量 (Side: LONG=1, SHORT=0)
    if 'Side' in df.columns:
        df['side_encoded'] = df['Side'].map({'LONG': 1, 'SHORT': 0})
        if 'side_encoded' not in FEATURES:
            FEATURES.append('side_encoded')

    # 2. 🧮 填補缺失值 (重要，防止訓練崩潰)
    X = df[FEATURES].fillna(0)
    y = df['target']

    print(f"📊 訓練樣本數: {len(X)} (TP: {sum(y==1)}, SL: {sum(y==0)})")

    # 3. ⚖️ 數據標準化 (讓不同量級的指標如 RSI 和 Funding 能公平競爭)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # 4. ✂️ 切分數據 (80% 訓練, 20% 測試)
    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y, test_size=0.2, random_state=42, stratify=y
    )

    # 5. 🧠 訓練隨機森林 (加入 class_weight 以對抗勝率不均)
    print("🌲 正在訓練隨機森林模型 (此過程約需 1-2 分鐘)...")
    model = RandomForestClassifier(
        n_estimators=500,       # 用 500 棵樹進行投票，極其穩定
        max_depth=12,           # 限制深度，防止過擬合 (Overfitting)
        min_samples_leaf=10,    # 每個葉子最少 10 個樣本，增加通用性
        class_weight='balanced',# 自動平衡 TP/SL 的權重
        n_jobs=-1,              # 使用所有 CPU 核心
        random_state=42
    )
    model.fit(X_train, y_train)

    # 6. ✅ 評估模型
    y_pred = model.predict(X_test)
    print("\n✅ 訓練完成！【模型表現報告】:")
    print(classification_report(y_test, y_pred))

    # 7. 📈 分析特徵重要性 (告訴你哪個指標最準)
    importances = model.feature_importances_
    feat_imp = pd.Series(importances, index=FEATURES).sort_values(ascending=False)
    print("\n🧐 指標貢獻度排行 (Top 10):")
    print(feat_imp.head(10))

    # 8. 💾 儲存「模型」與「標準化器」
    # 必須同時儲存 Scaler，否則實盤數據會因量級不對而失效
    joblib.dump(model, 'ai_auditor_model.pkl')
    joblib.dump(scaler, 'ai_auditor_scaler.pkl')
    print("\n💾 模型檔案 [ai_auditor_model.pkl] 與 [ai_auditor_scaler.pkl] 已生成。")

if __name__ == "__main__":
    train_advanced_model('MASTER_SIGNALS_DATABASE.csv')