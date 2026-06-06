# config.py (V32 旗艦版配置)
VER = "v34" 

# 1. 定義分級作戰矩陣 ( margin:單位, tp:獲利目標, sl:初始止損 )
STAGE_SETTINGS = {
    "ULTRA_RESONANCE":  {"margin": 50, "tp": 0.045, "sl": 0.015}, 
    "V23_SMOOTH_TREND": {"margin": 40, "tp": 0.040, "sl": 0.015},
    "STAGE_2_HEAVY":    {"margin": 50, "tp": 0.040, "sl": 0.015}, 
    "STAGE_1_RECON":    {"margin": 20, "tp": 0.025, "sl": 0.013},
    "DEFAULT":          {"margin": 25, "tp": 0.030, "sl": 0.012}
}

CONFIG = {
    "version": VER,
    
    # ===== 文件路徑 =====
    "signal_csv": f"oi_rest_signals_{VER}.csv",
    "trade_log_csv": f"oi_sim_trades_{VER}.csv",
    "summary_csv": f"oi_summary_15m_{VER}.csv",
    "pos_json": f"positions_{VER}.json",

    # ===== 1. 資金管理 (兩階段進場配置) =====
    "initial_balance": 2000,
    "leverage": 5,
    "max_positions": 15,
    "max_new_trades_per_cycle": 2,
    
    # V25 新增：分階段下注金額
    #"recon_margin": 20,           # STAGE_1 偵察兵下 15U
    #"heavy_margin": 35,           # STAGE_2 衝鋒單下 40U
    #"score_S": 94,                # 94分神單下 80U
    #"score_A": 88,                # 88分強單下 60U
    

    # ===== 2. 市場動態門檻 (對齊 V25 主循環) =====
    "trend_threshold": 80,        # 趨勢市門檻下調，捕捉早期 RECALL
    "range_threshold": 83,        # 震盪市嚴格門檻
    
    # ===== 3. V24/V25 燃料核心特徵 =====
    "demon_oi_4h_min": 15.0,      
    "demon_oi_4h_super": 35.0,    
    "demon_funding_squeeze": -0.2,
    "demon_acc_oi_min": 1.2,      # 針對 RECALL 下調門檻 (原本 1.8)
    "demon_acc_pr_max": 0.0035,   # 針對潛伏期放寬價格波動 (0.35%)
    "legal_amnesty_score": 88,    

    # ===== 4. 止盈止損與特赦 RSI =====
    "tp_pct": 0.050,              # 提高目標回報
    "sl_pct": 0.012,              
    "breakeven_trigger": 0.012,   
    "breakeven_profit": 0.006,    
    "rsi_long_max": 78,           
    "rsi_short_min": 22,          
    "demon_rsi_max": 85,          
    "demon_rsi_min": 15, 
    "profit_lock_trigger": 0.025, # 2.5% 啟動二段鎖利
    "profit_lock_pct": 0.015,     # 二段鎖定 1.5%         

    # ===== 5. 持時與動能退場 =====
    "max_hold_min": 45,           
    "momentum_idle_min": 18,      
    "replacement_min_hold": 15,   
    "replacement_max_pnl": 0.003, 
    "cooldown_minutes": 30        
}