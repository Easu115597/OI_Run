import pandas as pd
import os

class ReplayEngine:
    def __init__(self, csv_file):
        self.csv_file = csv_file
        # 1. ✨ 保留：讀取時自動去掉欄位名稱前後的空格
        self.df = pd.read_csv(csv_file).rename(columns=lambda x: x.strip())
        self.trades = []

    def _clean_val(self, val):
        """將 ' 2.5%' 轉換為 0.025"""
        if isinstance(val, str):
            try:
                return float(val.replace('%', '').strip()) / 100
            except:
                return 0.0
        return val

    def run(self):
        # 2. 🚀 核心改進：使用字典 active_positions 支援「多幣種並行持倉」
        # 取代原本的 current_trade = None，防止開倉訊號被覆蓋
        active_positions = {} 
        
        self.df['Event'] = self.df['Event'].astype(str).str.strip()
        
        for _, row in self.df.iterrows():
            event = row['Event']
            symbol = row['Symbol']
            
            if event == 'OPEN':
                # ✨ 保留你的欄位對齊 'meta'，但存入字典中
                active_positions[symbol] = {
                    "symbol": symbol,
                    "side": row['Side'],
                    "entry_price": float(row['Price']),
                    "entry_time": row['Time'],
                    "margin": float(row['Margin']),
                    "meta": row.get('Reason_Metadata', row.get('Reason', "")) 
                }

            elif event == 'CLOSE':
                # 🛠️ 邏輯修正：直接從字典彈出 (pop) 該幣種的開倉資訊
                if symbol in active_positions:
                    trade = active_positions.pop(symbol)
                    
                    pnl_usdt = float(row['PnL_USDT'])
                    pnl_pct = self._clean_val(row['PnL_Pct'])
                    
                    # ✨ 融合更新
                    trade.update({
                        "exit_price": float(row['Price']),
                        "exit_time": row['Time'],
                        "pnl": pnl_usdt,
                        "pnl_pct": pnl_pct,
                        "reason": row.get('Reason_Metadata', "")
                    })
                    self.trades.append(trade)
                else:
                    # 這是為了防錯：如果只有 CLOSE 沒有 OPEN，就跳過
                    continue

        # 3. ✨ 增加排序：讓報告按結束時間排列，看起來更直觀
        self.trades.sort(key=lambda x: x['exit_time'])
        return self.trades