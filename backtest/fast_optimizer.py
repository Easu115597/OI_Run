import pandas as pd
import numpy as np
import os
import sys

# 讓程式能找到上一層的目錄
ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

# ==========================================
# 🛠️ 手動修改區：在這裡換你想測試的檔案名稱
# ==========================================
TARGET_SIGNAL_FILE = "oi_rest_signals_v17.6.csv" 
# ==========================================

def run_optimization():
    file_path = os.path.join(ROOT_PATH, TARGET_SIGNAL_FILE)
    print(f"\n🚀 開始分析檔案: {TARGET_SIGNAL_FILE}")

    if not os.path.exists(file_path):
        print(f"❌ 找不到檔案: {file_path}")
        return

    try:
        # 1. 讀取與基礎清洗
        df = pd.read_csv(file_path).rename(columns=lambda x: x.strip())
        
        # 2. 確定結果欄位名稱
        target_col = 'Result_15m' if 'Result_15m' in df.columns else 'Result'
        if target_col not in df.columns:
            print(f"❌ 檔案缺少結果欄位 ({target_col})，請先執行 Data_Awakener.py 復活數據。")
            return

        # 3. 過濾出有 TP/SL 的有效樣本
        df = df.dropna(subset=[target_col])
        df = df[df[target_col].isin(['TP', 'SL'])]
        
        if len(df) == 0:
            print(f"⚠️ 檔案中沒有已完成 (TP/SL) 的樣本數據。")
            return
            
        print(f"📊 歷史有效樣本總數: {len(df)}")
    except Exception as e:
        print(f"❌ 讀取過程發生錯誤: {e}")
        return

    # 4. 循環測試不同的門檻 (這一段是核心計算)
    thresholds = [60, 65, 68, 70, 72, 75, 78, 80, 85]
    results = []

    for ts in thresholds:
        test_df = df[df['Final_Score'] >= ts].copy()
        if len(test_df) == 0:
            continue # 如果這個門檻沒單子，跳過
        
        wins = (test_df[target_col] == 'TP').sum()
        losses = (test_df[target_col] == 'SL').sum()
        total = len(test_df)
        win_rate = wins / total
        
        # 預估收益計算 (對齊你的 Config)
        pnl = (wins * 3.5) - (losses * 1.5)
        
        # 分別計算多空勝率
        long_samples = test_df[test_df['Dir'] == 1]
        short_samples = test_df[test_df['Dir'] == -1]
        
        long_wr = (long_samples[target_col] == 'TP').mean() if len(long_samples) > 0 else np.nan
        short_wr = (short_samples[target_col] == 'TP').mean() if len(short_samples) > 0 else np.nan

        results.append({
            "門檻": ts,
            "總交易": total,
            "勝率": f"{win_rate:.1%}",
            "多頭勝率": f"{long_wr:.1%}" if not np.isnan(long_wr) else "N/A",
            "空頭勝率": f"{short_wr:.1%}" if not np.isnan(short_wr) else "N/A",
            "期望收益": f"{pnl:+.1f}U"
        })

    # 5. 輸出報告與最佳化建議 (這一段放在循環結束後)
    if not results:
        print("❌ 分析失敗：在目前所有進場門檻設定下，皆無符合條件的交易樣本。")
        return

    report = pd.DataFrame(results)
    print("\n" + report.to_string(index=False))
    
    # 找出期望收益最高的行
    try:
        # 把 "+10.5U" 轉成數字 10.5 進行比較
        report['pnl_num'] = report['期望收益'].str.replace('U', '').str.replace('+', '').astype(float)
        best = report.loc[report['pnl_num'].idxmax()]
        
        print("\n" + "="*60)
        print(f"🏆 最佳策略門檻建議: {best['門檻']} 分")
        print(f"💡 預估收益: {best['期望收益']} | 勝率: {best['勝率']}")
        print("="*60 + "\n")
    except:
        print("\n⚠️ 無法產生進一步的最佳化建議。")

if __name__ == "__main__":
    run_optimization()