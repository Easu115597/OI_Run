# logic_engine.py
from config import CONFIG

def hard_filter(v, ai_res, d):  
    """
    V25.5 法律引擎：支持兩階段進場與長線燃料特赦
    v: 物理結構 (metadata_phys)
    ai_res: 決策字典 (包含 final_score, entry_stage, is_demon)
    d: 原始指標數據
    """
    final_score = ai_res.get('final_score', 0)
    # V25 新增：獲取進場階段標籤
    entry_stage = ai_res.get('entry_stage', 'NONE')
    is_demon = ai_res.get('is_demon', False)
    structure = v.get('structure', 'UNKNOWN')
    side = "LONG" if v.get('direction') == 1 else "SHORT"
    rsi = d.get('rsi', 50)
    pr_5m = abs(d.get('pr_5m', 0))
    state_count = d.get('state_count', 0)

    # === 🔱 [第一層：V25.5 至高特赦令] ===
    # 只要是 88 分以上的強單，或標註為偵察兵階段，全線放行結構限制
    if final_score >= CONFIG.get("legal_amnesty_score", 88):
        return True, "LEGAL_AMNESTY_STRONG_MOMENTUM"
    
    # V25 新增：偵察兵特赦 (允許在蓄勢期結構不穩定時進場)
    if entry_stage == "STAGE_1_RECON":
        return True, "LEGAL_AMNESTY_RECON_MODE"

    # === 🔱 [第二層：妖幣/強燃料特赦] ===
    if is_demon:
        if final_score >= 82:
            return True, "LEGAL_AMNESTY_DEMON_ENERGY"
        
        # 妖幣 RSI 寬容度 (對齊 config)
        if side == "LONG" and rsi > CONFIG.get("demon_rsi_max", 85): 
            return False, f"DEMON_RSI_OVERHEAT({rsi:.1f})"
        if side == "SHORT" and rsi < CONFIG.get("demon_rsi_min", 15): 
            return False, f"DEMON_RSI_OVERHEAT({rsi:.1f})"

    # === ⚖️ [第三層：常規法律 (適用於普通幣與 STAGE_2)] ===
    
    # 1. 結構檢查：普通重倉單必須是 BUILD
    if not is_demon and "BUILD" not in structure:
        return False, f"STRUC_REJECT({structure})"

    # 2. 波動檢查：防止死盤
    # 只要分數 > 80，門檻從 0.2% 降到 0.1%
    vol_limit = 0.1 if final_score >= 80 else 0.2
    if pr_5m < vol_limit:
        return False, f"LOW_VOLATILITY({pr_5m:.2f})"

    # 3. 趨勢老化檢查
    if not is_demon and state_count > CONFIG.get("max_hold_min", 45):
        return False, f"TREND_EXHAUSTED({state_count}m)"

    # 4. 普通幣 RSI 嚴格限制
    if not is_demon:
        if side == "LONG" and rsi > CONFIG.get("rsi_long_max", 78): 
            return False, f"LONG_RSI_HIGH({rsi:.1f})"
        if side == "SHORT" and rsi < CONFIG.get("rsi_short_min", 22): 
            return False, f"SHORT_RSI_LOW({rsi:.1f})"

    return True, "PASS"