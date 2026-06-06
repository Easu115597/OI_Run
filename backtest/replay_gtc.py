import pandas as pd
import numpy as np
import os

# === 配置區 ===
INPUT_CSV = "oi_rest_signals_v22.6.csv"
OUTPUT_CSV = "v22_6_re-scored_results.csv"
GO_THRESHOLD = 78  # 你想測試的進場門檻

# --- 基礎工具函數 (由主程式移植) ---
def normalize(val, min_v, max_v):
    try:
        val = float(val)
        if max_v == min_v: return 0
        return 2 * ((val - min_v) / (max_v - min_v)) - 1
    except: return 0

def directional_map(val, side_str):
    return val if side_str == "LONG" else -val

def get_structure_score(struct):
    mapping = {
        "LONG_BUILD": 0.9, "SHORT_BUILD": 0.9,
        "LONG_EXIT": -0.6, "SHORT_COVER": -0.4,
        "CHOP": 0.1, "REVERSAL": 0.3
    }
    return mapping.get(struct, 0.0)


# 模擬 V22.8 的工具函數 (這裡簡化，請對應你主程式的邏輯)
def simulate_v22_8_logic(row):
    """
    Titan-Quant V21 四層分級決策系統
    1. 強化 CHOP 壓縮起爆識別 (0.8% 空間)
    2. 新增早期趨勢 (state_count <= 4) 加分
    """
    try:
         # ✨ 【關鍵修復】在此處立即定義 d
        # 將 CSV 的大寫欄位對齊到代碼用的小寫 key
        d = {
            'oi_5m': float(row.get('OI_5m', 0)),
            'oi_1h': float(row.get('OI_1h', 0)),
            'oi_4h': float(row.get('OI_4h', 0)),
            'pr_5m': float(row.get('Pr_5m', 0)),
            'vol_spike': float(row.get('Vol_Spike', 1.0)),
            'taker_ratio': float(row.get('Taker_Ratio', 1.0)),
            'rsi': float(row.get('RSI', 50)),
            'bb_pos': float(row.get('BB_Pos', 0.5)),
            'funding': float(row.get('Funding', 0)),
            'structure': str(row.get('Structure', 'CHOP')),
            'state': str(row.get('State', 'RANGE')), # 注意：若CSV沒存State，預設RANGE
            'state_count': int(row.get('State_Count', 0))
        }
        
        # 決定方向
        side = "LONG" if row.get('Dir') == 1 else "SHORT"

        # --- 以下完全對齊 V22.8 代碼 ---
        
        # === [Step 0: 變數預取與初始化] ===
        state = d.get('state', 'RANGE')
        state_count = d.get('state_count', 0)
        pr = d.get('pr_5m', 0)
        oi = d.get('oi_5m', 0)
        taker = d.get('taker_ratio', 1.0)
        vol = d.get('vol_spike', 1.0)
        struct = d.get('structure', 'CHOP')
        funding = d.get('funding', 0)
        
        oi_1h = d.get("oi_1h", 0)
        oi_4h = d.get("oi_4h", 0)
        rsi = d.get('rsi', 50)
        bb = d.get('bb_pos', 0.5)
        pr_1m = d.get('pr_1m', 0)

        # 💡 [關鍵修正]：先初始化所有加減分變數，防止 NameError
        co_bonus = 0
        risk_penalty = 0
        funding_bonus = 0

        # === [Step 1: 實施甜蜜區間封頂 (Clamp) - 依據你的實測數值] ===
        # 確保極端值不會稀釋權重，熱點區直接拿滿分
        oi_5m_c = max(min(oi, 6.0), -6.0)     
        oi_1h_c = max(min(oi_1h, 30.0), -30.0)   
        oi_4h_c = max(min(oi_4h, 45.0), -45.0)   
        vol_c = min(vol, 12.0)                   # 12倍以上視為滿分熱度
        taker_c = max(min(taker, 4.5), 0.2)      # 4.5倍以上視為滿分攻擊力

        # === [Layer 1: 物理力量 (使用你設定的權重 26/30/22/8)] ===
        # 1. 結構權重 (26%)
        s_raw = get_structure_score(d['structure'])
        s_score = directional_map(s_raw, side) * 0.26
        
        # 2. OI 權重 (30%) - 使用校準後的區間
        o5 = normalize(oi_5m_c, 0.2, 4.0) 
        o1 = normalize(oi_1h_c, 0, 15.0)
        o4 = normalize(oi_4h_c, 0, 25.0)
        o_score = (o5 * 0.18 + o1 * 0.12 + o4 * 0.08)
        
        # 3. 攻擊性權重 (22%)
        t_norm = normalize(taker_c, 0.6, 4.0)
        v_norm = normalize(vol_c, 1.0, 10.0)
        attack_score = (directional_map(t_norm, side) * 0.18 + v_norm * 0.10)
        
        # 4. 盤口權重 (8%)
        ob_norm = normalize(d.get('ob_ratio', 1.0), 0.7, 1.3)
        ob_score = directional_map(ob_norm, side) * 0.08
        
        base_score = round((s_score + o_score + attack_score + ob_score + 1) * 50)

        # === [Layer 2: 你的專屬邏輯加分區 (全數保留)] ===
        
        # 1. Funding 方向確認
        if side == "LONG":
            if funding < -0.01: funding_bonus += 2
            elif funding > 0.03: funding_bonus -= 2
        else: # SHORT
            if funding > 0.01: funding_bonus += 2
            elif funding < -0.03: funding_bonus -= 2

        # 2. 趨勢一致性檢查 (真突破 vs 誘多/誘空)
        if side == "LONG":
            if pr > 0.15 and oi > 0.35: co_bonus += 12 
            if pr > 0.15 and oi < -0.3: co_bonus -= 24
        else:
            if pr < -0.15 and oi > 0.35: co_bonus += 12
            if pr < -0.15 and oi < -0.3: co_bonus -= 24

        # 3. Taker 能量驗證
        if side == "LONG" and taker > 1.15: co_bonus += 8
        if side == "SHORT" and taker < 0.85: co_bonus += 8

        # 4. ✨ [保留] 微趨勢啟動加分
        if side == "LONG":
            if 0.12 < pr < 0.22 and oi > 0.2 and taker > 1.08 and vol > 1.3: co_bonus += 8
        else:
            if -0.22 < pr < -0.12 and oi > 0.2 and taker < 0.92 and vol > 1.3: co_bonus += 8

        # 5. [保留] 中長線趨勢共振
        if side == "LONG":
            if oi_1h > 10 and oi_4h > 15: co_bonus += 6
            if oi_1h > 20 and oi_4h > 30: co_bonus += 8
        else:
            if oi_1h < -10 and oi_4h < -15: co_bonus += 6
            if oi_1h < -20 and oi_4h < -30: co_bonus += 8

        # 6. ✨ [保留] 爆發共振加速器
        if side == "LONG":
            if (pr > 0.45 and oi > 0.8 and taker > 1.25 and vol > 2.5 and "BUILD" in struct and state_count <= 4):
                co_bonus += 12
        elif side == "SHORT":
            if (pr < -0.45 and oi > 0.8 and taker < 0.75 and vol > 2.5 and "BUILD" in struct and state_count <= 4):
                co_bonus += 12

        # 7. [保留] CHOP 壓縮加分
        if state == "CHOP":
            if abs(pr) < 0.8 and oi > 0.4 and vol > 1.2:
                if (side == "LONG" and taker > 1.05) or (side == "SHORT" and taker < 0.95):
                    co_bonus += 8

        # 8. ✨ [保留] 雙層結構獎勵 (3+4)
        if "BUILD" in struct:
            co_bonus += 3
            if state_count <= 6: co_bonus += 4

        # 9. ✨ [新增] 強勢擠兌特赦分
        if side == "LONG" and funding < -1.0 and oi_4h > 15:
            co_bonus += 10  # 極端負費率 + 長線增倉 = 必漲信號，直接加 10 分保送

        # === [Layer 3: 風險懲罰區 (你的邏輯 + 數據極端重罰)] ===
        
        # 1. [保留] CHOP 衰減與對敲過濾
        if state == "CHOP":
            if state_count > 12: risk_penalty -= 5
            if state_count > 24: risk_penalty -= 8
            risk_penalty -= 2 # 你的優化 10

        if abs(pr) < 0.10 and abs(oi) > 1.8 and vol > 4.0:
            co_bonus -= 20 # 你的原本邏輯

        # 2. 🚨 [新增] 數據極端過熱重罰 (根據你的 40/20 數據觀察)
        if vol > 18.0: risk_penalty -= 15       # 18倍以上視為瘋狗單
        if taker > 10.0: risk_penalty -= 20     # 10倍以上視為盤口空虛

        # 3. [保留] RSI / BB 階梯懲罰 (完全照你的數值)
        if side == "LONG":
            if rsi > 92: risk_penalty -= 10
            elif rsi > 86: risk_penalty -= 6
            elif rsi > 80: risk_penalty -= 3
            if bb > 1.03: risk_penalty -= 6
            elif bb > 0.97: risk_penalty -= 3
        else:
            if rsi < 8: risk_penalty -= 10
            elif rsi < 14: risk_penalty -= 6
            elif rsi < 22: risk_penalty -= 3
            if bb < -0.03: risk_penalty -= 6
            elif bb < 0.03: risk_penalty -= 3

        # 4. [保留] 其餘過濾點
        if (side == "LONG" and pr_1m > 1.8) or (side == "SHORT" and pr_1m < -1.8): risk_penalty -= 8
        if abs(pr) < 0.05 and abs(oi) < 0.15: risk_penalty -= 10
        if abs(oi) > 6: co_bonus -= 8 # 你的原本邏輯

        # === [Layer 4: 最終合成] ===
        final_score = max(0, min(100, base_score + co_bonus + risk_penalty + funding_bonus))
        
        # ✨ 關鍵修改：回測腳本只回傳數字，不回傳元組
        return float(final_score)

    except Exception as e:
        return 0.0
pass

def analyze_history(csv_path):
    df = pd.read_csv(csv_path)
    results = []
    for _, row in df.iterrows():
        score = simulate_v22_score(row)
        results.append({
            "Time": row['Time'],
            "Symbol": row['Symbol'],
            "Original_Score": row['Final_Score'],
            "V22_6_Score": score,
            "Would_Go": "✅ YES" if score >= 78 else "❌ NO"
        })
    return pd.DataFrame(results)

# --- 4. 執行回算主程式 ---
if __name__ == "__main__":
    if not os.path.exists(INPUT_CSV):
        print(f"❌ 錯誤：找不到 {INPUT_CSV}")
    else:
        print(f"📖 正在讀取歷史紀錄 {INPUT_CSV}...")
        df = pd.read_csv(INPUT_CSV)
        
        print(f"🧪 正在套用 V22.8 物理引擎回算分數...")
        # 套用評分函數
        df['V22_Score'] = df.apply(simulate_v22_8_logic, axis=1)
        
        # 標註變動
        df['Action_Change'] = "STAY"
        
        # ✨ 修復比較邏輯：現在 V22_Score 是純數字了
        mask_become_go = (df['Decision'] == 'NO_GO') & (df['V22_Score'] >= 78)
        df.loc[mask_become_go, 'Action_Change'] = "⭐ BECOME_GO"
        
        df.to_csv(OUTPUT_CSV, index=False)
        print(f"✅ 處理完成！結果已存至: {OUTPUT_CSV}")
        
        changed = df[df['Action_Change'] == "⭐ BECOME_GO"]
        print(f"\n📊 --- 回算分析報告 ---")
        print(f"新發現的進場機會: {len(changed)} 筆")
        
        if len(changed) > 0:
            print(f"\n✨ 重新抓到的訊號範例：")
            cols = ['Time', 'Symbol', 'Final_Score', 'V22_Score', 'Structure', 'Reason']
            # 只顯示有變動的前 20 筆
            print(changed[cols].head(20).to_string(index=False))