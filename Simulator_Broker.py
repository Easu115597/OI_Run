import csv
import os
import json
import time
from datetime import datetime, timedelta
from config import CONFIG

try:
    from config import CONFIG, STAGE_SETTINGS
except ImportError:
    # 防止有些舊檔案引用方式不同
    import config
    CONFIG = config.CONFIG
    STAGE_SETTINGS = config.STAGE_SETTINGS
    
# ✨ 自動對齊設定，確保全局唯一
POS_FILE = CONFIG["pos_json"]
LOG_FILE = CONFIG["trade_log_csv"]

class SimulatorBroker:
    def __init__(self, initial_balance=2000):
        # 1. 資金接續 (修正：只保留一個乾淨的加載邏輯)
        self.balance = self._load_last_balance(initial_balance)
        # 2. 持倉接續
        self.positions = self._load_positions()
        
        self.leverage = CONFIG.get("leverage", 5)
        #self.refill_cooldown = None 
        self.loss_streak = 0  
        self.trade_cooldowns = {} 
        self.symbol_loss_streak = {} 
        self.direction_cooldowns = {} # ✨ 新增：確保類別內有定義

        self._init_log()
        print(f"💰 模擬器啟動 | 餘額: {self.balance:.2f} | 持倉: {len(self.positions)} | 對齊版本: {CONFIG['version']}")

    def _load_last_balance(self, default_balance):
        """從當前版本的 CSV 帳本讀取最後一次平倉後的 Balance"""
        if not os.path.exists(LOG_FILE): 
            return default_balance
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                reader = list(csv.reader(f))
                if len(reader) <= 1: return default_balance
                header = reader[0]
                if 'Balance' in header:
                    idx = header.index('Balance')
                    # 抓取最後一行的餘額
                    return float(reader[-1][idx])
        except Exception as e:
            print(f"⚠️ 讀取歷史餘額失敗: {e}")
        return default_balance

    def _load_positions(self):
        if os.path.exists(POS_FILE):
            try:
                with open(POS_FILE, 'r') as f: return json.load(f)
            except: return {}
        return {}

    def _save_positions(self):
        with open(POS_FILE, 'w') as f: json.dump(self.positions, f)

    def _init_log(self):
        if not os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Time', 'Event', 'Symbol', 'Side', 'Price', 'Margin', 'PnL_USDT', 'PnL_Pct', 'Balance', 'Reason_Metadata'])

    def log_trade(self, event, symbol, side, price, margin=0, pnl=0, pnl_pct=0, reason="", extra_data=None):
        """
        修正版：確保「上帝視角」數據在寫入 CSV 前就被正確構造
        """
        # 1. ✨ 優先構造「詳細理由 (detailed_reason)」
        if extra_data:
            # 從 metadata 裡提取我們在評分引擎裡塞進去的所有原始數據
            score = extra_data.get('final_score', 0)
            stage = extra_data.get('entry_stage', 'NONE')
            sm = extra_data.get('sm_phys_score', 0)
            ai_wr = extra_data.get('ai_win_rate', 0.5)
            ai_adv = extra_data.get('ai_advice', 'PASS')

            o5 = extra_data.get('o5', 0)      # 5m OI
            tk = extra_data.get('tk', 0)      # Taker Ratio
            fd = extra_data.get('fd', 0)      # Funding
            sc = extra_data.get('sc', 0)      # Scan Count
            oi4 = extra_data.get('oi_4h', 0)  # 4h OI
            struct = extra_data.get('structure', 'CHOP')
            state = extra_data.get('state', 'TREND')
            
            # 重新構造一條最強大的訊息
            # 格式：Score:81 [STAGE_1 | o5:2.1 | tk:1.5 | oi4:12.5 | fd:0.01 | sc:45 | BUILD | TREND]
            # 格式範例：Score:81(Sm:74) [STAGE_1 | o5:2.1 | tk:1.5 | oi4:12.5 | ...]
            detailed_info = (
                    f"Score:{score}(Sm:{sm}) AI:{ai_wr*100:.1f}%({ai_adv}) "
                    f"[{stage} | o5:{o5} | tk:{tk} | oi4:{oi4} | fd:{fd} | sc:{sc} | {struct} | {state}]"
                )
        else:
            # 如果沒有 extra_data (通常是 CLOSE 事件)，就用傳進來的理由 (如 SL_HIT)
            detailed_info = reason

        # 2. ✨ 構造 CSV 寫入列 (現在 detailed_info 已經準備好了)
        row = [
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), # 時間
            event,                                        # 事件 (OPEN/CLOSE)
            symbol,                                       # 幣種
            side,                                         # 方向
            f"{price:.8f}",                               # 價格
            round(margin, 2),                             # 金額
            round(pnl, 2),                                # 盈虧 U
            f"{round(pnl_pct*100, 2)}%",                   # 盈虧 %
            round(self.balance, 2),                       # 餘額
            detailed_info                                 # ✨ 關鍵：這裡寫入上帝視角字串
        ]

        # 3. 💾 執行寫入
        try:
            # 確保 LOG_FILE 對應你的檔名 (如 oi_sim_trades_v31.csv)
            with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow(row)
        except Exception as e:
            print(f"❌ [帳本寫入失敗] {e}")

    def check_and_refill(self, min_required):
        """餘額不足自動補款"""
        if self.balance < min_required:
            now = datetime.now()
            if self.refill_cooldown is None:
                self.refill_cooldown = now + timedelta(minutes=5)
                print(f"🚨 [資金枯竭] 餘額 {self.balance:.2f} 低於 {min_required}！將於 5 分鐘後補款")
                return False
            if now >= self.refill_cooldown:
                self.balance = 2000.0  
                self.refill_cooldown = None 
                self.log_trade("REFILL", "SYSTEM", "NONE", 0, reason="自動重置資金")
                return True
            return False
        self.refill_cooldown = None
        return True
    
    def open_position(self, symbol, side, price, final_score, metadata=None):
        # 1. 基礎檢查
        if symbol in self.positions: return
        
        metadata = metadata or {}
        stage = metadata.get('entry_stage', 'NONE')
        
        # === 💰 【防禦性配置讀取】 ===
        # 先拿整張表
        all_settings = CONFIG.get("STAGE_SETTINGS", {})
        
        # 建立一個絕對安全的後備方案 (Fallback)
        absolute_default = {"margin": 20, "tp": 0.03, "sl": 0.012}
        
        # 優先級：1. 對應的 stage -> 2. 配置中的 DEFAULT -> 3. 程式內建的絕對後備
        stage_cfg = all_settings.get(stage, all_settings.get("DEFAULT", absolute_default))
        
        # 💡 現在這裡絕對不會是 None 了
        req_amount = stage_cfg.get("margin", 20)
        target_tp_pct = stage_cfg.get("tp", 0.03)
        target_sl_pct = stage_cfg.get("sl", 0.012)

        # === 🛑 門禁與末位淘汰 ===
        max_p = CONFIG.get("max_positions", 15)
        if len(self.positions) >= max_p:
            if final_score >= 88:
                worst_sym = self.get_worst_position()
                if worst_sym:
                    print(f"♻️ [末位淘汰] 為了 {symbol}({final_score}分) 踢掉 {worst_sym}")
                    self.close_position(worst_sym, self.positions[worst_sym]['entry_price'], "REPLACED")
                else: return
            else: return

        # === 💰 資金檢查 ===
        if not self.check_and_refill(req_amount): return
        if self.balance < req_amount: return

        # === 🚀 執行開倉 ===
        self.balance -= req_amount
        
        # 標籤繼承
        is_high_scan = metadata.get('is_high_scan', False)
        is_demon = metadata.get('is_demon', False)
        
        # 止損寬度計算：偵察兵嚴守 0.7%，其餘妖幣放寬至 1.8%
        final_sl_pct = target_sl_pct
        if is_high_scan and "RECON" not in stage:
            final_sl_pct = max(target_sl_pct, 0.018)
        
        # --- 這裡只賦值一次！包含所有需要的標籤 ---
        self.positions[symbol] = {
            "side": side,
            "entry_price": price,
            "entry_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "usdt_margin": req_amount, 
            "qty": (req_amount * self.leverage) / price,
            # ✨ 關鍵：每筆單子的止損/止盈都是獨立且分級的
            "stop_loss": price * (1 - final_sl_pct if side=="LONG" else 1 + final_sl_pct),
            "take_profit_target": target_tp_pct, # ✨ 把止盈目標存進去，供 update_positions 使用
            "highest_pnl": 0.0,
            "last_momentum_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "is_high_scan": is_high_scan, # 妖幣標籤
            "is_demon": is_demon,         # 燃料標籤
            "entry_stage": stage,
            "entry_score": final_score
        }
        
        # ✨ 關鍵修正：從 metadata 裡抓取我們準備好的「上帝視角字串」
        # 如果抓不到，就用預設的 Score 理由
        detailed_reason = metadata.get('reason_metadata', f"Score:{final_score}")

        # 💾 [寫入日誌]
        # 這裡把詳細字串傳給 log_trade 的 reason 參數
        self.log_trade(
            event="OPEN", 
            symbol=symbol, 
            side=side, 
            price=price, 
            margin=req_amount, 
            reason=detailed_reason, # 👈 傳入上帝視角字串
            extra_data=metadata      # 👈 傳入原始數據供 log_trade 內部解析
        )


        # 6. [日誌與紀錄]
        self.log_trade("OPEN", symbol, side, price, margin=req_amount, reason=f"Score:{final_score}", extra_data=metadata)
        self._save_positions()
        
        # 7. 打印結果
        tag_str = "🔥妖幣" if is_high_scan else "🛡️普通"
        print(f"💰 [{stage}][{tag_str}] {symbol} {side} ({final_score}分) | 金額: {req_amount}U | 餘額: {self.balance:.2f} (持倉:{len(self.positions)}/{max_p})")

    def update_positions(self, symbol, current_price, is_still_candidate):
        if symbol not in self.positions: return
        pos = self.positions[symbol]
        stage = pos.get('entry_stage', 'DEFAULT')
        
        # 從 CONFIG 獲取分級設定 (確保對齊你的 STAGE_SETTINGS)
        all_stg = CONFIG.get("STAGE_SETTINGS", {})
        stg = all_stg.get(stage, all_stg.get("DEFAULT", {"margin": 20, "tp": 0.03, "sl": 0.012}))
        
        # 1. 🛡️ 盈虧計算 (做空獲利為正)
        if pos['side'] == "LONG":
            pnl_pct = (current_price - pos['entry_price']) / pos['entry_price']
        else:
            pnl_pct = (pos['entry_price'] - current_price) / pos['entry_price']
        
        # 更新最高盈虧紀錄
        pos['highest_pnl'] = max(pos.get('highest_pnl', 0.0), pnl_pct)

        # 2. 持時計算
        entry_t = pos['entry_time'] if isinstance(pos['entry_time'], datetime) else datetime.strptime(pos['entry_time'], '%Y-%m-%d %H:%M:%S')
        hold_min = (datetime.now() - entry_t).total_seconds() / 60
        
        # 顯示監控狀態 (加入更豐富的圖示)
        mode_icon = "🕵️" if "RECON" in stage else ("⚡" if "RESONANCE" in stage else "🔱")
        scan_tag = "🔥" if pos.get('is_high_scan') else "🛡️"
        color = "🟢" if pnl_pct > 0 else "🔴"
        print(f"⌛ [監控]{mode_icon}{symbol}({stage}){scan_tag} | {color} PnL: {pnl_pct*100:.2f}% | 最高: {pos['highest_pnl']*100:.1f}% | 持時: {int(hold_min)}m")

        # === 🚀 V35 利潤保護模組 ===

        # A. 【閃電保本】(針對 BTW 案例)
        # 如果是強共振單，且獲利曾達到 0.6%，立刻啟動保本位 (進場價 +0.1%)
        if "RESONANCE" in stage and pos['highest_pnl'] >= 0.006:
            be_margin = 0.001 # 鎖定微利 0.1% 覆蓋手續費
            if pos['side'] == "LONG":
                new_sl = pos['entry_price'] * (1 + be_margin)
                if pos['stop_loss'] < new_sl:
                    pos['stop_loss'] = new_sl
                    print(f"⚡ [閃電保本] {symbol} 強力脫離成本區，已鎖定保本位。")
            else:
                new_sl = pos['entry_price'] * (1 - be_margin)
                if pos['stop_loss'] > new_sl:
                    pos['stop_loss'] = new_sl
                    print(f"⚡ [閃電保本] {symbol} 強力脫離成本區，已鎖定保本位。")

        # B. 【常規二段式利潤保護】(V32.9 繼承版)
        be_trigger = stg.get('tp', 0.03) * 0.4  # 獲利達到目標 40% 時啟動
        if pos['highest_pnl'] >= max(be_trigger, CONFIG.get("breakeven_trigger", 0.012)):
            be_margin = CONFIG.get("breakeven_profit", 0.003) 
            if pos['side'] == "LONG":
                pos['stop_loss'] = max(pos['stop_loss'], pos['entry_price'] * (1 + be_margin))
            else:
                pos['stop_loss'] = min(pos['stop_loss'], pos['entry_price'] * (1 - be_margin))

        # C. 【強力鎖利】(當獲利大於全域設定時)
        if pos['highest_pnl'] >= CONFIG.get("profit_lock_trigger", 0.025):
            lock_margin = CONFIG.get("profit_lock_pct", 0.015)
            if pos['side'] == "LONG":
                pos['stop_loss'] = max(pos['stop_loss'], pos['entry_price'] * (1 + lock_margin))
            else:
                pos['stop_loss'] = min(pos['stop_loss'], pos['entry_price'] * (1 - lock_margin))

        # === 🏁 出場判定 ===

        # 1. 🛑 止損判定 (優先級最高)
        # 判定是否觸發止損價 (由 open_position 設定的動態 SL)
        is_sl_hit = (pos['side'] == "LONG" and current_price <= pos['stop_loss']) or \
                    (pos['side'] == "SHORT" and current_price >= pos['stop_loss'])
        
        if is_sl_hit:
            self.close_position(symbol, current_price, f"SL_HIT_{stage}")
            return

        # 2. 🎯 止盈判定
        target_tp = stg.get('tp', 0.03)
        if pnl_pct >= target_tp:
            self.close_position(symbol, current_price, f"TP_MAX_{stage}")
            return

        # 3. ⏳ 殭屍持倉清理 (針對 MSTR 案例)
        # 如果持有超過 120 分鐘，且盈虧在 +/- 0.8% 之間晃盪，且不在前台榜單，強制退出
        if hold_min > 120 and abs(pnl_pct) < 0.008 and not is_still_candidate:
            self.close_position(symbol, current_price, "TIME_STAGNATION_EXIT")
            return

        # 4. 📉 動能衰竭判定
        if is_still_candidate:
            pos['last_momentum_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        lmt_str = pos.get('last_momentum_time', pos['entry_time'])
        lmt = datetime.strptime(lmt_str, '%Y-%m-%d %H:%M:%S') if isinstance(lmt_str, str) else lmt_str
        idle_min = (datetime.now() - lmt).total_seconds() / 60

        # 【偵察兵特有】微利保衛：獲利中且長時間沒動靜，落袋為安
        if "RECON" in stage and pnl_pct > 0.005 and idle_min > 10:
            self.close_position(symbol, current_price, "RECON_PROFIT_PROTECT")
            return

        # 全局超時判定
        if hold_min > 25 and idle_min > CONFIG.get("momentum_idle_min", 15):
            self.close_position(symbol, current_price, "MOMENTUM_DECAY")
        elif hold_min > CONFIG.get("max_hold_min", 240):
            self.close_position(symbol, current_price, "TIMEOUT_EXIT")
    
    def close_position(self, symbol, price, reason):
        if symbol not in self.positions: return
        pos = self.positions[symbol]
        
        pnl_usdt = (price - pos['entry_price']) * pos['qty'] * (1 if pos['side'] == "LONG" else -1)
        
        # 抗磨損機制
        if reason in ["REPLACED", "MOMENTUM_DECAY"]:
            if pnl_usdt < (pos['usdt_margin'] * CONFIG.get("noise_filter_pnl", 0.002)): return 

        # 單幣熔斷與冷卻邏輯
        if pnl_usdt < 0:
            self.symbol_loss_streak[symbol] = self.symbol_loss_streak.get(symbol, 0) + 1
            if self.symbol_loss_streak[symbol] >= 2:
                self.trade_cooldowns[symbol] = datetime.now() + timedelta(minutes=240)
                self.symbol_loss_streak[symbol] = 0
                print(f"🚨 [單幣熔斷] {symbol} 強制冷凍 60 分鐘")
        else:
            self.symbol_loss_streak[symbol] = 0

        # 方向冷卻
        if pnl_usdt < 0 or reason in ["SL_HIT", "REPLACED"]:
            self.direction_cooldowns[symbol] = {
                "side": pos['side'], "until": datetime.now() + timedelta(minutes=CONFIG.get("cooldown_minutes", 12))
            }

        # 正式結算
        self.positions.pop(symbol)
        self.balance += (pos['usdt_margin'] + pnl_usdt)
        self.loss_streak = (self.loss_streak + 1) if pnl_usdt < 0 else 0
        
        self.log_trade("CLOSE", symbol, pos['side'], price, pnl=pnl_usdt, 
                       pnl_pct=pnl_usdt/pos['usdt_margin']/self.leverage, reason=reason)
        self._save_positions()
        
        print(f"🏁 [平倉] {symbol} {reason} | 盈虧: {pnl_usdt:+.2f}U | 餘額: {self.balance:.2f} | 連敗:{self.loss_streak}")

    def get_worst_position(self):
        if not self.positions: return None
        worst_sym, worst_score = None, 9999.0
        for sym, pos in self.positions.items():
            pnl = pos.get('highest_pnl', 0) * 100
            entry_t = datetime.strptime(pos['entry_time'], '%Y-%m-%d %H:%M:%S')
            hold_min = (datetime.now() - entry_t).total_seconds() / 60
            # 績效分：PnL 越高分數越高，持有越久分數越低
            perf_score = pnl - (hold_min * 0.05) 
            if perf_score < worst_score:
                worst_score = perf_score
                worst_sym = sym
        return worst_sym
