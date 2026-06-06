class TradeAnalyzer:

    def __init__(self, trades):
        self.trades = trades
        self.initial_balance = 2000 # 假設

    def analyze(self):
        if not self.trades:
            print("❌ 無交易數據可分析")
            return
        
        bad_entries = []
        early_exits = []

        for t in self.trades:

            rsi = self.extract_rsi(t['meta'])
            pnl = t['pnl_pct']

            # ❌ 不該開（高風險進場）
            if t['side'] == "LONG" and rsi and rsi > 75:
                bad_entries.append((t, "LONG_RSI_TOO_HIGH"))

            if t['side'] == "SHORT" and rsi and rsi < 25:
                bad_entries.append((t, "SHORT_RSI_TOO_LOW"))

            # 💰 太早出（其實會賺更多）
            if t['reason'] in ["MOMENTUM_DECAY", "REPLACED"] and pnl > 0:
                early_exits.append((t, "EXIT_TOO_EARLY"))

            if not self.trades: return
        
            # 基礎統計
            tp_trades = [t for t in self.trades if t['reason'] == 'TP_MAX']
            sl_trades = [t for t in self.trades if t['reason'] == 'SL_HIT']
            
            total_pnl = sum(t['pnl'] for t in self.trades)
            win_rate = len(tp_trades) / len(self.trades) if self.trades else 0

            # 1. 基礎統計
            wins = [t for t in self.trades if t['pnl'] > 0]
            losses = [t for t in self.trades if t['pnl'] <= 0]
            total_pnl = sum(t['pnl'] for t in self.trades)
            
            win_rate = len(wins) / len(self.trades) * 100
            profit_factor = abs(sum(t['pnl'] for t in wins) / sum(t['pnl'] for t in losses)) if losses else 999
            
            # 2. 輸出統計看板
            print("\n" + "="*40)
            print(f"💰 總盈虧: {total_pnl:+.2f} USDT")
            print(f"🎯 勝率: {win_rate:.2f}% ({len(wins)}勝 / {len(losses)}敗)")
            print(f"📈 獲利因子 (PF): {profit_factor:.2f}")
            print(f"📦 平均每筆盈虧: {total_pnl/len(self.trades):.2f} USDT")
            print("="*40)

            # 3. 排名分析
            sorted_trades = sorted(self.trades, key=lambda x: x['pnl'])
            
            print("\n🏆 [最強 Top 5 盈利單]")
            for t in reversed(sorted_trades[-15:]):
                print(f"  - {t['symbol']} | {t['side']} | PnL: {t['pnl']:+.2f} | 理由: {t['meta']}")

            print("\n💀 [最慘 Top 5 虧損單]")
            for t in sorted_trades[:15]:
                print(f"  - {t['symbol']} | {t['side']} | PnL: {t['pnl']:+.2f} | 理由: {t['meta']}")

            return bad_entries, early_exits

    def extract_rsi(self, meta):
        try:
            if "rsi:" in meta:
                return float(meta.split("rsi:")[1].split("|")[0])
        except:
            return None
        
    def export_for_gpt(self):
        """匯出適合丟給 GPT 分析的格式"""
        # 這裡提取 Meta 欄位裡的原始指標 (OI, Taker, RSI 等)
        # 這樣 GPT 就能幫你看：是不是 RSI 超過 70 的做多單全部都死在最慘名單裡
        pass
        
    def build_dataset(self):
        dataset = []

        for t in self.trades:
            rsi = self.extract_rsi(t['meta'])

            dataset.append({
                "rsi": rsi,
                "pnl": t['pnl_pct'],
                "side": t['side']
            })

        return dataset
    