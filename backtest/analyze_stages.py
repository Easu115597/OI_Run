import pandas as pd

def analyze_entry_stages(csv_file):
    print(f"📊 正在深度分析階段表現: {csv_file}")
    df = pd.read_csv(csv_file).rename(columns=lambda x: x.strip())
    
    # 過濾出已平倉的單子
    df = df[df['Event'] == 'CLOSE']
    
    # 提取階段 (從 Reason 欄位，假設格式為 Score:83 [entry_stage:STAGE_2_HEAVY | ...])
    def extract_stage(reason):
        if 'STAGE_2_HEAVY' in str(reason): return 'HEAVY'
        if 'STAGE_1_RECON' in str(reason): return 'RECON'
        return 'UNKNOWN'

    df['Stage'] = df['Reason_Metadata'].apply(extract_stage)
    
    # 分組統計
    stats = []
    for stage in ['RECON', 'HEAVY']:
        subset = df[df['Stage'] == stage]
        if subset.empty: continue
        
        wins = subset[subset['PnL_USDT'] > 0]
        losses = subset[subset['PnL_USDT'] <= 0]
        
        win_rate = len(wins) / len(subset)
        total_pnl = subset['PnL_USDT'].sum()
        avg_pnl = subset['PnL_USDT'].mean()
        pf = wins['PnL_USDT'].sum() / abs(losses['PnL_USDT'].sum()) if not losses.empty else 999
        
        stats.append({
            "階段": stage,
            "交易數": len(subset),
            "勝率": f"{win_rate:.2%}",
            "總盈虧": f"{total_pnl:+.2f} U",
            "獲利因子(PF)": f"{pf:.2f}",
            "平均每筆": f"{avg_pnl:+.2f} U"
        })

    print("\n" + "="*70)
    print(pd.DataFrame(stats).to_string(index=False))
    print("="*70)

if __name__ == "__main__":
    analyze_entry_stages("../oi_sim_trades_v30.csv") # 指向你的交易日誌