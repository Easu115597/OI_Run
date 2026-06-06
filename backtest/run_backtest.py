import sys
import os
import pandas as pd
from datetime import datetime

# 1. 自動獲取根目錄路徑
ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

try:
    from config import CONFIG
    print(f"✅ 成功讀取根目錄設定 | 版本: {CONFIG.get('version', 'Unknown')}")
except ImportError:
    print("❌ 錯誤：找不到 config.py")
    sys.exit(1)

from backtest.replay_engine import ReplayEngine
from backtest.trade_analyzer import TradeAnalyzer
from backtest.report_generator import ReportGenerator

# 2. 獲取 CSV 檔案路徑
LOG_FILE_NAME = CONFIG.get("trade_log_csv", "oi_sim_trades_v30.csv")
CSV_FILE = os.path.join(ROOT_PATH, LOG_FILE_NAME)

print(f"🔍 正在讀取資料來源: {CSV_FILE}")

if not os.path.exists(CSV_FILE):
    print(f"❌ 嚴重錯誤：找不到檔案 {CSV_FILE}")
    sys.exit(1)

# 3. 🏁 執行回測引擎 (配對 OPEN/CLOSE)
engine = ReplayEngine(CSV_FILE)
trades = engine.run()

if not trades:
    print(f"❌ 載入交易數: 0")
    sys.exit(0)

print(f"📊 載入交易數: {len(trades)}")

# 4. 📊 執行基礎分析與報告
try:
    analyzer = TradeAnalyzer(trades)
    bad_entries, early_exits = analyzer.analyze() 
    report = ReportGenerator(bad_entries, early_exits)
    report.print_report()
except Exception as e:
    print(f"❌ 分析過程中發生錯誤: {e}")

# 5. 🎯 [新增] 深度階段表現分析 (Stage Analysis)
def analyze_entry_stages_fixed(trade_list):
    print(f"\n📈 正在深度分析階段表現 (樣本數: {len(trade_list)})")
    
    # 建立一個列表來存儲提取後的數據
    processed_data = []
    
    for t in trade_list:
        meta = str(t.get('meta', ''))
        # 提取階段
        stage = 'UNKNOWN'
        if 'STAGE_2_HEAVY' in meta: stage = 'HEAVY'
        elif 'STAGE_1_RECON' in meta: stage = 'RECON'
        elif 'V23_SMOOTH_TREND' in meta: stage = 'SMOOTH'
        elif 'ULTRA_RESONANCE' in meta: stage = 'RESONANCE'
        
        processed_data.append({
            'Stage': stage,
            'PnL': t.get('pnl', 0),
            'Symbol': t.get('symbol', '')
        })
    
    df = pd.DataFrame(processed_data)
    
    stats = []
    for stage in ['RECON', 'HEAVY','SMOOTH','RESONANCE']:
        subset = df[df['Stage'] == stage]
        if subset.empty: continue
        
        wins = subset[subset['PnL'] > 0]
        losses = subset[subset['PnL'] <= 0]
        
        win_rate = len(wins) / len(subset)
        total_pnl = subset['PnL'].sum()
        avg_pnl = subset['PnL'].mean()
        # 獲利因子計算
        loss_sum = abs(losses['PnL'].sum())
        pf = wins['PnL'].sum() / loss_sum if loss_sum > 0 else 999.0
        
        stats.append({
            "階段": stage,
            "交易數": len(subset),
            "勝率": f"{win_rate:.2%}",
            "總盈虧": f"{total_pnl:+.2f} U",
            "獲利因子(PF)": f"{pf:.2f}",
            "平均每筆": f"{avg_pnl:+.2f} U"
        })

    if stats:
        print("\n" + "="*80)
        print(f"🚀 【分階段戰力報告】")
        print("-" * 80)
        print(pd.DataFrame(stats).to_string(index=False))
        print("="*80)
    else:
        print("⚠️ 未能在交易紀錄中識別出 STAGE_1 或 STAGE_2 標籤。")

# 執行深度分析
analyze_entry_stages_fixed(trades)