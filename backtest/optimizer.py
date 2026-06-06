import numpy as np

def find_best_tp_sl(dataset):
    """
    dataset 是由 analyzer.build_dataset() 產生的清單
    裡面包含 {'pnl': float}
    """
    best_score = -99999
    best_params = (0.025, 0.02) # 預設值

    # 1️⃣ 遍歷不同的止盈空間
    for tp in np.arange(0.015, 0.045, 0.005):
        # 2️⃣ 遍歷不同的止損空間
        for sl in np.arange(0.01, 0.035, 0.005):
            
            total_performance = 0

            for d in dataset:
                # ✨ 強制轉換為 float，確保不會報 UFuncNoLoopError
                try:
                    pnl = float(d['pnl']) 
                except:
                    continue

                # 模擬如果套用這個 tp/sl，結果會如何
                if pnl >= tp:
                    total_performance += tp
                elif pnl <= -sl:
                    total_performance -= sl
                else:
                    total_performance += pnl

            # 3️⃣ 找出表現最好的組合
            if total_performance > best_score:
                best_score = total_performance
                best_params = (tp, sl)

    return best_params