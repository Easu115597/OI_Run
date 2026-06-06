import asyncio
import json
import aiohttp
import time
import redis
import csv
import os
import pandas as pd
import pandas_ta as ta
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from logic_engine import hard_filter
from config import CONFIG
import re
import traceback
from ai_auditor import TradeAuditor

# 匯入自定義模組 (確保這些檔案在同目錄)
from brain import SelfEvolvingBrain
from Simulator_Broker import SimulatorBroker

# ================= 配置與路徑 =================
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
VLLM_URL = "http://localhost:8000/v1/chat/completions"
MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct-AWQ"

TG_TOKEN = "8540426175:AAHILTmO_Z3jRuYN7Ah7YPKhw5h6T6mrrOY"
TG_CHAT_ID = "-1003710235381"
TG_THREAD_ID = 1136

CSV_FILE = CONFIG["signal_csv"]
SUMMARY_CSV = CONFIG["summary_csv"]
TOP_N = 100

# ================= 基礎工具函數 =================

def parse_json_robustly(text):
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match: return json.loads(match.group(0))
        return None
    except: return None

def build_self_evolving_config(csv_path):
    """V24 核心：讀取歷史數據，分析哪些參數在賺錢，動能更新門檻"""
    default_config = {
        "allowed_structures": ["LONG_BUILD", "SHORT_BUILD"],
        "min_score": 68, "max_dev": 2.8, "max_sb": 25.0, "max_dev_for_entry": 1.8
    }
    if not os.path.exists(csv_path): return default_config
    try:
        df = pd.read_csv(csv_path)
        df_done = df[df['Result_15m'].isin(['TP', 'SL'])].copy()
        if len(df_done) < 15: return default_config
        df_done['is_win'] = df_done['Result_15m'] == 'TP'
        struct_wr = df_done.groupby('Structure')['is_win'].mean()
        good_structs = struct_wr[struct_wr > 0.52].index.tolist()
        global_wr = df_done['is_win'].mean()
        new_min_score = 85 if global_wr < 0.45 else 80
        print(f"📈 [大腦進化] 全球勝率: {global_wr:.2%}, 更新門檻: {new_min_score}")
        return {
            "allowed_structures": good_structs if good_structs else ["LONG_BUILD", "SHORT_BUILD"],
            "min_score": new_min_score, "max_dev": 2.5, "max_sb": 22.0, "max_dev_for_entry": 1.5
        }
    except Exception as e:
        print(f"⚠️ 大腦進化失敗: {e}"); return default_config

# ================= 核心物理引擎 =================

def detect_v20_physics(d):
    """V25 改版物理判定：保留所有線性權重與品質評級"""
    pr = d.get('pr_5m', 0)
    oi = d.get('oi_5m', 0)
    sb = d.get('sb_rate', 0)
    dev = d.get('dev_osc', 0)
    taker = d.get('taker_ratio', 1.0)
    vol = d.get('vol_spike', 1.0)
    
    # 真正的品質判定
    direction = 1 if pr > 0 else -1
    if (direction == 1 and sb > 15) or (direction == -1 and sb < -15): q_rank = "S (極致共振)"
    elif (direction == 1 and sb > 5) or (direction == -1 and sb < -5): q_rank = "A (良好支持)"
    elif (direction == 1 and sb > -5) or (direction == -1 and sb < 5): q_rank = "B (平庸/無力)"
    else: q_rank = "D (嚴重背離/主力出貨)"
    
    # 結構鎖定
    if oi > 0: struct = "LONG_BUILD" if pr > 0 else "SHORT_BUILD"
    else: struct = "SHORT_COVER" if pr > 0 else "LONG_EXIT"

    # 線性加權 (線性 Confidence) - 此處保留原本 V18 的 3.8 係數
    conf = 4.0 if "BUILD" in struct else 1.5
    sb_val = sb if direction == 1 else -sb
    conf += max(-2.0, min(5.0, sb_val / 12.0))
    taker_val = (taker - 1.0) if direction == 1 else (1.0 - taker)
    conf += max(-1.0, min(4.0, taker_val * 4.0))
    conf += max(0.0, min(3.0, (vol - 1.0) * 1.5))
    
    base_score = int(42 + (conf * 3.8))
    risk_tags = []
    if abs(dev) > 2.5: risk_tags.append("STRETCHED")
    if (direction == 1 and sb < -5) or (direction == -1 and sb > 5): risk_tags.append("DIVERGENCE")
    
    return {
        "structure": struct, "direction": direction, "pre_score": int(max(5, min(95, base_score))),
        "risk_tags": risk_tags, "quality_rank": q_rank, "python_fact": f"價({pr}%) OI({oi}%) SB({sb}) 乖離({dev}%)"
    }

def classify_market_state(d):
    """判定市場處於 趨勢(TREND) 或 震盪(CHOP)"""
    pr5 = d.get("pr_5m", 0)
    vol = d.get("vol_spike", 1.0)
    if abs(pr5) > 1.0 and vol > 2.5: return "TREND"
    if abs(pr5) > 0.3 and d.get("oi_5m", 0) > 0.5 and vol > 1.2: return "TREND"
    return "CHOP"

# ================= 主機器人類別 =================

class TitanV17:
    def __init__(self):
        self.symbols = []
        self.cooldowns = defaultdict(lambda: datetime.min)
        self.state_tracker = defaultdict(lambda: {"state": "NONE", "count": 0})
        self.market_data = defaultdict(dict)
        self.current_p_map = {}
        self.auditor = TradeAuditor()

        # 實例化模組
        self.config = build_self_evolving_config(CSV_FILE)
        self.brain = SelfEvolvingBrain(CONFIG)
        self.broker = SimulatorBroker(initial_balance=CONFIG['initial_balance'])
        
        self.win_rates = {"GLOBAL": 0.5}
        self.last_summary_time = time.time()
        self.processing_symbols = set()

        # 15 分鐘摘要數據存儲
        self.summary_data = defaultdict(lambda: {
            "count": 0, "max_base": 0, "max_ai": 0, 
            "max_pr": 0, "max_oi": 0, "max_taker": 0, "max_vol": 0,
            "max_rsi": 0, "max_bb": 0, "max_phys": 0, "max_final": 0,
            "start_price": 0, "last_scene": "", "last_reason": "", "total_change": 0
        })
        self._init_csv()

    def _init_csv(self):
        """初始化 CSV 表頭 - 支援 V31 與 V23 平滑版雙紀錄"""
        # 1. 訊號紀錄檔 (Signal CSV)
        if not os.path.exists(CSV_FILE):
            with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Time', 'Symbol', 'Price', 'OI_5m', 'OI_1h', 'OI_4h', 
                    'Pr_5m', 'Vol_Spike', 'Taker_Ratio', 'RSI', 'BB_Pos', 'Funding', 
                    'AI_Adj', 'Base_Score', 'Phys_Score', 'Final_Score', 
                    'Sm_Base', 'Sm_Phys','AI_Win_Rate', 'AI_Advice','Stage'
                    'Decision', 'Structure', 'State_Count', 'Reason', 'Dir', 'Result_15m' ,
                    'Reason_Metadata', 'Side_Num', 'Hold_Status'
                ])
        
        # 2. 摘要紀錄檔 (Summary CSV)
        if not os.path.exists(SUMMARY_CSV):
            with open(SUMMARY_CSV, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Time', 'Symbol', 'ScanCount', 'TotalChange', 'MaxOI', 'MaxTaker', 'MaxVol', 
                    'Max_RSI', 'Max_BB', 'MaxBase', 'MaxPhys', 'MaxFinal', 
                    'Max_SmBase', 'Max_SmPhys','Max_AI_Win_Rate', 'Max_AI_Advice','Stage' # ✨ 新增：摘要統計平滑版最高分
                    'LastScene', 'LastReason'
                ])
        
    async def get_top_symbols(self):
        """獲取交易額前 TOP_N 的標的"""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://fapi.binance.com/fapi/v1/ticker/24hr") as resp:
                    data = await resp.json()
                    filtered = [s for s in data if s['symbol'].endswith('USDT') and not any(e in s['symbol'] for e in ['USDC', 'DAI', 'PAXG'])]
                    sorted_list = sorted(filtered, key=lambda x: float(x['quoteVolume']), reverse=True)
                    self.symbols = [item['symbol'] for item in sorted_list[:TOP_N]]
                    print(f"🚀 V25.5 旗艦版啟動：鎖定前 {len(self.symbols)} 名活躍幣種")
            except Exception as e:
                print(f"⚠️ 獲取交易對失敗: {e}")

    async def fetch_orderbook(self, session, symbol):
        """盤口比 (Leading Indicator)"""
        url = f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=20"
        try:
            async with session.get(url) as resp:
                data = await resp.json()
                bids = sum(float(b[1]) for b in data['bids'])
                asks = sum(float(a[1]) for a in data['asks'])
                return round(bids / asks, 2) if asks > 0 else 1.0
        except: return 1.0

    async def fetch_klines_indicators(self, session, symbol):
        """獲取 K 線指標：RSI, BB, SB_Rate, Supertrend"""
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1m&limit=100"
        try:
            async with session.get(url, timeout=5) as resp:
                raw_data = await resp.json()
                if not isinstance(raw_data, list) or len(raw_data) < 50: return None
                df = pd.DataFrame(raw_data).iloc[:, [0, 1, 2, 3, 4, 5, 9]]
                df.columns = ['ts', 'open', 'high', 'low', 'close', 'volume', 'taker_buy_vol']
                df = df.apply(pd.to_numeric)
                
                # 🛠️ SB Rate 高階計算 (Buy Volume 物理還原)
                df['tw'] = df['high'] - df[['open', 'close']].max(axis=1)
                df['bw'] = df[['open', 'close']].min(axis=1) - df['low']
                df['body'] = (df['close'] - df['open']).abs()
                df['buy_vol'] = df['volume'] * df.apply(lambda r: 0.5 * (r['tw'] + r['bw'] + (2 * r['body'] if r['open'] <= r['close'] else 0)) / (r['tw'] + r['bw'] + r['body'] + 0.001), axis=1)
                sb_series = (df['buy_vol'] - (df['volume'] - df['buy_vol'])).rolling(34).mean()
                sb_rate = (sb_series / (df['volume'].rolling(34).mean() + 0.1)).iloc[-2] * 100

                # 🛠️ Supertrend 乖離
                sti = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=3.0)
                st_val = sti.iloc[:, 0].iloc[-2]
                dev_osc = (df['close'].iloc[-2] - st_val) / st_val * 100 if st_val != 0 else 0

                # 🛠️ RSI & Bollinger Bands
                rsi = ta.rsi(df['close'], length=14).iloc[-2]
                bb = ta.bbands(df['close'], length=20, std=2)
                bb_pos = (df['close'].iloc[-2] - bb.iloc[:, 0].iloc[-2]) / (bb.iloc[:, 2].iloc[-2] - bb.iloc[:, 0].iloc[-2] + 0.0001)
                
                return {
                    "rsi": round(float(rsi), 1), "bb_pos": round(float(bb_pos), 2),
                    "vol_spike": round(float(df['volume'].iloc[-2] / df['volume'].iloc[-22:-2].mean()), 2),
                    "taker_ratio": round(float(df['taker_buy_vol'].iloc[-2] / (df['volume'].iloc[-2] - df['taker_buy_vol'].iloc[-2] + 0.1)), 2),
                    "sb_rate": round(float(sb_rate), 2), "dev_osc": round(float(dev_osc), 2),
                    "pr_1": round(((df['close'].iloc[-1]/df['close'].iloc[-2])-1)*100, 2)
                }
        except: return None

    def normalize(self, value, min_val, max_val):
        if max_val == min_val: return 0
        v = (value - min_val) / (max_val - min_val)
        return max(min(v * 2 - 1, 1), -1)

    def directional_map(self, score, side):
        return score if side == "LONG" else -score

    def get_structure_score(self, structure):
        mapping = {
            "LONG_BUILD": 0.9, "SHORT_BUILD": 0.9,
            "LONG_EXIT": -0.6, "SHORT_COVER": -0.4,
            "CHOP": 0.1, "REVERSAL": 0.3
        }
        return mapping.get(structure, 0.0)
    
    def calculate_v23_smooth_score(self, d, side):
        """
        [影子模型] Titan-Quant V23 - 平滑趨勢版
        僅用於統計與比較，不參與進場決策
        """
        try:
            # 變數提取
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
            pr_1 = d.get('pr_1', 0)

            co_bonus, risk_penalty, funding_bonus = 0, 0, 0

            # === [Step 1: 實施甜蜜區間封頂 (Clamp) - 依據你的實測數值] ===
            # 確保極端值不會稀釋權重，熱點區直接拿滿分
            oi_5m_c = max(min(oi, 6.0), -6.0)     
            oi_1h_c = max(min(oi_1h, 30.0), -30.0)   
            oi_4h_c = max(min(oi_4h, 45.0), -45.0)   
            vol_c = min(vol, 12.0)                   # 12倍以上視為滿分熱度
            taker_c = max(min(taker, 4.5), 0.2)      # 4.5倍以上視為滿分攻擊力

            # === [Layer 1: 物理力量 (使用你設定的權重 26/30/22/8)] ===
            # 1. 結構權重 (26%)
            s_raw = self.get_structure_score(struct)
            s_score = self.directional_map(s_raw, side) * 0.20
            
            # 2. OI 權重 (30%) - 使用校準後的區間
            o5 = self.normalize(oi_5m_c, 0.2, 3.0) 
            o1 = self.normalize(oi_1h_c, 0, 10.0)
            o4 = self.normalize(oi_4h_c, 0, 25.0)
            o_score = (o5 * 0.26 + o1 * 0.24 + o4 * 0.24)
            
            # 3. 攻擊性權重 (22%)
            t_norm = self.normalize(taker_c, 0.6, 3.0)
            v_norm = self.normalize(vol_c, 1.0, 7.5)
            attack_score = (self.directional_map(t_norm, side) * 0.24 + v_norm * 0.20)
            
            # 4. 盤口權重 (8%)
            ob_norm = self.normalize(d.get('ob_ratio', 1.0), 0.7, 1.3)
            ob_score = self.directional_map(ob_norm, side) * 0.16
            
            sm_base_score = round((s_score + o_score + attack_score + ob_score + 1) * 50)

            # === [Layer 2: 你的專屬邏輯加分區 (全數保留)] ===
            
            # 1. Funding 方向確認
            if side == "LONG":
                if funding < -0.03: funding_bonus += 2
                elif funding > 0.05: funding_bonus -= 2
            else: # SHORT
                if funding > 0.05: funding_bonus += 2
                elif funding < -0.03: funding_bonus -= 2

            # 2. 趨勢一致性檢查 (真突破 vs 誘多/誘空)
            if side == "LONG":
                if pr > 0.3 and oi > 0.5: co_bonus += 3 
                if pr > 0.3 and oi < -0.5: co_bonus -= 5
            else:
                if pr < -0.3 and oi > 0.5: co_bonus += 3
                if pr < -0.3 and oi < -0.5: co_bonus -= 5

            # 3. Taker 能量驗證
            if side == "LONG" and taker > 1.15: co_bonus += 8
            if side == "SHORT" and taker < 0.85: co_bonus += 3

                        
            # 6. ✨ [保留] 爆發共振加速器
            if side == "LONG":
                if (pr > 0.45 and oi > 0.8 and taker > 1.25 and vol > 2.5 and "BUILD" in struct and state_count <= 4):
                    co_bonus += 12
            elif side == "SHORT":
                if (pr < -0.45 and oi > 0.8 and taker < 0.75 and vol > 2.5 and "BUILD" in struct and state_count <= 4):
                    co_bonus += 12

            # 9. ✨ [新增] 強勢擠兌特赦分
            if side == "LONG" and funding < -1.0 and oi_4h > 15:
                co_bonus += 8  # 極端負費率 + 長線增倉 = 必漲信號，直接加 10 分保送
            
            # === [Layer 2.5: V34.5 影子核避雷針 - 優化版] ===
            melt_down_penalty = 0
            melt_reason = ""

            # 1. 🚨 費率過熱熔斷 (多空雙向避雷)
            # 多頭：費率過正 (>0.08%) 代表擁擠；空頭：費率過負 (<-0.1%) 代表軋空風險
            if side == "LONG" and funding > 0.08:
                melt_down_penalty -= 60
                melt_reason = "FUNDING_OVERHEAT_LONG"
            elif side == "SHORT" and funding < -0.10:
                melt_down_penalty -= 60
                melt_reason = "FUNDING_SQUEEZE_SHORT"
            
            # 2. 🚨 持倉力竭熔斷 (防止接最後一棒)
            if oi_4h > 65 and rsi > 82:
                melt_down_penalty -= 60
                melt_reason = "OI_CLIMAX_EXHAUSTION"
                
            # 3. 📉 逆勢影子修正
            if (side == "LONG" and pr < -0.5) or (side == "SHORT" and pr > 0.5):
                sm_base_score -= 20

            # === [Layer 3: 風險懲罰區 (你的邏輯 + 數據極端重罰)] ===
            
            # 1. [保留] CHOP 衰減與對敲過濾
            if state == "CHOP":
                if state_count > 20: risk_penalty -= 6
                if state_count > 36: risk_penalty -= 10
                risk_penalty -= 2 # 你的優化 10

            
            # 2. 🚨 [新增] 數據極端過熱重罰 (根據你的 40/20 數據觀察)
            if vol > 18.0: risk_penalty -= 6       # 18倍以上視為瘋狗單
            if taker > 24.0: risk_penalty -= 6     # 10倍以上視為盤口空虛

            # 3. [保留] RSI / BB 階梯懲罰 (完全照你的數值)
            if side == "LONG":
                if rsi > 91: risk_penalty -= 8
                elif rsi > 87: risk_penalty -= 5
                elif rsi > 83: risk_penalty -= 3
                if bb > 1.15: risk_penalty -= 6
                elif bb > 1.05: risk_penalty -= 3
            else:
                if rsi < 12: risk_penalty -= 8
                elif rsi < 15: risk_penalty -= 5
                elif rsi < 19: risk_penalty -= 3
                if bb < -0.15: risk_penalty -= 6
                elif bb < -0.05: risk_penalty -= 3

            # 4. [保留] 其餘過濾點
            if (side == "LONG" and pr_1 > 1.8) or (side == "SHORT" and pr_1 < -1.8): risk_penalty -= 8
            if abs(pr) < 0.05 and abs(oi) < 0.15: risk_penalty -= 2
            if abs(oi) > 6: co_bonus -= 4 # 你的原本邏輯

            # === V34 影子核溢價 ===
            shadow_bonus = 0
            
            # 如果長線資金 (4h) 穩定且短線 (1h) 持續注入
            if d.get('oi_4h', 0) > 10.0 and d.get('oi_1h', 0) > 5.0:
                shadow_bonus += 8
                
            # 如果是高頻掃描幣且結構紮實
            if d.get('state_count', 0) > 20 and "BUILD" in d.get('structure', ''):
                shadow_bonus += 5

            # === [Layer 4: 最終合成] ===
            sm_phys_score = sm_base_score + co_bonus + risk_penalty + funding_bonus + shadow_bonus + melt_down_penalty
            return float(sm_phys_score), float(sm_base_score)
        except Exception as e:
            # 如果算失敗了，回傳 0, 0 而不是讓程式崩潰
            print(f"⚠️ V23 影子計算異常: {e}")
            return 0.0, 0.0
            
    def calculate_v25_composite_score(self, d, side, trend_threshold, shadow_phys=0):
        """
        Titan-Quant V33.0 [軍團重組版]
        修正：1. 變數對齊 2. 補完影子判定 3. 修正空頭燃料 4. 統一回傳格式
        """
        try:
            # === [Step 0: 強制數值化提取] ===
            pr = float(d.get('pr_5m', d.get('pr', 0)))
            oi = float(d.get('oi_5m', d.get('oi', 0)))
            taker = float(d.get('taker_ratio', d.get('taker', 1.0)))
            vol = float(d.get('vol_spike', d.get('vol', 1.0)))
            sb = float(d.get('sb_rate', d.get('sb', 0)))
            oi_4h = float(d.get('oi_4h', 0))
            funding = float(d.get('funding', d.get('funding_rate', 0)))
            state_count = int(d.get('state_count', d.get('sc', 0)))
            pr_1 = float(d.get('pr_1', d.get('pr_1m', 0)))
            
            # 確保大小寫統一
            side_str = str(side).upper()
            struct = str(d.get('structure', 'CHOP')).upper()
            state = str(d.get('state', 'TREND')).upper()
            
            # ✨ [修正 1]：定義影子核活躍狀態 (由外部傳入 shadow_phys)
            v23_is_active = (shadow_phys >= 72)

            # === [Layer 1: V18.8 暴力原始底座] ===
            conf = 4.0 if "BUILD" in struct else 1.5
            sb_val = sb if side_str == "LONG" else -sb
            conf += max(-2.0, min(5.0, sb_val / 12.0))
            taker_val = (taker - 1.0) if side_str == "LONG" else (1.0 - taker)
            conf += max(-1.0, min(4.0, taker_val * 4.0))
            conf += max(0.0, min(3.0, (vol - 1.0) * 1.5))
            
            # --- ✨ 核心修正：5m OI 燃料分 (修正 SHORT 判定) ---
            fuel_score = 0
            if side_str == "LONG" and pr > 0.2 and oi > 0.5:
                fuel_score = min(25, oi * 3) 
            elif side_str == "SHORT" and pr < -0.2 and oi > 0.5: # ✅ 修正：做空要看 pr < -0.2
                fuel_score = min(25, oi * 3)
            elif oi < -1.0:
                fuel_score = -10

            base_score = int(42 + (conf * 3.7) + fuel_score)

            # === V34 [上帝視角 2.0] 插件區 ===
        
            # A. 虛假火星過濾 (Fake Taker Filter)
            if d.get('taker_ratio', 1) > 5.0 and d.get('vol_spike', 1) < 1.0 and d.get('oi_5m', 0) < 0.2:
                base_score -= 20
                
            # B. 蓄勢加速器 (Loading Trigger)
            # 判斷是否為高品質建倉：OI 大量堆疊
            if d.get('oi_5m', 0) > 2.0 and d.get('oi_1h', 0) > 5.0:
                # 檢查 Taker 是否正在「甦醒」 (這需要 state_count 支持)
                if d.get('taker_ratio', 1) > 1.1 and d.get('state_count', 0) > 3:
                    base_score += 5 # 給予預判加分
                    
            # C. 莊家吸籌特赦 (針對 AVGO 案例)
            # 如果 OI 很高但 Taker 極低且價格微跌，這不是弱，是潛伏
            if d.get('oi_1h', 0) > 5.0 and d.get('taker_ratio', 1) < 0.3 and abs(d.get('pr_5m', 0)) < 0.5:
                base_score += 5 # 保持關注度，不要因為 Taker 低就把它踢出監控

            # === [Layer 2: 現代化插件] ===
            demon_bonus = 0
            if oi_4h > 20:
                demon_bonus = 10
                if oi_4h > 40: demon_bonus = 20

            persistence_bonus = 8 if state_count > 8 else 0

            anti_trap_trigger = False
            if side_str == "SHORT" and funding < -0.40 and pr > -0.5:
                anti_trap_trigger = True

            # --- 結構防禦 ---
            struct_penalty = 0
            if side_str == "SHORT" and "EXIT" in struct:
                if pr > 0: struct_penalty = -25 # 漲勢中離場結構禁空
            elif side_str == "LONG" and "COVER" in struct:
                if pr < 0: struct_penalty = -25 # 跌勢中回補結構禁多

            # === [V31 螢火獵手：持久度加成] ===
            scan_bonus = 0
            if state_count > 15: scan_bonus = 7
            if state_count > 30: scan_bonus = 12
            if state_count > 70: scan_bonus = 20
            
            # 特赦邏輯
            if state_count > 20 and abs(pr) > 1.5:
                struct_penalty = 0 
                scan_bonus += 10 

            # === [Step 5: 分數初步合成] ===
            final_phys = base_score + persistence_bonus + struct_penalty + scan_bonus + demon_bonus

            # === [Step 6: V30.3 核心扣分插件] ===
            if (side_str == "SHORT" and pr_1 > 0.1) or (side_str == "LONG" and pr_1 < -0.1):
                final_phys -= 20 # 逆勢熔斷
            if state == "CHOP":
                final_phys -= 15 # 震盪熔斷
            if final_phys > 92:
                final_phys -= 10 # 高分過熱熔斷

            # === [V33.4 邏輯校準：先組建細節，再判斷門禁] ===
            
            # 1. ✨ 先把基本的數據字典建好，防止 return 時找不到變數
            details = {
                "base": base_score,
                "entry_stage": "NONE",
                "structure": struct,
                "state": state,
                "o5": round(oi, 2),
                "tk": round(taker, 2),
                "fd": round(funding, 4),
                "sc": state_count,
                "oi_4h": round(oi_4h, 2),
                "pr1": round(pr_1, 2),
                "pr5": round(pr, 2),
                "vol": round(vol, 2),
                "shadow_active": v23_is_active
            }

            # 2. 🛡️ 門禁 A：極端費率一票否決 (VIC, SLX 慘案防禦)
            if (side_str == "SHORT" and funding < -0.50) or (side_str == "LONG" and funding > 0.50):
                # 這裡直接用剛剛建好的 details，並加上訊息
                return float(final_phys), {**details, "entry_stage": "NONE", "msg": "極端費率避讓"}

            # 3. 🛡️ 門禁 B：震盪市 + 離場結構 = 100% 禁止
            is_risky_struct = "EXIT" in struct or "COVER" in struct
            if state == "CHOP" and is_risky_struct:
                return float(final_phys), {**details, "entry_stage": "NONE", "msg": "震盪離場避讓"}

            # 4. 判定正式進場階段
            entry_stage = "NONE"
            if not anti_trap_trigger:
                # 雙核共振
                if v23_is_active and final_phys >= 82:
                    # 只有當費率沒有過熱時，才准許 ULTRA_RESONANCE
                    if abs(funding) < 0.05:
                        entry_stage = "ULTRA_RESONANCE" if state == "TREND" else "STAGE_1_RECON"
                    else:
                        entry_stage = "STAGE_1_RECON" # 費率稍高，降級處理
                
                # 影子波段
                elif v23_is_active and "BUILD" in struct:
                    if final_phys >= 74:
                        entry_stage = "V23_SMOOTH_TREND" if state == "TREND" else "STAGE_1_RECON"
                
                # 靈敏核
                else:
                    if "BUILD" in struct and state == "TREND":
                        if final_phys >= 87:
                            entry_stage = "STAGE_2_HEAVY"
                        elif final_phys >= 83:
                            entry_stage = "STAGE_1_RECON"
                    else:
                        if final_phys >= 88:
                            entry_stage = "STAGE_1_RECON"
            
            # === ✨ [V34 核心修正：動能一票否決制] ===
            # 只有當 entry_stage 已經決定要進場時，才進行最後的動能審核
            if entry_stage != "NONE":
                is_momentum_valid = False
                
                # 1. 例外：超級大資金進場 (OI_1h > 10.0)，無視動能直接進場 (抓慢牛)
                if d.get('oi_1h', 0) > 10.0:
                    is_momentum_valid = True
                
                # 2. 規則 A：確認有點火 (Taker_Ratio > 1.1)
                elif taker > 1.10:
                    is_momentum_valid = True
                    
                # 3. 規則 B：確認已突破 (Pr_5m > 2.5%)
                elif abs(pr) > 1.5:
                    is_momentum_valid = True
                
                # 如果以上三者都不符合，強制否決進場 (退回觀察區)
                if not is_momentum_valid:
                    # 這裡可以保留分數，但收回進場權限
                    entry_stage = "NONE"
                    details["msg"] = "動能否決: 無點火且無突破"

            # 終極過濾：波動太小
            if final_phys < 50 or abs(pr) < 0.25:
                entry_stage = "NONE"

            # 更新標籤並回傳
            details["entry_stage"] = entry_stage
            return float(final_phys), details

        except Exception as e:
            # 加上報錯追蹤，方便除錯
            import traceback
            print(f"❌ 評分引擎崩潰: {e}")
            traceback.print_exc()            
            return 0.0, {"base": 0, "entry_stage": "NONE", "structure": "CHOP","o5": 0, "tk": 0, "oi_4h": 0, "sc": 0, "pr1": 0}
       
     

    async def call_ai_v21(self, d, v, phys_score):
        """V21.1 AI 首席審計員"""
        is_coherent = (d.get('pr_5m', 0) * d.get('oi_5m', 0) > 0)
        prompt = f"""
[角色] Titan-Quant分析師。
[現況] 物理分:{phys_score}, 結構:{v['structure']}。
[數據] 5m價:{d['pr_5m']}%, 5mOI:{d['oi_5m']}%, 4hOI:{d['oi_4h']}%, Taker:{d['taker_ratio']}。
[規範] 嚴禁重讀RSI。分析莊家意圖(吸籌/洗盤/誘多)。
輸出JSON: {{"adjustment": -6~+3, "reason": "30字內理由"}}"""
        payload = {
            "model": MODEL_NAME, "messages": [{"role": "system", "content": "量化專家"}, {"role": "user", "content": prompt}],
            "temperature": 0.01, "response_format": {"type": "json_object"}
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(VLLM_URL, json=payload, timeout=30) as resp:
                    if resp.status == 200:
                        content = (await resp.json())['choices'][0]['message']['content']
                        parsed = parse_json_robustly(content)
                        if parsed: return {"adjustment": parsed.get('adjustment', 0), "reason": parsed.get('reason', '分析完成'), "decision": "GO"}
            return {"adjustment": 0, "reason": "AI超時", "decision": "GO"}
        except: return {"adjustment": -2, "reason": "AI異常", "decision": "GO"}

    async def process_ai_task(self, session, c, sem, score_threshold):
        """深度分析任務：整合物理分、AI、TG發報、CSV存檔"""
        target_s = c['symbol']
        
        # --- 0. 鋼鐵初始化 (變數必先定義) ---
        final_decision_stage = "NONE" # 🛑 放在這裡，絕對不會報 UnboundLocalError
        is_go = False
        final_score = 0
        sm_phys = 0
        sm_base = 0
        phys_score = 0
        ai_adj = 0
        details = {}
        ai_res = {"adjustment": 0, "reason": "N/A"}

        if target_s not in self.summary_data:
            self.summary_data[target_s].update({"start_price": c['p']})
        self.summary_data[target_s]["count"] += 1
        
        async with sem:
            try:
                # 1. 採集指標
                indicators = await self.fetch_klines_indicators(session, target_s)
                if not indicators: return None
                ob_ratio = await self.fetch_orderbook(session, target_s)

                # 2. 狀態計數
                curr_state = classify_market_state({**indicators, "pr_5m": c['p5'], "oi_5m": c['o5']})
                if target_s not in self.state_tracker: self.state_tracker[target_s] = {"state": "INIT", "count": 0}
                if curr_state == self.state_tracker[target_s]["state"]: self.state_tracker[target_s]["count"] += 1
                else: self.state_tracker[target_s] = {"state": curr_state, "count": 1}
                
                # 3. 計算歷史 OI 斜率
                h = c['h']
                oi_1h = round((h[0]['oi'] - h[min(len(h)-1, 60)]['oi']) / (h[min(len(h)-1, 60)]['oi'] + 0.001) * 100, 2)
                oi_4h = round((h[0]['oi'] - h[min(len(h)-1, 240)]['oi']) / (h[min(len(h)-1, 240)]['oi'] + 0.001) * 100, 2)

                # 4. 組裝數據
                data = {
                    **indicators, "symbol": target_s, "price": c['p'], 
                    "oi_5m": round(c['o5'], 2), "pr_5m": round(c['p5'], 2), 
                    "oi_1h": oi_1h, "oi_4h": oi_4h, "ob_ratio": ob_ratio, 
                    "funding": c.get('f', 0), "state_count": self.state_tracker[target_s]["count"], 
                    "state": curr_state
                }

                # 5. 判定物理結構與 Side
                v20_phys = detect_v20_physics(data)
                side = "LONG" if v20_phys['direction'] == 1 else "SHORT"
                struct_name = v20_phys.get('structure', 'CHOP')

                # 6. 雙核評分引擎 🚀
                sm_phys, sm_base = self.calculate_v23_smooth_score(data, side)
                phys_score, details = self.calculate_v25_composite_score(data, side, score_threshold, sm_phys)
                
                
                ai_res = await self.call_ai_v21(data, v20_phys, phys_score) 
                ai_adj = int(ai_res.get('adjustment', 0))
                final_score = round(max(0, min(100, phys_score + ai_adj)), 1)
                
                
                # 7. V32 雙核決策邏輯 ⚖️
                v31_stage = details.get("entry_stage", "NONE")
                v23_is_active = (sm_phys >= 72) # 影子門檻 72
                v31_final = final_score 
                
                # --- 核心判定流程 (先清空標籤再判定) ---
                final_decision_stage = "NONE" 
                
                if v31_final >= 55:
                    if v31_stage != "NONE" and v23_is_active:
                        final_decision_stage = "ULTRA_RESONANCE"
                    elif v23_is_active and v31_final >= 62:
                        final_decision_stage = "V23_SMOOTH_TREND"
                    elif v31_stage != "NONE":
                        final_decision_stage = v31_stage

                # === 🤖 [AI 審計員：純收值觀察模式] ===
                win_rate = self.auditor.predict_win_rate(data)
                
                # ✨ 這裡我們不修改 final_decision_stage，只做紀錄
                ai_advice = "PASS" # 預設 AI 沒意見
                if final_decision_stage != "NONE":
                    if win_rate < 0.55:
                        ai_advice = "WOULD_REJECT" # AI 說如果是它就會攔截
                    elif win_rate > 0.70:
                        ai_advice = "WOULD_UPGRADE" # AI 說如果是它就會加碼

                # 判定進場開關
                is_go = (final_decision_stage != "NONE")

                # 🚀 構造詳細日誌
                detailed_reason = (
                    f"[{final_decision_stage}] Score:{final_score}(Sm:{sm_phys}) | "
                    f"AI:{win_rate:.1%} ({ai_advice}) | "
                    f"o5:{details.get('o5', 0)} | tk:{details.get('tk', 0)} | "
                    f"oi4:{details.get('oi_4h', 0)} | fd:{details.get('fd', 0)} | "
                    f"sc:{data.get('state_count', 0)} | pr1:{details.get('pr1', 0)} | "
                    f"{struct_name} | {curr_state}"
                )

                
                # --- 8. 數據封裝 ---
                data.update(details) 
                
                data.update({
                    "ai_win_rate": round(win_rate, 4), # 存下預測勝率
                    "ai_advice": ai_advice,           # 存下 AI 的建議 (PASS/REJECT/UPGRADE)
                    "entry_stage": final_decision_stage,
                    "base_score": details.get("base", 0), # ✨ 修正：將 base 映射為 base_score
                    "final_score": final_score,
                    "sm_phys_score": sm_phys,
                    "sm_base_score": sm_base,
                    "decision": "GO" if is_go else "NO_GO",
                    "reason_metadata": detailed_reason,                     
                    "ai_adj": ai_adj, 
                    "side": side, 
                    "structure": struct_name,
                    "reason": ai_res.get('reason', 'N/A')
                })
                
                # --- 9. 存檔判定 (大撒網) ---
                if (data.get("base_score", 0) >= 50) or (sm_base >= 40):
                    self.log_to_csv(target_s, data, ai_res, v20_phys)
                                
                # --- 10. TG 報警判定 (多重信號標記) ---
                entry_stage = data.get('entry_stage', 'NONE')
                valid_stages = ["STAGE_1_RECON", "V23_SMOOTH_TREND", "ULTRA_RESONANCE"]

                # 只要分數 >= 75 或者 觸發了我們定義的三大策略，就發 TG
                if final_score >= 70 or entry_stage in valid_stages:
                    await self.send_tg_alert(target_s, ai_res, data, v20_phys)

                self.update_summary(target_s, data, v20_phys, final_score)
                return data

            except Exception as e:
                print(f"❌ {target_s} 深度分析核心崩潰: {e}")
                import traceback
                traceback.print_exc()
                return None

    def log_to_csv(self, symbol, d, ai, v):
        """將所有數據寫入 CSV，包含影子平滑分數"""
        try:
            log_file = CSV_FILE
            file_exists = os.path.isfile(log_file)
            
            # 建立 28 個數據欄位，確保精確對應
            row = [
                datetime.now().strftime('%H:%M:%S'),    # 1
                symbol,                                 # 2
                d.get('price', 0),                      # 3
                d.get('oi_5m', 0),                      # 4
                d.get('oi_1h', 0),                      # 5
                d.get('oi_4h', 0),                      # 6
                d.get('pr_5m', 0),                      # 7
                d.get('vol_spike', 0),                  # 8
                d.get('taker_ratio', 0),                # 9
                d.get('rsi', 50),                       # 10
                d.get('bb_pos', 0.5),                   # 11
                d.get('funding', 0),                    # 12
                d.get('ai_adj', 0),                     # 13
                d.get('base_score', 0),                 # 14
                d.get('phys_score', 0),                 # 15
                d.get('final_score', 0),                # 16
                d.get('sm_base_score', 0),              # 17
                d.get('sm_phys_score', 0),              # 18
                d.get('ai_win_rate', 0.5),              # 19 ✨ 新增：AI預測勝率
                d.get('ai_advice', 'PASS'),             # 20 ✨ 新增：AI建議標籤
                d.get('entry_stage', 'NONE'),           # 21
                d.get('decision', 'NO_GO'),             # 22
                v.get('structure', 'CHOP'),             # 23
                d.get('state_count', 0),                # 24
                str(ai.get('reason', 'N/A')).replace(',', ';'), # 25
                v.get('direction', 0),                  # 26
                'HOLD',                                 # 27
                d.get('reason_metadata', ''),           # 28
                1 if d.get('side') == 'LONG' else -1,   # 29
                'STAY'                                  # 30
            ]

            with open(log_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow([
                        'Time', 'Symbol', 'Price', 'OI_5m', 'OI_1h', 'OI_4h', 
                        'Pr_5m', 'Vol_Spike', 'Taker_Ratio', 'RSI', 'BB_Pos', 'Funding', 
                        'AI_Adj', 'Base_Score', 'Phys_Score', 'Final_Score',
                        'Sm_Base', 'Sm_Phys', 'AI_Win_Rate', 'AI_Advice', # ✨ 這裡也要補上標題
                        'Stage', 'Decision', 'Structure', 'State_Count', 'Reason', 'Dir', 'Result_15m',
                        'Reason_Metadata', 'Side_Num', 'Hold_Status'
                    ])
                writer.writerow(row)
        except Exception as e:
            print(f"❌ CSV 寫入失敗: {e}")

    async def main_loop(self):
        """Titan-Quant V25.5 旗艦版主循環：不縮水全功能版"""
        sem = asyncio.Semaphore(5)
        while True:
            # 0. 對齊啟動
            await asyncio.sleep(60 - (time.time() % 60))
            now_str = datetime.now().strftime('%H:%M:%S')
            
            # 1. 自進化大腦與摘要
            if time.time() - self.last_summary_time >= 900:
                try:
                    self.config = build_self_evolving_config(CSV_FILE)
                    await self.send_15m_summary()
                    self.update_stats()
                    self.summary_data.clear()
                    self.last_summary_time = time.time()
                except: pass

            async with aiohttp.ClientSession() as session:
                # 2. 獲取行情
                try:
                    async with session.get("https://fapi.binance.com/fapi/v1/premiumIndex", timeout=10) as resp:
                        p_data = await resp.json()
                        # 確保兼容 markPrice 和 mark_p
                        self.current_p_map = {
                            i['symbol']: (float(i.get('markPrice', i.get('mark_p', 0))), float(i['lastFundingRate'])*100) 
                            for i in p_data if 'symbol' in i
                        }
                except Exception as e:
                    print(f"⚠️ 行情解析失敗: {e}")
                    continue

                # 3. 📊 [豪華儀表板]
                try:
                    total_margin = sum(p['usdt_margin'] for p in self.broker.positions.values())
                    floating_pnl = sum(((self.current_p_map.get(s, (p['entry_price'],0))[0] - p['entry_price']) * p['qty'] * (1 if p['side']=="LONG" else -1)) for s, p in self.broker.positions.items())
                    equity = self.broker.balance + total_margin + floating_pnl
                    loss_streak = getattr(self.broker, "loss_streak", 0)
                    
                    print(f"""
                    [{now_str}] 🔱 Titan-Quant V25.5 [燃料主導模式]
                    ======================================================
                    💰 初始資金: {CONFIG['initial_balance']} USDT | 💎 當前總權益: {equity:.2f} USDT
                    💵 可用餘額: {self.broker.balance:.2f} USDT | 🌊 浮動盈虧: {floating_pnl:+.2f} USDT
                    📦 在途保證金: {total_margin:.2f} USDT | 🛡️ 連敗狀態: {loss_streak}
                    📈 當前持倉數: {len(self.broker.positions)} / {CONFIG['max_positions']}
                    ======================================================""")
                except: pass

                # 4. 掃描全市場標的
                candidates = []
                for s in self.symbols:
                    try:
                        async with session.get(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={s}", timeout=5) as resp:
                            oi_cur = float((await resp.json())['openInterest'])
                        p_cur, f_rate = self.current_p_map.get(s, (0, 0))
                        
                        r.lpush(f"v5:h:{s}", json.dumps({"p": p_cur, "oi": oi_cur, "ts": int(time.time())}))
                        r.ltrim(f"v5:h:{s}", 0, 240)
                        hist = [json.loads(x) for x in r.lrange(f"v5:h:{s}", 0, 240)]
                        if len(hist) < 6: continue
                        
                        oi_5m = (hist[0]['oi'] - hist[5]['oi']) / (hist[5]['oi'] + 0.001) * 100
                        pr_5m = (hist[0]['p'] - hist[5]['p']) / (hist[5]['p'] + 0.0001) * 100
                        
                        # V25 鬆動過濾器：找回那消失的 60%
                        if abs(oi_5m) > 0.8 or abs(pr_5m) > 0.35:
                            candidates.append({
                                "symbol": s, "p": p_cur, "f": f_rate, "o5": oi_5m, "p5": pr_5m, 
                                "h": hist, "score": abs(oi_5m) + abs(pr_5m) * 2
                            })
                    except: continue
                
                # 5. 熱度判別
                top = sorted(candidates, key=lambda x: x['score'], reverse=True)[:18]
                top_syms = set([c['symbol'] for c in top])
                if top:
                    m_heat = sum(abs(c['p5']) for c in top[:10]) / 10
                    market_mode = "TREND" if m_heat > 0.45 else "RANGE"
                    
                    # ✨ 從 CONFIG 讀取門檻，不再寫死 68
                    if market_mode == "TREND":
                        score_threshold = CONFIG.get("trend_threshold", 75)
                    else:
                        score_threshold = CONFIG.get("range_threshold", 83)
                        
                    print(f"🌡️ 市場熱度: {m_heat:.2f} | 模式: {market_mode} | 基準門檻: {score_threshold}")
                else: 
                    score_threshold = CONFIG.get("range_threshold", 83)

                # 6. 持倉動能退場監控
                for sym in list(self.broker.positions.keys()):
                    if sym in self.current_p_map:
                        self.broker.update_positions(sym, self.current_p_map[sym][0], sym in top_syms)

                # 7. 選拔進場 (V31.9 最終加固版)
                if top:
                    # 💡 【核心修正 1】先對 top 進行幣種去重，防止同一次循環重複處理
                    seen_top = set()
                    unique_top = []
                    for c in top:
                        sym = c['symbol']
                        if sym not in seen_top:
                            unique_top.append(c)
                            seen_top.add(sym)

                    # 💡 【核心修正 2】過濾正在持倉或鎖定中的幣
                    candidates = [c for c in unique_top if c['symbol'] not in self.broker.positions 
                                  and c['symbol'] not in self.processing_symbols]
                    
                    if not candidates: continue

                    # 在進入 AI 任務前，先將這些幣種全部鎖定
                    for c in candidates:
                        self.processing_symbols.add(c['symbol'])

                    try:
                        # 並行處理 AI 任務
                        results = await asyncio.gather(*[self.process_ai_task(session, c, sem, score_threshold) for c in candidates])
                        
                        # 篩選有效訊號
                        potential = [r for r in results if r and (r.get('decision') == 'GO' or r.get('entry_stage') != 'NONE')]
                        
                        if potential:
                            ranked = sorted(potential, key=lambda x: x['final_score'], reverse=True)
                            new_trades = 0
                            
                            for sig in ranked:
                                s, score, stage = sig['symbol'], sig['final_score'], sig.get('entry_stage', 'NONE')
                                
                                # 🛡️ 第三道防線：下單前最終確認（防止 AI 任務處理期間已經開了倉）
                                if s in self.broker.positions:
                                    continue

                                if new_trades >= 2: break 

                                # 🛡️ 冷卻檢查
                                if datetime.now() <= self.cooldowns[s]:
                                    continue

                                # 🛡️ 持倉上限與末位淘汰
                                current_pos_count = len(self.broker.positions)
                                max_p = CONFIG.get("max_positions", 15)
                                can_open = True
                                if current_pos_count >= max_p:
                                    if stage == "STAGE_2_HEAVY" and score >= 85:
                                        worst = self.broker.get_worst_position()
                                        if worst:
                                            p_data = self.current_p_map.get(worst, [sig['price']])
                                            self.broker.close_position(worst, p_data[0], "REPLACED")
                                        else: can_open = False
                                    else: can_open = False
                                
                                if not can_open: continue

                                # 🚀 正式執行 Broker 下單
                                try:
                                    self.broker.open_position(s, sig['side'], sig['price'], score, metadata=sig)
                                    self.cooldowns[s] = datetime.now() + timedelta(minutes=CONFIG["cooldown_minutes"])
                                    new_trades += 1
                                except Exception as e:
                                    print(f"❌ {s} 下單過程異常: {e}")

                    finally:
                        # 💡 核心解鎖邏輯：
                        # 任務全跑完後，將 candidates 裡的所有幣種解鎖
                        # 這樣下一分鐘的循環才能再次掃描它們
                        for c in candidates:
                            self.processing_symbols.discard(c['symbol'])

    def update_summary(self, symbol, d, v, final_score):
        """同步更新統計摘要數據"""
        try:
            s = self.summary_data[symbol]
            p_cur = d.get('price', 0)
            if s["start_price"] == 0: s["start_price"] = p_cur
            
            # 修正影子分數獲取
            sm_phys = d.get('sm_phys_score', 0)
            sm_base = d.get('sm_base_score', 0)
            ai_wr = d.get('ai_win_rate', 0.5)    # ✅ 定義 ai_wr
            ai_adv = d.get('ai_advice', 'PASS') # ✅ 定義 ai_adv

            s.update({
                "total_change": round(((p_cur/s["start_price"])-1)*100, 2),
                "max_oi": max(s.get("max_oi", 0), d.get("oi_5m", 0)),
                "max_taker": max(s.get("max_taker", 1.0), d.get("taker_ratio", 1.0)),
                "max_vol": max(s.get("max_vol", 1.0), d.get("vol_spike", 1.0)),
                "max_rsi": max(s.get("max_rsi", 50), d.get("rsi", 50)),
                "max_base": max(s.get("max_base", 0), d.get("base_score", 0)),
                "max_phys": max(s.get("max_phys", 0), d.get("phys_score", 0)),

                "sm_max_base": max(s.get("sm_max_base", 0), sm_base),
                "sm_max_phys": max(s.get("sm_max_phys", 0), sm_phys),
                "max_ai_win_rate": max(s.get("max_ai_win_rate", 0), ai_wr), # 修正：抓取 AI 勝率極值
                "last_ai_advice": ai_adv,                                  # 修正：記錄最新一次 AI 建議


                "max_final": max(s.get("max_final", 0), final_score),
                "last_scene": v.get("structure", "CHOP"),
                "last_reason": d.get("reason", "N/A"), # 👈 這裡補上逗號
                
            })
        except Exception as e:
            print(f"⚠️ {symbol} 摘要更新失敗: {e}")

    def update_stats(self):
        """自進化大腦勝率地圖更新"""
        try:
            df = pd.read_csv(CSV_FILE)
            df_done = df[df['Result_15m'].isin(['TP', 'SL'])].copy()
            if len(df_done) >= 15:
                df_done['is_win'] = df_done['Result_15m'] == 'TP'
                self.win_rates = df_done.groupby('Structure')['is_win'].mean().to_dict()
                self.brain.update_winrates(self.win_rates)
        except: pass

    async def send_tg_alert(self, symbol, ai, d, v):
        """V34.5 雙模式旗艦級 + AI 審計員聯動報警格式 (修復邏輯衝突版)"""
        stage = d.get('entry_stage', 'NONE')
        f_score = d.get('final_score', 0)
        ai_prob = d.get('ai_win_rate', 0.5)
        sm_phys = d.get('sm_phys_score', 0)
        
        # --- 1. 判定標題 (Stage Text) - 確保只有一個邏輯出口 ---
        if stage == "STAGE_1_RECON":
            stage_text = "🕵️【STAGE-1 潛伏偵察】"
        elif stage == "V23_SMOOTH_TREND":
            stage_text = "🌊【V23 平滑趨勢】"
        elif stage == "ULTRA_RESONANCE":
            stage_text = "⚡【ULTRA 極限共振】"
        elif stage == "STAGE_2_HEAVY":
            stage_text = "🚀【STAGE-2 重倉出擊】"
        else:
            # 💡 只有當 stage == "NONE" 時，才進來細分「為什麼值得觀察」
            if ai_prob > 0.65:
                stage_text = "🤖【AI 高勝率觀察】"
            elif sm_phys > 75:
                stage_text = "🎯【影子蓄勢觀察】"
            elif f_score >= 70:
                stage_text = "⚠️【高分異動觀察】"
            else:
                stage_text = "🔍【潛力幣追蹤】"

        # --- 2. 獲取 AI 建議標籤 (Emoji 視覺化) ---
        ai_advice = d.get('ai_advice', 'PASS')
        if ai_advice == "WOULD_REJECT":
            ai_tag = f"🛑 <b>AI攔截建議</b> ({ai_prob:.1%})"
        elif ai_advice == "WOULD_UPGRADE":
            ai_tag = f"🚀 <b>AI加碼建議</b> ({ai_prob:.1%})"
        else:
            ai_tag = f"🤖 <b>AI勝率預估</b> ({ai_prob:.1%})"

        side_emoji = "🟢 LONG" if v.get('direction', 1) == 1 else "🔴 SHORT"
        
        try:
             # 全面使用 .get() 防呆，避免 KeyError 導致 TG 傳送失敗
            p = d.get('price', 0)
            f_score = d.get('final_score', 0)
            b_score = d.get('base_score', 0)
            sm_base_score = d.get('sm_base_score', 0)
            sm_phys_score = d.get('sm_phys_score', 0)
            oi_4h = d.get('oi_4h', 0)
            ai_adj = d.get('ai_adj', 0)
            struct = v.get('structure', 'N/A')
            pr_5m = d.get('pr_5m', 0)
            oi_5m = d.get('oi_5m', 0)
            oi_1h = d.get('oi_1h', 0)
            tk_ratio = d.get('taker_ratio', 1.0)
            vol_spike = d.get('vol_spike', 1.0)
            rsi = d.get('rsi', 50)
            bb_pos = d.get('bb_pos', 0.5)
            reason = d.get('reason', 'N/A')

            msg = (
                f"<b>{stage_text}</b>\n"
                f"<b>{symbol} {side_emoji}</b>\n"
                f"🏆 <b>綜合總分：{d['final_score']}</b> (底座:{d['base_score']} 燃料:{d['oi_4h']}%)\n"
                f"🎯<b>影子評分：{d['sm_phys_score']}</b> (底座:{d['sm_base_score']}\n"
                f"{ai_tag}\n"  # ✨ 這裡插入 AI 審計數據
                f"🛡️└ AI修正：<code>{d['ai_adj']:+d}</code> | 結構: {v.get('structure')}\n"                
                f"━━━━━━━━━━━━━━\n"
                f"💰 <b>價格:</b> <code>{p}</code> ({d.get('pr_5m',0):+.2f}%)\n"
                f"📊 <b>燃料 (5m/1h/4h):</b> {d['oi_5m']}% / {d['oi_1h']}% / {d['oi_4h']}%\n"
                f"⚔️ <b>Taker:</b> {d['taker_ratio']} | <b>Vol:</b> {d['vol_spike']}x\n"
                f"📏 <b>RSI:</b> {d['rsi']} | <b>BB:</b> {d['bb_pos']}\n"
                f"━━━━━━━━━━━━━━\n"
                f"💡 <b>分析:</b> <i>{d.get('reason','N/A')}</i>"
            )
            await self.send_tg_msg(msg)
        except Exception as e:
            # 絕對不要用 pass！把錯誤印出來我們才知道缺了什麼欄位
            print(f"❌ TG 發送失敗 [{symbol}]: {e}")

    async def send_tg_msg(self, text):
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(url, json={"chat_id": TG_CHAT_ID, "message_thread_id": TG_THREAD_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        except: pass

    async def send_15m_summary(self):
        """發送 15 分鐘裁判摘要"""
        sorted_list = sorted(self.summary_data.items(), key=lambda x: x[1].get("max_final", 0), reverse=True)[:15]
        if not sorted_list: return
        try:
            with open(SUMMARY_CSV, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                for sym, s in self.summary_data.items():
                    if s["count"] > 0:
                        writer.writerow([datetime.now().strftime('%H:%M:%S'), sym, s["count"], s["total_change"], s["max_oi"], s["max_taker"], s["max_vol"], s["max_rsi"], s["max_bb"], s["max_base"], s["max_phys"], s["max_final"], s["last_scene"], s["last_reason"]])
            
            lines = [f"📊 **Titan-Quant V25.5 15min 摘要看板**\n"]
            for sym, s in sorted_list:
                lines.append(f"🔹 **{sym}** (物:{s['max_phys']}|終:{s['max_final']})\n📈 幅:{s['total_change']:+.2f}% | OI:{s['max_oi']:.1f}% | T:{s['max_taker']:.2f}\n💡 {s['last_reason'][:30]}...\n---")
            await self.send_tg_msg("\n".join(lines))
        except: pass

    async def run(self):
        print("🔄 正在初始化 V25.5 系統...")
        await self.get_top_symbols(); self.update_stats()
        await asyncio.gather(self.main_loop(), self.labeler_task())

    async def labeler_task(self):
        """後台標註器：每 15 分鐘自動判定交易結果"""
        while True:
            await asyncio.sleep(60)
            if not os.path.exists(CSV_FILE): continue
            try:
                df = pd.read_csv(CSV_FILE)
                df['Result_15m'] = df['Result_15m'].astype(object) 
                
                mask = (df['Final_Score'] >= 45) & (df['Result_15m'].isna())
                if not mask.any(): continue
                async with aiohttp.ClientSession() as session:
                    for idx, row in df[mask].iterrows():
                        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={row['Symbol']}&interval=1m&limit=15"
                        async with session.get(url) as resp:
                            kl = await resp.json()
                            highs, lows = [float(k[2]) for k in kl], [float(k[3]) for k in kl]
                            entry_p = float(row['Price'])
                            tp, sl = (entry_p*1.035, entry_p*0.985) if row['Dir']==1 else (entry_p*0.965, entry_p*1.015)
                            res = "TP" if (max(highs)>=tp if row['Dir']==1 else min(lows)<=tp) else ("SL" if (min(lows)<=sl if row['Dir']==1 else max(highs)>=sl) else "HOLD")
                            df.at[idx, 'Result_15m'] = res
                df.to_csv(CSV_FILE, index=False)
            except: pass

if __name__ == "__main__":
    asyncio.run(TitanV17().run())