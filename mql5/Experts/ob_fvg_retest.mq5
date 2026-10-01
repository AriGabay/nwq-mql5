//+------------------------------------------------------------------+
//|                                               ob_fvg_retest.mq5  |
//|  Order Block (found from its identifying FVG) -> touch -> new    |
//|  confirmation FVG -> Limit order at a retest level.              |
//|  Plan: docs/plans/2026-09-30-2310-feat-ob-fvg-retest-ea-plan.md  |
//|  Rules R4-R18, R36-R38, R41-R42. Closed-bar signals only; the    |
//|  chart period must equal SignalTF. Long is described; short      |
//|  mirrors.                                                        |
//+------------------------------------------------------------------+
#property copyright ""
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//==================================================================
// INPUTS (order, names and defaults fixed by KTD12 / interface contract)
//==================================================================
enum ENUM_OB_MODE    { OB_FVG = 0, OB_FVG_BOS = 1 };
enum ENUM_ENTRY_MODE { ENTRY_FVG_EDGE = 0, ENTRY_FVG_MID = 1, ENTRY_OB_EDGE = 2, ENTRY_OB_MID = 3 };

input ENUM_TIMEFRAMES SignalTF          = PERIOD_M15; // signal timeframe (chart period must match)
input ENUM_OB_MODE    ObMode            = OB_FVG;     // OB qualification (R6)
input ENUM_ENTRY_MODE EntryMode         = ENTRY_FVG_EDGE; // Limit price (R10)
input int             ImpulseWindowBars = 2;     // look-back from identifying-FVG candle 1 for the OB candle (R5)
input int             BosWindowBars     = 6;     // FVG+BOS: break close must occur within N bars after the OB candle (R6)
input int             SwingStrength     = 3;     // pivot strength each side (R6)
input double          VolumeMultiplier  = 2.0;   // middle candle tick volume >= k x average of the previous lookback bars (R41)
input int             VolumeLookbackHours = 24;  // lookback = VolumeLookbackHours*60/period minutes bars (96 on M15, 288 on M5) (R41)
input int             ObMaxAgeBars      = 96;    // touch window after activation (R8)
input int             FvgWindowBars     = 12;    // confirmation window after touch (R9), must be >= 2
input int             OrderExpiryBars   = 12;    // pending-order window after confirmation bar (R11)
input int             StopBufferPoints  = 10;    // SL beyond OB edge, points (R13)
input double          RiskRR            = 2.0;   // R multiple (R13)
input double          RiskPercent       = 1.0;   // % of balance at placement (R14)
input int             MaxExposures      = 3;     // positions + pending orders (R16)
input int             WarmupBars        = 3000;  // closed bars replayed in OnInit with trading disabled (KTD6)
input long            MagicNumber       = 770101; // magic number (R17)
input string          TradeComment      = "OBR"; // order comment prefix (no commas)
#ifdef RESEARCH_LOG
input string          ResearchRunTag    = "";    // tag for research CSV files ("" = no CSV)
#endif

//==================================================================
// CONSTANTS / TYPES
//==================================================================
// per-OB state machine (plan High-Level Technical Design)
#define ST_CANDIDATE 0   // identifying FVG found, qualification not complete (FVG+BOS)
#define ST_ACTIVE    1   // qualified, waiting for the touch (R8)
#define ST_TOUCHED   2   // touched, waiting for the confirmation FVG (R9)
#define ST_CONFIRMED 3   // confirmation FVG closed, placement on this tick (R10)
#define ST_PENDING   4   // Limit order working (R11)
#define ST_FILLED    5   // position open, only SL/TP manage it (R38)
#define ST_DONE      6   // retired; removed from the working list

// R42: market-closed placement retries (TRADE_RETCODE_MARKET_CLOSED)
#define MC_RETRY_MAX     120     // at most this many placement attempts per setup
#define MC_RETRY_GAP_MSC 60000   // at most one attempt per 60 s of tick time

struct Piv
  {
   int      peak;     // absolute index of the peak bar
   int      conf;     // absolute index of the last right-side confirmation bar
   double   lvl;
   int      brk;      // first bar after conf whose close breaks the level, -1 = unbroken
  };

struct Setup
  {
   int      id;
   int      dir;          // 1 long, -1 short
   int      state;
   long     actSeq;       // activation order (KTD2, R16)
   // OB candle (R5)
   int      obBar;
   double   obHigh;
   double   obLow;
   // identifying FVG (R36)
   int      idC1;
   int      idC3;
   double   idLow;
   double   idHigh;
   double   idVolRatio;   // middle-candle tick volume ratio (R41)
   // FVG+BOS qualification (R6)
   int      bosPivBar;
   int      bosPivConf;
   double   bosLevel;
   int      bosBreakBar;
   // stages
   int      actBar;
   int      touchBar;
   // confirmation FVG (R9, R36)
   int      cC1;
   int      cC3;
   double   cLow;
   double   cHigh;
   double   cVolRatio;    // middle-candle tick volume ratio (R41)
   // order
   bool     priced;
   double   entry;
   double   sl;
   double   tp;
   bool     sized;
   double   volume;
   long     stopsPts;
   long     placeMsc;
   ulong    ticket;
   int      placeAttempts;   // placement attempts made (R42)
   long     lastAttemptMsc;  // tick time of the last attempt (R42 throttle)
   long     mcFirstMsc;      // tick time of the first market-closed refusal, 0 = none (R42)
   // fill / exit
   long     fillMsc;
   double   fillPrice;
   ulong    posId;
   long     exitMsc;
   double   exitPrice;
   string   exitKind;
   // outcome
   string   reason;
   long     reasonMsc;
   bool     cancelDue;     // R11 cancellation scheduled, delete not yet executed
   string   cancelReason;
   long     cancelDueMsc;
   bool     retestSeen;
  };

//==================================================================
// GLOBAL STATE
//==================================================================
// closed signal-timeframe bars by absolute index (0 = first processed bar)
double   gO[], gH[], gL[], gC[];
datetime gT[];
long     gV[];                // tick volume per bar (MqlRates.tick_volume, R41)
long     gVCum[];             // prefix sums: gVCum[k] = gV[0] + ... + gV[k-1] (size gBars + 1)
int      gBars = 0;
int      gVolN = 0;           // R41 lookback in bars = VolumeLookbackHours*60 / period minutes
datetime gLastTime = 0;
long     gPeriodSec = 0;

Piv      gPH[];              // swing highs
Piv      gPL[];              // swing lows
datetime gUsedOb[];          // OB candle times already used (R5: at most once)
Setup    S[];                // working setups (candidate .. filled)
bool     gDirty = false;
int      gNextId = 1;
long     gActSeq = 0;
bool     gOrphansDone = false;

CTrade   trade;
double   gTick = 0.0;
int      gDigits = 0;
int      gVolDigits = 2;
string   gCmtPrefix = "";

// funnel (KTD7)
string   gReasons[] = {"expired_untouched", "invalidated_active", "invalidated_touched", "invalidated_confirmed",
                       "invalidated_pending", "cancelled_no_fvg", "skipped_price_past", "skipped_too_close",
                       "skipped_sl_stops", "skipped_volume", "skipped_margin", "skipped_cap", "skipped_duplicate",
                       "expired_unfilled", "filled", "filled_late", "run_end_pending", "skipped_market_closed",
                       "skipped_broker_reject"};
int      gReasonCnt[];
int      gCntActivated = 0, gCntTouched = 0, gCntConfirmed = 0, gCntPlaced = 0, gCntWarmupDropped = 0;
int      gCntIdfvgRejVol = 0, gCntCfvgRejVol = 0, gCntMcRetries = 0;   // R41 / R42 funnel keys

//==================================================================
// SMALL HELPERS
//==================================================================
string Fmt(double p) { return DoubleToString(p, gDigits); }
string DirStr(int d) { return d == 1 ? "LONG" : "SHORT"; }
long   BarCloseMsc(int bar) { return ((long)gT[bar] + gPeriodSec) * 1000; }

double RoundTick(double p, bool up)
  {
   if(gTick <= 0) return NormalizeDouble(p, gDigits);
   double q = p / gTick;
   double r = up ? MathCeil(q - 1e-9) : MathFloor(q + 1e-9);
   return NormalizeDouble(r * gTick, gDigits);
  }
double RoundTickNearest(double p)
  {
   if(gTick <= 0) return NormalizeDouble(p, gDigits);
   return NormalizeDouble(MathRound(p / gTick) * gTick, gDigits);
  }

string OrderComment(int dir, datetime obTime)
  {
   return gCmtPrefix + "#" + (dir == 1 ? "L" : "S") + "#" + IntegerToString((long)obTime);
  }

void InitSetup(Setup &s)
  {
   s.id = 0; s.dir = 0; s.state = ST_CANDIDATE; s.actSeq = 0;
   s.obBar = -1; s.obHigh = 0; s.obLow = 0;
   s.idC1 = -1; s.idC3 = -1; s.idLow = 0; s.idHigh = 0; s.idVolRatio = 0;
   s.bosPivBar = -1; s.bosPivConf = -1; s.bosLevel = 0; s.bosBreakBar = -1;
   s.actBar = -1; s.touchBar = -1;
   s.cC1 = -1; s.cC3 = -1; s.cLow = 0; s.cHigh = 0; s.cVolRatio = 0;
   s.priced = false; s.entry = 0; s.sl = 0; s.tp = 0; s.sized = false; s.volume = 0; s.stopsPts = 0;
   s.placeMsc = 0; s.ticket = 0; s.placeAttempts = 0; s.lastAttemptMsc = 0; s.mcFirstMsc = 0;
   s.fillMsc = 0; s.fillPrice = 0; s.posId = 0; s.exitMsc = 0; s.exitPrice = 0; s.exitKind = "";
   s.reason = ""; s.reasonMsc = 0;
   s.cancelDue = false; s.cancelReason = ""; s.cancelDueMsc = 0; s.retestSeen = false;
  }

// a close beyond the OB on the stop side (long: below OB low) - R6, R11
bool CloseBeyond(const Setup &s, int bar)
  {
   return (s.dir == 1) ? (gC[bar] < s.obLow) : (gC[bar] > s.obHigh);
  }
// touch: long low reaches OB high, short high reaches OB low (R8, R15)
bool Touches(const Setup &s, int bar)
  {
   return (s.dir == 1) ? (gL[bar] <= s.obHigh) : (gH[bar] >= s.obLow);
  }

//==================================================================
// RESEARCH LOGGING (research build only). Reads account/symbol state
// and writes files; it never sends, modifies or closes orders.
// rl_days / rl_deals / OnTester / frames are ported unchanged from the
// archived EA (KTD8); rl_setups / rl_bars are new.
//==================================================================
#ifdef RESEARCH_LOG
#define RL_DAY_FIELDS  8
#define RL_SPREAD_BINS 20000
#define RL_SETUPS_HEADER "setup_id,dir,ob_mode,entry_mode,ob_time,ob_high,ob_low,idfvg_c1_time,idfvg_c3_time,idfvg_low,idfvg_high,bos_pivot_time,bos_pivot_conf_time,bos_level,bos_break_time,activation_time,touch_time,cfvg_c1_time,cfvg_c3_time,cfvg_low,cfvg_high,entry,sl,tp,volume,stops_level_pts,place_time_msc,order_ticket,fill_time_msc,fill_price,position_id,exit_time_msc,exit_price,exit_kind,reason,reason_time_msc,retest_seen_no_fill,idfvg_vol_ratio,cfvg_vol_ratio,market_closed_first_msc,place_attempts"
datetime rlDay = 0;
double   rlBalOpen = 0, rlEqOpen = 0, rlEqMin = 0, rlEqMax = 0, rlBalClose = 0, rlEqClose = 0;
int      rlSpreadHist[RL_SPREAD_BINS];
long     rlSpreadN = 0;
double   rlDays[];            // flattened day records: date, balOpen, eqOpen, eqMin, eqMax, balClose, eqClose, spreadMedian
bool     rlFinalized = false;
bool     rlOn = false;        // setup/bar rows are collected only when files will be written
string   rlSetupRows[];
MqlRates rlBars[];

double RL_SpreadMedian()
  {
   if(rlSpreadN <= 0) return 0.0;
   long half = (rlSpreadN + 1) / 2, acc = 0;
   for(int i = 0; i < RL_SPREAD_BINS; i++)
     {
      acc += rlSpreadHist[i];
      if(acc >= half) return i * _Point;
     }
   return (RL_SPREAD_BINS - 1) * _Point;
  }
void RL_CloseDay()
  {
   if(rlDay == 0) return;
   int sz = ArraySize(rlDays);
   ArrayResize(rlDays, sz + RL_DAY_FIELDS, 8192);
   rlDays[sz + 0] = (double)rlDay;
   rlDays[sz + 1] = rlBalOpen;
   rlDays[sz + 2] = rlEqOpen;
   rlDays[sz + 3] = rlEqMin;
   rlDays[sz + 4] = rlEqMax;
   rlDays[sz + 5] = rlBalClose;
   rlDays[sz + 6] = rlEqClose;
   rlDays[sz + 7] = RL_SpreadMedian();
   rlDay = 0;
  }
void RL_StartDay(datetime day, double bal, double eq)
  {
   rlDay = day; rlBalOpen = bal; rlEqOpen = eq; rlEqMin = eq; rlEqMax = eq; rlBalClose = bal; rlEqClose = eq;
   ArrayInitialize(rlSpreadHist, 0);
   rlSpreadN = 0;
  }
void RL_Init()
  {
   ArrayResize(rlDays, 0);
   rlDay = 0;
   rlFinalized = false;
   rlOn = (ResearchRunTag != "" && !MQLInfoInteger(MQL_OPTIMIZATION));
   ArrayResize(rlSetupRows, 0);
   ArrayResize(rlBars, 0);
  }
void RL_OnTick()
  {
   if(rlFinalized) return;
   datetime now = TimeCurrent();
   datetime day = now - now % 86400;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(day != rlDay)
     {
      RL_CloseDay();
      RL_StartDay(day, bal, eq);
     }
   if(eq < rlEqMin) rlEqMin = eq;
   if(eq > rlEqMax) rlEqMax = eq;
   rlBalClose = bal;
   rlEqClose  = eq;
   MqlTick tk;
   if(_Point > 0 && SymbolInfoTick(_Symbol, tk))
     {
      int pts = (int)MathRound((tk.ask - tk.bid) / _Point);
      if(pts < 0) pts = 0;
      if(pts >= RL_SPREAD_BINS) pts = RL_SPREAD_BINS - 1;
      rlSpreadHist[pts]++;
      rlSpreadN++;
     }
  }
void RL_Finalize()
  {
   if(rlFinalized) return;
   RL_CloseDay();
   rlFinalized = true;
  }
// non-annualized Sharpe of daily close-to-close equity returns (first day vs its opening equity)
double RL_DailySharpe()
  {
   int nd = ArraySize(rlDays) / RL_DAY_FIELDS;
   if(nd < 2) return 0.0;
   double r[];
   ArrayResize(r, nd);
   double prev = rlDays[2];
   for(int i = 0; i < nd; i++)
     {
      double c = rlDays[i * RL_DAY_FIELDS + 6];
      r[i] = (prev > 0) ? c / prev - 1.0 : 0.0;
      prev = c;
     }
   double m = 0;
   for(int i = 0; i < nd; i++) m += r[i];
   m /= nd;
   double v = 0;
   for(int i = 0; i < nd; i++) v += (r[i] - m) * (r[i] - m);
   v /= (nd - 1);
   return (v > 0) ? m / MathSqrt(v) : 0.0;
  }
void RL_CostTotals(double &commission, double &swap, double &lots, double &entries)
  {
   commission = 0; swap = 0; lots = 0; entries = 0;
   if(!HistorySelect(0, TimeCurrent() + 86400)) return;
   for(int i = 0; i < HistoryDealsTotal(); i++)
     {
      ulong tk = HistoryDealGetTicket(i);
      if(tk == 0) continue;
      long type = HistoryDealGetInteger(tk, DEAL_TYPE);
      if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL) continue;
      commission += HistoryDealGetDouble(tk, DEAL_COMMISSION);
      swap       += HistoryDealGetDouble(tk, DEAL_SWAP);
      if(HistoryDealGetInteger(tk, DEAL_ENTRY) == DEAL_ENTRY_IN)
        {
         lots += HistoryDealGetDouble(tk, DEAL_VOLUME);
         entries += 1;
        }
     }
  }
// closed signal bar processed while trading was enabled (rl_bars)
void RL_AddBar(const MqlRates &r)
  {
   if(!rlOn) return;
   int sz = ArraySize(rlBars);
   ArrayResize(rlBars, sz + 1, 65536);
   rlBars[sz] = r;
  }
string RL_T(int bar)              { return (bar >= 0 && bar < gBars) ? IntegerToString((long)gT[bar]) : ""; }
string RL_P(double p, bool has)   { return has ? DoubleToString(p, gDigits) : ""; }
string RL_L(long v, bool has)     { return has ? IntegerToString(v) : ""; }
// one rl_setups row per setup that reached Active (warm-up drops excluded by the caller)
void RL_AddSetupRow(const Setup &s)
  {
   if(!rlOn) return;
   bool conf   = (s.cC3 >= 0);
   bool placed = (s.ticket > 0);
   bool filled = (s.fillMsc > 0);
   bool exited = (s.exitKind != "");
   string row = IntegerToString(s.id)
                + "," + (s.dir == 1 ? "L" : "S")
                + "," + IntegerToString((int)ObMode)
                + "," + IntegerToString((int)EntryMode)
                + "," + RL_T(s.obBar)
                + "," + RL_P(s.obHigh, true)
                + "," + RL_P(s.obLow, true)
                + "," + RL_T(s.idC1)
                + "," + RL_T(s.idC3)
                + "," + RL_P(s.idLow, true)
                + "," + RL_P(s.idHigh, true)
                + "," + RL_T(s.bosPivBar)
                + "," + RL_T(s.bosPivConf)
                + "," + RL_P(s.bosLevel, s.bosPivBar >= 0)
                + "," + RL_T(s.bosBreakBar)
                + "," + RL_T(s.actBar)
                + "," + RL_T(s.touchBar)
                + "," + RL_T(s.cC1)
                + "," + RL_T(s.cC3)
                + "," + RL_P(s.cLow, conf)
                + "," + RL_P(s.cHigh, conf)
                + "," + RL_P(s.entry, s.priced)
                + "," + RL_P(s.sl, s.priced)
                + "," + RL_P(s.tp, s.priced)
                + "," + (s.sized ? DoubleToString(s.volume, gVolDigits) : "")
                + "," + RL_L(s.stopsPts, s.priced)
                + "," + RL_L(s.placeMsc, placed)
                + "," + RL_L((long)s.ticket, placed)
                + "," + RL_L(s.fillMsc, filled)
                + "," + RL_P(s.fillPrice, filled)
                + "," + RL_L((long)s.posId, filled)
                + "," + RL_L(s.exitMsc, exited)
                + "," + RL_P(s.exitPrice, exited)
                + "," + s.exitKind
                + "," + s.reason
                + "," + IntegerToString(s.reasonMsc)
                + "," + (s.retestSeen ? "1" : "0")
                + "," + DoubleToString(s.idVolRatio, 4)
                + "," + (conf ? DoubleToString(s.cVolRatio, 4) : "")
                + "," + RL_L(s.mcFirstMsc, s.mcFirstMsc > 0)
                + "," + IntegerToString(s.placeAttempts);
   int sz = ArraySize(rlSetupRows);
   ArrayResize(rlSetupRows, sz + 1, 4096);
   rlSetupRows[sz] = row;
  }
void RL_WriteRunFiles()
  {
   if(ResearchRunTag == "" || MQLInfoInteger(MQL_OPTIMIZATION)) return;
   int h = FileOpen("rl_days_" + ResearchRunTag + ".csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(h != INVALID_HANDLE)
     {
      FileWrite(h, "date", "bal_open", "eq_open", "eq_min", "eq_max", "bal_close", "eq_close", "spread_median");
      int nd = ArraySize(rlDays) / RL_DAY_FIELDS;
      for(int i = 0; i < nd; i++)
        {
         int b = i * RL_DAY_FIELDS;
         FileWrite(h, TimeToString((datetime)rlDays[b], TIME_DATE),
                   DoubleToString(rlDays[b + 1], 2), DoubleToString(rlDays[b + 2], 2), DoubleToString(rlDays[b + 3], 2),
                   DoubleToString(rlDays[b + 4], 2), DoubleToString(rlDays[b + 5], 2), DoubleToString(rlDays[b + 6], 2),
                   DoubleToString(rlDays[b + 7], gDigits));
        }
      FileClose(h);
     }
   h = FileOpen("rl_deals_" + ResearchRunTag + ".csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(h != INVALID_HANDLE && HistorySelect(0, TimeCurrent() + 86400))
     {
      FileWrite(h, "time", "ticket", "position_id", "type", "entry", "volume", "price", "profit", "commission", "swap", "magic", "comment");
      for(int i = 0; i < HistoryDealsTotal(); i++)
        {
         ulong tk = HistoryDealGetTicket(i);
         if(tk == 0) continue;
         FileWrite(h, TimeToString((datetime)HistoryDealGetInteger(tk, DEAL_TIME), TIME_DATE | TIME_SECONDS),
                   (long)tk, HistoryDealGetInteger(tk, DEAL_POSITION_ID), HistoryDealGetInteger(tk, DEAL_TYPE),
                   HistoryDealGetInteger(tk, DEAL_ENTRY), DoubleToString(HistoryDealGetDouble(tk, DEAL_VOLUME), 2),
                   DoubleToString(HistoryDealGetDouble(tk, DEAL_PRICE), gDigits), DoubleToString(HistoryDealGetDouble(tk, DEAL_PROFIT), 2),
                   DoubleToString(HistoryDealGetDouble(tk, DEAL_COMMISSION), 2), DoubleToString(HistoryDealGetDouble(tk, DEAL_SWAP), 2),
                   HistoryDealGetInteger(tk, DEAL_MAGIC), HistoryDealGetString(tk, DEAL_COMMENT));
        }
     }
   if(h != INVALID_HANDLE) FileClose(h);
   // rl_setups: one row per setup (KTD8, R40)
   h = FileOpen("rl_setups_" + ResearchRunTag + ".csv", FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h != INVALID_HANDLE)
     {
      FileWriteString(h, RL_SETUPS_HEADER);
      FileWriteString(h, "\r\n");
      for(int i = 0; i < ArraySize(rlSetupRows); i++)
         FileWriteString(h, rlSetupRows[i] + "\r\n");
      FileClose(h);
     }
   // rl_bars: closed signal-timeframe OHLC + tick volume processed while trading was enabled (KTD8, R39, R41)
   h = FileOpen("rl_bars_" + ResearchRunTag + ".csv", FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h != INVALID_HANDLE)
     {
      FileWriteString(h, "time,open,high,low,close,tick_volume\r\n");
      for(int i = 0; i < ArraySize(rlBars); i++)
         FileWriteString(h, IntegerToString((long)rlBars[i].time) + "," + DoubleToString(rlBars[i].open, gDigits) + "," +
                         DoubleToString(rlBars[i].high, gDigits) + "," + DoubleToString(rlBars[i].low, gDigits) + "," +
                         DoubleToString(rlBars[i].close, gDigits) + "," + IntegerToString(rlBars[i].tick_volume) + "\r\n");
      FileClose(h);
     }
  }
void RL_Deinit()
  {
   RL_Finalize();
   RL_WriteRunFiles();
  }
double OnTester()
  {
   RL_Finalize();
   double sharpe = RL_DailySharpe();
   if(MQLInfoInteger(MQL_OPTIMIZATION))
     {
      double commission, swap, lots, entries;
      RL_CostTotals(commission, swap, lots, entries);
      int cnt = ArraySize(rlDays);
      double data[];
      ArrayResize(data, 4 + cnt);
      data[0] = commission; data[1] = swap; data[2] = lots; data[3] = entries;
      for(int i = 0; i < cnt; i++) data[4 + i] = rlDays[i];
      FrameAdd("rl", 1, sharpe, data);
     }
   return sharpe;
  }
void OnTesterInit() { }
void OnTesterDeinit()
  {
   int h = FileOpen("rl_frames_" + ResearchRunTag + ".csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(h == INVALID_HANDLE) return;
   FileWrite(h, "kind", "pass", "a", "b", "c", "d", "e", "f", "g", "h");
   ulong pass; string name; long id; double value; double data[];
   FrameFirst();
   while(FrameNext(pass, name, id, value, data))
     {
      if(name != "rl") continue;
      string params[]; uint pc = 0;
      string joined = "";
      if(FrameInputs(pass, params, pc))
         for(uint i = 0; i < pc; i++) joined += (i > 0 ? ";" : "") + params[i];
      FileWrite(h, "P", (long)pass, DoubleToString(value, 8), DoubleToString(data[0], 2), DoubleToString(data[1], 2),
                DoubleToString(data[2], 2), DoubleToString(data[3], 0), joined);
      int nd = (ArraySize(data) - 4) / RL_DAY_FIELDS;
      for(int i = 0; i < nd; i++)
        {
         int b = 4 + i * RL_DAY_FIELDS;
         FileWrite(h, "D", (long)pass, TimeToString((datetime)data[b], TIME_DATE),
                   DoubleToString(data[b + 1], 2), DoubleToString(data[b + 2], 2), DoubleToString(data[b + 3], 2),
                   DoubleToString(data[b + 4], 2), DoubleToString(data[b + 5], 2), DoubleToString(data[b + 6], 2),
                   DoubleToString(data[b + 7], 5));
        }
     }
   FileClose(h);
  }
#endif

//==================================================================
// SETUP LIFECYCLE AND FUNNEL (KTD7)
//==================================================================
void CountReason(string code)
  {
   for(int i = 0; i < ArraySize(gReasons); i++)
      if(gReasons[i] == code) { gReasonCnt[i]++; return; }
   Print("OBR: unknown reason code ", code);
  }
int ReasonCount(string code)
  {
   for(int i = 0; i < ArraySize(gReasons); i++)
      if(gReasons[i] == code) return gReasonCnt[i];
   return 0;
  }
string KV(string key, int v) { return " " + key + "=" + IntegerToString(v); }

void PrintFunnel()
  {
   string line = "Funnel:";
   line += KV("activated", gCntActivated);
   line += KV("touched", gCntTouched);
   line += KV("confirmed", gCntConfirmed);
   line += KV("placed", gCntPlaced);
   line += KV("filled", ReasonCount("filled"));
   line += KV("filled_late", ReasonCount("filled_late"));
   for(int i = 0; i < ArraySize(gReasons); i++)
     {
      if(gReasons[i] == "filled" || gReasons[i] == "filled_late") continue;   // already listed above
      line += KV(gReasons[i], gReasonCnt[i]);
     }
   line += KV("warmup_dropped", gCntWarmupDropped);
   line += KV("idfvg_rejected_volume", gCntIdfvgRejVol);
   line += KV("cfvg_rejected_volume", gCntCfvgRejVol);
   line += KV("market_closed_retries", gCntMcRetries);
   Print(line);
  }

void SetReason(Setup &s, string code, long msc)
  {
   if(s.reason != "") return;
   s.reason = code;
   s.reasonMsc = msc;
  }

// final bookkeeping for a setup: funnel counts and its rl_setups row
void CloseSetup(Setup &s)
  {
   s.state = ST_DONE;
   gDirty = true;
   if(s.actBar < 0) return;                          // never activated: not a setup
   if(s.reason == "warmup_dropped") { gCntWarmupDropped++; return; }   // KTD6: excluded from funnel and rows
   gCntActivated++;
   if(s.touchBar >= 0) gCntTouched++;
   if(s.cC3 >= 0)      gCntConfirmed++;
   if(s.ticket > 0)    gCntPlaced++;
   CountReason(s.reason);
   if(s.cC3 >= 0)
      Print("OBR setup #", s.id, " ", DirStr(s.dir), " -> ", s.reason,
            (s.exitKind != "" ? " (exit " + s.exitKind + ")" : ""));
#ifdef RESEARCH_LOG
   RL_AddSetupRow(s);
#endif
  }

void Retire(Setup &s, string code, long msc)
  {
   SetReason(s, code, msc);
   CloseSetup(s);
  }

void Discard(Setup &s)     // candidate that never qualified (R6) - no reason code, no row
  {
   s.state = ST_DONE;
   gDirty = true;
  }

void Skip(Setup &s, string code, long msc)
  {
   Print("OBR setup #", s.id, " ", DirStr(s.dir), " ", code, " entry ", Fmt(s.entry), " SL ", Fmt(s.sl), " TP ", Fmt(s.tp),
         " stops level ", s.stopsPts, " pts");
   Retire(s, code, msc);
  }

void Activate(Setup &s, int bar)
  {
   s.state  = ST_ACTIVE;
   s.actBar = bar;
   s.actSeq = ++gActSeq;
   s.id     = gNextId++;
  }

void PushSetup(Setup &s)
  {
   int sz = ArraySize(S);
   ArrayResize(S, sz + 1, 256);
   S[sz] = s;
  }

void CompactSetups()
  {
   if(!gDirty) return;
   int w = 0;
   for(int r = 0; r < ArraySize(S); r++)
     {
      if(S[r].state == ST_DONE) continue;
      if(w != r) S[w] = S[r];
      w++;
     }
   ArrayResize(S, w, 256);
   gDirty = false;
  }

//==================================================================
// BARS AND PIVOTS
//==================================================================
int AppendBar(const MqlRates &r)
  {
   int n = gBars;
   ArrayResize(gO, n + 1, 65536); ArrayResize(gH, n + 1, 65536); ArrayResize(gL, n + 1, 65536);
   ArrayResize(gC, n + 1, 65536); ArrayResize(gT, n + 1, 65536); ArrayResize(gV, n + 1, 65536);
   ArrayResize(gVCum, n + 2, 65536);
   gO[n] = r.open; gH[n] = r.high; gL[n] = r.low; gC[n] = r.close; gT[n] = r.time;
   gV[n] = (long)r.tick_volume;
   if(n == 0) gVCum[0] = 0;
   gVCum[n + 1] = gVCum[n] + gV[n];
   gBars = n + 1;
   gLastTime = r.time;
   return n;
  }

// R41: volume filter on an FVG's middle candle m. ratio = gV[m] / mean(gV[m-N .. m-1]), N = gVolN closed
// bars counted (not wall time; warm-up bars count). Fewer than N bars before m -> does not qualify.
bool VolumeQualifies(int m, double &ratio)
  {
   ratio = 0;
   int nb = gVolN;
   if(nb <= 0 || m < nb || m >= gBars) return false;
   long sum = gVCum[m] - gVCum[m - nb];               // bars m-nb .. m-1
   if(sum <= 0) return false;                         // no reference volume: not qualifying
   ratio = (double)gV[m] * (double)nb / (double)sum;
   return ratio >= VolumeMultiplier - 1e-9;           // >= (float noise tolerated), never >
  }

// strict pivot tests (ties are not pivots), adapted from the archived EA
bool IsPivotHigh(int p, int str)
  {
   if(p - str < 0 || p + str >= gBars) return false;
   for(int j = p - str; j <= p + str; j++)
     {
      if(j == p) continue;
      if(!(gH[p] > gH[j])) return false;
     }
   return true;
  }
bool IsPivotLow(int p, int str)
  {
   if(p - str < 0 || p + str >= gBars) return false;
   for(int j = p - str; j <= p + str; j++)
     {
      if(j == p) continue;
      if(!(gL[p] < gL[j])) return false;
     }
   return true;
  }

void PushPiv(Piv &a[], int peak, int conf, double lvl)
  {
   int sz = ArraySize(a);
   ArrayResize(a, sz + 1, 1024);
   a[sz].peak = peak; a[sz].conf = conf; a[sz].lvl = lvl; a[sz].brk = -1;
  }

void PrunePiv(Piv &a[], int n)
  {
   int keepFrom = n - ImpulseWindowBars - 5;          // older breaks can never fall in a BOS window again
   int w = 0;
   for(int r = 0; r < ArraySize(a); r++)
     {
      if(a[r].brk >= 0 && a[r].brk < keepFrom) continue;
      if(w != r) a[w] = a[r];
      w++;
     }
   ArrayResize(a, w, 1024);
   if(ArraySize(a) > 5000) ArrayRemove(a, 0, ArraySize(a) - 5000);
  }

// close-based break per pivot (first close beyond the level after confirmation), then new pivots (R6, KTD3)
void UpdatePivots(int n)
  {
   for(int i = 0; i < ArraySize(gPH); i++)
      if(gPH[i].brk < 0 && gPH[i].conf < n && gC[n] > gPH[i].lvl) gPH[i].brk = n;
   for(int i = 0; i < ArraySize(gPL); i++)
      if(gPL[i].brk < 0 && gPL[i].conf < n && gC[n] < gPL[i].lvl) gPL[i].brk = n;
   int p = n - SwingStrength;
   if(IsPivotHigh(p, SwingStrength)) PushPiv(gPH, p, n, gH[p]);
   if(IsPivotLow(p, SwingStrength))  PushPiv(gPL, p, n, gL[p]);
   PrunePiv(gPH, n);
   PrunePiv(gPL, n);
  }

// R6: does the close of bar b break a swing that (a) was confirmed before b (look-ahead rule)
// and (b) has its peak before the OB candle (structure rule)? Records the latest such swing.
bool FindBreak(Setup &s, int b)
  {
   int best = -1;
   if(s.dir == 1)
     {
      for(int i = 0; i < ArraySize(gPH); i++)
         if(gPH[i].brk == b && gPH[i].conf < b && gPH[i].peak < s.obBar && (best < 0 || gPH[i].peak > gPH[best].peak)) best = i;
      if(best < 0) return false;
      s.bosPivBar = gPH[best].peak; s.bosPivConf = gPH[best].conf; s.bosLevel = gPH[best].lvl;
     }
   else
     {
      for(int i = 0; i < ArraySize(gPL); i++)
         if(gPL[i].brk == b && gPL[i].conf < b && gPL[i].peak < s.obBar && (best < 0 || gPL[i].peak > gPL[best].peak)) best = i;
      if(best < 0) return false;
      s.bosPivBar = gPL[best].peak; s.bosPivConf = gPL[best].conf; s.bosLevel = gPL[best].lvl;
     }
   s.bosBreakBar = b;
   return true;
  }

bool ObUsed(datetime t)
  {
   for(int i = 0; i < ArraySize(gUsedOb); i++)
      if(gUsedOb[i] == t) return true;
   return false;
  }
void MarkObUsed(datetime t, int n)
  {
   // an OB candle lies at most ImpulseWindowBars+1 bars before candle 3, so older entries can go
   int w = 0;
   int oldestBar = n - ImpulseWindowBars - 5;
   if(oldestBar < 0) oldestBar = 0;
   datetime oldest = gT[oldestBar];
   for(int r = 0; r < ArraySize(gUsedOb); r++)
     {
      if(gUsedOb[r] < oldest) continue;
      gUsedOb[w] = gUsedOb[r];
      w++;
     }
   ArrayResize(gUsedOb, w + 1, 64);
   gUsedOb[w] = t;
  }

//==================================================================
// SIGNAL STATE MACHINE (one closed bar, KTD2 order)
//==================================================================
// R5, R7, R15: identifying FVG completed at bar n -> look back from its candle 1 for the OB candle.
// R41: an identifying FVG failing the volume filter qualifies no OB (and does not consume the OB candle).
void NewCandidate(int dir, int n, bool live)
  {
   int c1 = n - 2;
   int ob = -1;
   for(int j = c1; j >= 0 && j > c1 - ImpulseWindowBars; j--)
     {
      bool opposite = (dir == 1) ? (gC[j] < gO[j]) : (gC[j] > gO[j]);   // a doji is neither
      if(opposite) { ob = j; break; }
     }
   if(ob < 0) return;
   if(ObUsed(gT[ob])) return;                        // each candle becomes an OB at most once
   double vr = 0;
   bool volOk = VolumeQualifies(n - 1, vr);           // middle candle = n - 1 (R41)
   if(volOk) MarkObUsed(gT[ob], n);

   Setup s; InitSetup(s);
   s.dir = dir; s.state = ST_CANDIDATE;
   s.obBar = ob; s.obHigh = gH[ob]; s.obLow = gL[ob];
   s.idC1 = c1; s.idC3 = n;
   s.idLow  = (dir == 1) ? gH[c1] : gH[n];
   s.idHigh = (dir == 1) ? gL[n]  : gL[c1];
   s.idVolRatio = vr;

   // R6: a close beyond the OB before activation discards the candidate
   for(int j = ob + 1; j <= n; j++)
      if(CloseBeyond(s, j)) return;

   if(ObMode == OB_FVG)
     {
      if(!volOk) { if(live) gCntIdfvgRejVol++; return; }   // would have activated: rejected by volume
      Activate(s, n); PushSetup(s); return;
     }

   // FVG+BOS: the break close may already have happened between the OB candle and candle 3
   bool brk = false;
   for(int b = ob + 1; b <= n && b - ob <= BosWindowBars; b++)
      if(FindBreak(s, b)) { brk = true; break; }
   if(!brk && n - ob >= BosWindowBars) return;        // BOS window elapsed
   if(!volOk) { if(live) gCntIdfvgRejVol++; return; }   // would have been tracked: rejected by volume
   if(brk) Activate(s, n);                            // activation at the later of c3 and break close
   PushSetup(s);
  }

// R9, R36: record the confirmation FVG; warm-up confirmations are dropped (KTD6)
void Confirm(Setup &s, int n, bool canTrade, long closeMsc, double volRatio)
  {
   int d = s.dir;
   s.cC1 = n - 2; s.cC3 = n;
   s.cVolRatio = volRatio;
   s.cLow  = (d == 1) ? gH[n - 2] : gH[n];
   s.cHigh = (d == 1) ? gL[n]     : gL[n - 2];
   if(!canTrade) { Retire(s, "warmup_dropped", closeMsc); return; }
   s.state = ST_CONFIRMED;                            // placed on this tick (R10)
  }

// R11, R38: pending-order window count, then close beyond the OB. `filledAfterClose` = the order
// filled on a tick after this bar closed, i.e. on the tick where the cancellation would execute.
void PendingBarCheck(Setup &s, int n, long closeMsc, bool filledAfterClose)
  {
   if((s.dir == 1 && gL[n] <= s.entry) || (s.dir == -1 && gH[n] >= s.entry)) s.retestSeen = true;
   string due = "";
   if(n - s.cC3 >= OrderExpiryBars) due = "expired_unfilled";
   else if(CloseBeyond(s, n))       due = "invalidated_pending";
   if(due == "") return;
   if(filledAfterClose) { s.reason = "filled_late"; return; }    // kept, counted separately (R38)
   s.cancelDue = true; s.cancelReason = due; s.cancelDueMsc = closeMsc;
  }

void ProcessBar(const MqlRates &r, bool live, bool canTrade)
  {
   int n = AppendBar(r);
#ifdef RESEARCH_LOG
   if(live) RL_AddBar(r);
#endif
   UpdatePivots(n);
   long closeMsc = BarCloseMsc(n);

   // 1) pending orders: window count, then close beyond the OB (R11, R38). A confirmed setup still
   //    awaiting placement after a market-closed refusal gives up after OrderExpiryBars bars (R42).
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(S[i].state == ST_PENDING && !S[i].cancelDue) PendingBarCheck(S[i], n, closeMsc, false);
      else if(S[i].state == ST_FILLED && S[i].fillMsc >= closeMsc && S[i].reason == "filled")
         PendingBarCheck(S[i], n, closeMsc, true);
      else if(S[i].state == ST_CONFIRMED && n - S[i].cC3 >= OrderExpiryBars)
         Skip(S[i], "skipped_market_closed", closeMsc);
     }

   // 2) close beyond the OB: candidate discard, active / touched / confirmed invalidation (R6, R11)
   for(int i = 0; i < ArraySize(S); i++)
     {
      int st = S[i].state;
      if(st != ST_CANDIDATE && st != ST_ACTIVE && st != ST_TOUCHED && st != ST_CONFIRMED) continue;
      if(!CloseBeyond(S[i], n)) continue;
      if(st == ST_CANDIDATE)      Discard(S[i]);
      else if(st == ST_ACTIVE)    Retire(S[i], "invalidated_active", closeMsc);
      else if(st == ST_TOUCHED)   Retire(S[i], "invalidated_touched", closeMsc);
      else                        Retire(S[i], "invalidated_confirmed", closeMsc);
     }

   // 3) touch, only on bars after the activation bar; expiry after ObMaxAgeBars (R8)
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(S[i].state != ST_ACTIVE || S[i].actBar >= n) continue;
      int k = n - S[i].actBar;
      if(k <= ObMaxAgeBars && Touches(S[i], n)) { S[i].state = ST_TOUCHED; S[i].touchBar = n; }
      else if(k >= ObMaxAgeBars) Retire(S[i], "expired_untouched", closeMsc);
     }

   // 4) confirmation FVG with this bar as candle 3 (R9, R16, R36); it must pass the volume filter (R41)
   if(n >= 2)
     {
      bool bullF = gL[n] > gH[n - 2];
      bool bearF = gH[n] < gL[n - 2];
      double cvr = 0;
      bool cVolOk = (bullF || bearF) ? VolumeQualifies(n - 1, cvr) : false;
      int bestL = -1, bestS = -1;
      for(int i = 0; i < ArraySize(S); i++)
        {
         if(S[i].state != ST_TOUCHED) continue;
         if(!((S[i].dir == 1) ? bullF : bearF)) continue;
         if(n - 2 < S[i].touchBar) continue;                 // candle 1 is the touch bar or later (AE1)
         if(n - S[i].touchBar > FvgWindowBars) continue;     // candle 3 closes within the window
         if(n == S[i].idC3) continue;                        // the identifying FVG never confirms (R36)
         if(S[i].dir == 1) { if(bestL < 0 || S[i].actSeq > S[bestL].actSeq) bestL = i; }
         else              { if(bestS < 0 || S[i].actSeq > S[bestS].actSeq) bestS = i; }
        }
      // one confirmation FVG confirms only the most recently activated eligible OB (R16); a volume-failing
      // FVG confirms nothing and the touched OBs keep waiting for the first passing FVG in their window
      if(!cVolOk)
        {
         if(live && (bestL >= 0 || bestS >= 0)) gCntCfvgRejVol++;
        }
      else
        {
         if(bestL >= 0) Confirm(S[bestL], n, canTrade, closeMsc, cvr);
         if(bestS >= 0) Confirm(S[bestS], n, canTrade, closeMsc, cvr);
        }
     }
   for(int i = 0; i < ArraySize(S); i++)
      if(S[i].state == ST_TOUCHED && n - S[i].touchBar >= FvgWindowBars)
         Retire(S[i], "cancelled_no_fvg", closeMsc);

   // 5) FVG+BOS candidates waiting for their break close, then new identifying FVGs (R5, R6)
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(S[i].state != ST_CANDIDATE || S[i].idC3 >= n) continue;
      if(n - S[i].obBar <= BosWindowBars && FindBreak(S[i], n)) Activate(S[i], n);
      else if(n - S[i].obBar >= BosWindowBars) Discard(S[i]);
     }
   if(n >= 2)
     {
      if(gL[n] > gH[n - 2])      NewCandidate(1, n, live);
      else if(gH[n] < gL[n - 2]) NewCandidate(-1, n, live);
     }
   // 6) setups now in ST_CONFIRMED are placed by OnTick on this tick, oldest activation first
  }

//==================================================================
// TRADING (R10, R11, R13, R14, R16, R37, R38; KTD4, KTD5)
//==================================================================
bool IsOwn(string sym, long magic) { return sym == _Symbol && magic == MagicNumber; }

// own open positions plus own pending orders (R16)
int CountExposures()
  {
   int cnt = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(IsOwn(PositionGetString(POSITION_SYMBOL), PositionGetInteger(POSITION_MAGIC))) cnt++;
     }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong t = OrderGetTicket(i);
      if(t == 0) continue;
      if(IsOwn(OrderGetString(ORDER_SYMBOL), OrderGetInteger(ORDER_MAGIC))) cnt++;
     }
   return cnt;
  }

// an own order in the same direction at the same price (R16)
bool OwnOrderAt(int dir, double price)
  {
   ENUM_ORDER_TYPE want = (dir == 1) ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong t = OrderGetTicket(i);
      if(t == 0) continue;
      if(!IsOwn(OrderGetString(ORDER_SYMBOL), OrderGetInteger(ORDER_MAGIC))) continue;
      if((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE) != want) continue;
      if(MathAbs(OrderGetDouble(ORDER_PRICE_OPEN) - price) < gTick / 2.0) return true;
     }
   return false;
  }

bool IsBuyType(ENUM_ORDER_TYPE t)
  {
   return t == ORDER_TYPE_BUY || t == ORDER_TYPE_BUY_LIMIT || t == ORDER_TYPE_BUY_STOP || t == ORDER_TYPE_BUY_STOP_LIMIT;
  }

// freeze level is read at each use (KTD4)
bool FreezeAllows(bool isBuy, double price, const MqlTick &tk)
  {
   long fl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   if(fl <= 0) return true;
   double ref = isBuy ? tk.ask : tk.bid;
   return MathAbs(ref - price) > fl * _Point;
  }

// broker rejection -> the closest KTD7 skip code (journal keeps the retcode)
string FailReason(uint rc)
  {
   if(rc == TRADE_RETCODE_INVALID_STOPS) return "skipped_sl_stops";
   if(rc == TRADE_RETCODE_NO_MONEY) return "skipped_margin";
   if(rc == TRADE_RETCODE_INVALID_VOLUME || rc == TRADE_RETCODE_LIMIT_VOLUME) return "skipped_volume";
   if(rc == TRADE_RETCODE_LIMIT_ORDERS || rc == TRADE_RETCODE_LIMIT_POSITIONS) return "skipped_cap";
   if(rc == TRADE_RETCODE_INVALID_PRICE || rc == TRADE_RETCODE_PRICE_OFF) return "skipped_too_close";
   return "skipped_broker_reject";
  }

// R10: Limit order on the first tick after the confirmation FVG's candle 3 closed. Never a market order,
// never a clamp: every failed check skips the setup with its reason code.
// R42: a TRADE_RETCODE_MARKET_CLOSED refusal keeps the setup in ST_CONFIRMED; it is retried on later ticks
// (at most once per MC_RETRY_GAP_MSC of tick time, at most MC_RETRY_MAX attempts) with the original
// entry/SL/TP, re-running every check below in the same order. ProcessBar ends the wait at a close beyond
// the OB (invalidated_confirmed) or after OrderExpiryBars bars since candle 3 (skipped_market_closed).
void PlaceSetup(Setup &s, const MqlTick &tk)
  {
   int  d   = s.dir;
   long msc = tk.time_msc;

   if(s.placeAttempts > 0 && msc - s.lastAttemptMsc < MC_RETRY_GAP_MSC) return;   // retry throttle (R42)
   s.placeAttempts++;
   s.lastAttemptMsc = msc;
   if(s.placeAttempts > 1) gCntMcRetries++;

   if(!s.priced)
     {
      // entry per EntryMode (R10); half-tick midpoints are rounded deeper into the zone
      double pe;
      if(EntryMode == ENTRY_FVG_EDGE)      pe = (d == 1) ? s.cHigh : s.cLow;
      else if(EntryMode == ENTRY_FVG_MID)  pe = RoundTick((s.cLow + s.cHigh) / 2.0, d != 1);
      else if(EntryMode == ENTRY_OB_EDGE)  pe = (d == 1) ? s.obHigh : s.obLow;
      else                                 pe = RoundTick((s.obLow + s.obHigh) / 2.0, d != 1);
      pe = NormalizeDouble(pe, gDigits);
      // R13: SL beyond the OB by the buffer (rounded away from entry); TP from the intended entry
      double buf = StopBufferPoints * _Point;
      double psl = (d == 1) ? RoundTick(s.obLow - buf, false) : RoundTick(s.obHigh + buf, true);
      double ptp = RoundTickNearest(pe + d * RiskRR * MathAbs(pe - psl));
      s.entry = pe; s.sl = psl; s.tp = ptp; s.priced = true;
     }
   double e = s.entry, sl = s.sl, tp = s.tp;              // retries keep the original prices (R42)

   // R37: the broker stops level, read at every placement - the only distance threshold
   long stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   s.stopsPts = stops;
   double px = (d == 1) ? tk.ask : tk.bid;
   if((d == 1 && px <= e) || (d == -1 && px >= e)) { Skip(s, "skipped_price_past", msc); return; }
   long distPts = (long)MathRound(MathAbs(px - e) / _Point);
   if(distPts < stops) { Skip(s, "skipped_too_close", msc); return; }
   long slPts = (long)MathRound((e - sl) * d / _Point);
   long tpPts = (long)MathRound((tp - e) * d / _Point);
   if(slPts <= 0 || tpPts <= 0 || slPts < stops || tpPts < stops) { Skip(s, "skipped_sl_stops", msc); return; }

   // R14 / KTD5: risk % of balance at placement, rounded down to the volume step
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double step      = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minVol    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxVol    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(tickValue <= 0 || tickSize <= 0 || step <= 0) { Skip(s, "skipped_volume", msc); return; }
   double riskMoney  = AccountInfoDouble(ACCOUNT_BALANCE) * RiskPercent / 100.0;
   double lossPerLot = MathAbs(e - sl) / tickSize * tickValue;
   if(lossPerLot <= 0) { Skip(s, "skipped_volume", msc); return; }
   double vol = NormalizeDouble(MathFloor(riskMoney / lossPerLot / step + 1e-9) * step, gVolDigits);
   s.volume = vol; s.sized = true;
   if(vol < minVol - 1e-9 || vol > maxVol + 1e-9) { Skip(s, "skipped_volume", msc); return; }
   double margin = 0;
   if(!OrderCalcMargin((d == 1) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, vol, e, margin) ||
      margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE)) { Skip(s, "skipped_margin", msc); return; }

   // R16: exposure cap and duplicate own order
   if(CountExposures() >= MaxExposures) { Skip(s, "skipped_cap", msc); return; }
   if(OwnOrderAt(d, e)) { Skip(s, "skipped_duplicate", msc); return; }

   string cmt = OrderComment(d, gT[s.obBar]);
   bool ok = (d == 1) ? trade.BuyLimit(vol, e, _Symbol, sl, tp, ORDER_TIME_GTC, 0, cmt)
                      : trade.SellLimit(vol, e, _Symbol, sl, tp, ORDER_TIME_GTC, 0, cmt);
   uint rc = trade.ResultRetcode();
   ulong ticket = trade.ResultOrder();
   if(!ok || ticket == 0 || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
     {
      Print("OBR setup #", s.id, " placement rejected, retcode ", rc, " ", trade.ResultComment(),
            ", attempt ", s.placeAttempts);
      if(rc == TRADE_RETCODE_MARKET_CLOSED)
        {
         if(s.mcFirstMsc == 0) s.mcFirstMsc = msc;
         if(s.placeAttempts >= MC_RETRY_MAX) { Skip(s, "skipped_market_closed", msc); return; }
         return;                                          // stays ST_CONFIRMED, retried later (R42)
        }
      Skip(s, FailReason(rc), msc);
      return;
     }
   s.ticket = ticket;
   s.placeMsc = msc;
   if(OrderSelect(ticket))
     {
      long setupMsc = OrderGetInteger(ORDER_TIME_SETUP_MSC);
      if(setupMsc > 0) s.placeMsc = setupMsc;
     }
   s.state = ST_PENDING;
   Print("OBR setup #", s.id, " ", DirStr(d), " ", (d == 1 ? "Buy" : "Sell"), " Limit ", DoubleToString(vol, gVolDigits),
         " @", Fmt(e), " SL ", Fmt(sl), " TP ", Fmt(tp), " ticket ", ticket, " ", cmt);
  }

// placements for OBs confirmed at the bar that just closed, plus market-closed retries (R42),
// oldest activation first (KTD2 step 6)
void PlaceQueued(const MqlTick &tk)
  {
   int q[];
   int nq = 0;
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(S[i].state != ST_CONFIRMED) continue;
      ArrayResize(q, nq + 1);
      int j = nq;
      while(j > 0 && S[q[j - 1]].actSeq > S[i].actSeq) { q[j] = q[j - 1]; j--; }
      q[j] = i;
      nq++;
     }
   for(int k = 0; k < nq; k++) PlaceSetup(S[q[k]], tk);
  }

// R11 cancellations scheduled at a bar close are executed here; a failed delete retries every tick
void ExecuteDeletes(const MqlTick &tk)
  {
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(S[i].state != ST_PENDING || !S[i].cancelDue) continue;
      if(!OrderSelect(S[i].ticket)) continue;              // left the book: the next sync resolves it (R38)
      double price = OrderGetDouble(ORDER_PRICE_OPEN);
      if(!FreezeAllows(S[i].dir == 1, price, tk)) continue; // inside the freeze level: retry next tick
      if(trade.OrderDelete(S[i].ticket) && trade.ResultRetcode() == TRADE_RETCODE_DONE)
        {
         Print("OBR setup #", S[i].id, " order ", S[i].ticket, " deleted: ", S[i].cancelReason);
         Retire(S[i], S[i].cancelReason, tk.time_msc);
        }
      else
         Print("OBR setup #", S[i].id, " delete failed, retcode ", trade.ResultRetcode(), " - retry next tick");
     }
  }

// KTD6: on the first live tick, delete own pending orders no in-memory setup owns
bool OwnedBySetup(ulong ticket)
  {
   for(int i = 0; i < ArraySize(S); i++)
      if(S[i].ticket == ticket && S[i].state != ST_DONE) return true;
   return false;
  }
void DeleteOrphans(const MqlTick &tk)
  {
   bool left = false;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong t = OrderGetTicket(i);
      if(t == 0) continue;
      if(!IsOwn(OrderGetString(ORDER_SYMBOL), OrderGetInteger(ORDER_MAGIC))) continue;
      if(OwnedBySetup(t)) continue;
      ENUM_ORDER_TYPE typ = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      double price = OrderGetDouble(ORDER_PRICE_OPEN);
      if(!FreezeAllows(IsBuyType(typ), price, tk)) { left = true; continue; }
      if(trade.OrderDelete(t) && trade.ResultRetcode() == TRADE_RETCODE_DONE)
         Print("OBR: deleted orphan own order ", t);
      else
         left = true;
     }
   gOrphansDone = !left;
  }

// fill detection: the own order left the book and a DEAL_ENTRY_IN deal of that order exists (R38)
void SyncPending(Setup &s, long nowMsc)
  {
   if(OrderSelect(s.ticket)) return;                  // still working
   datetime from = (datetime)(s.placeMsc / 1000 - 1);
   if(HistorySelect(from, TimeCurrent() + 86400))
     {
      for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
        {
         ulong dl = HistoryDealGetTicket(i);
         if(dl == 0) continue;
         if((ulong)HistoryDealGetInteger(dl, DEAL_ORDER) != s.ticket) continue;
         if(HistoryDealGetInteger(dl, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
         s.fillMsc   = HistoryDealGetInteger(dl, DEAL_TIME_MSC);
         s.fillPrice = HistoryDealGetDouble(dl, DEAL_PRICE);
         s.posId     = (ulong)HistoryDealGetInteger(dl, DEAL_POSITION_ID);
         s.state     = ST_FILLED;
         bool late   = s.cancelDue && s.fillMsc >= s.cancelDueMsc;
         SetReason(s, late ? "filled_late" : "filled", s.fillMsc);
         Print("OBR setup #", s.id, " ", DirStr(s.dir), " filled @", Fmt(s.fillPrice), " position ", s.posId,
               (late ? " (filled_late)" : ""));
         return;
        }
     }
   // no fill deal: removed from the book outside the EA (cancelled / expired / rejected by the server)
   if(HistoryOrderSelect(s.ticket))
     {
      long st = HistoryOrderGetInteger(s.ticket, ORDER_STATE);
      if(st == ORDER_STATE_CANCELED || st == ORDER_STATE_EXPIRED || st == ORDER_STATE_REJECTED)
        {
         Print("OBR setup #", s.id, " order ", s.ticket, " removed without fill outside the EA, state ", st);
         Retire(s, s.cancelDue ? s.cancelReason : "expired_unfilled", nowMsc);
        }
     }
  }

// exit detection: the position is gone; its closing deal gives time, price and SL/TP kind
void SyncPosition(Setup &s)
  {
   if(PositionSelectByTicket(s.posId)) return;       // still open
   if(!HistorySelectByPosition((long)s.posId)) return;
   ulong last = 0; long lastMsc = -1;
   for(int i = 0; i < HistoryDealsTotal(); i++)
     {
      ulong dl = HistoryDealGetTicket(i);
      if(dl == 0) continue;
      long en = HistoryDealGetInteger(dl, DEAL_ENTRY);
      if(en != DEAL_ENTRY_OUT && en != DEAL_ENTRY_OUT_BY) continue;
      long t = HistoryDealGetInteger(dl, DEAL_TIME_MSC);
      if(t >= lastMsc) { lastMsc = t; last = dl; }
     }
   if(last == 0) return;
   long rs = HistoryDealGetInteger(last, DEAL_REASON);
   s.exitMsc   = lastMsc;
   s.exitPrice = HistoryDealGetDouble(last, DEAL_PRICE);
   s.exitKind  = (rs == DEAL_REASON_SL) ? "sl" : (rs == DEAL_REASON_TP) ? "tp" : "end";
   CloseSetup(s);
  }

void SyncTrades(long nowMsc)
  {
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(S[i].state == ST_PENDING)     SyncPending(S[i], nowMsc);
      else if(S[i].state == ST_FILLED) SyncPosition(S[i]);
     }
  }

//==================================================================
// MT5 EVENTS
//==================================================================
int OnInit()
  {
   ArrayResize(gReasonCnt, ArraySize(gReasons));
   ArrayInitialize(gReasonCnt, 0);
#ifdef RESEARCH_LOG
   RL_Init();
#endif
   if(_Period != SignalTF)
     {
      Print("OBR: this EA runs on the ", EnumToString(SignalTF), " chart only (SignalTF). Current TF: ", EnumToString(_Period));
      return INIT_FAILED;
     }
   // KTD2 / R9: windows must be positive, the confirmation window at least 2 bars
   if(FvgWindowBars < 2 || ImpulseWindowBars <= 0 || BosWindowBars <= 0 || SwingStrength <= 0 || ObMaxAgeBars <= 0 ||
      OrderExpiryBars <= 0 || StopBufferPoints < 0 || RiskRR <= 0 || RiskPercent <= 0 || MaxExposures <= 0 || WarmupBars < 0 ||
      VolumeMultiplier < 0 || VolumeLookbackHours <= 0)
     {
      Print("OBR: invalid inputs (FvgWindowBars >= 2, windows > 0, RiskRR > 0, RiskPercent > 0, MaxExposures > 0, ",
            "VolumeMultiplier >= 0, VolumeLookbackHours > 0)");
      return INIT_PARAMETERS_INCORRECT;
     }
   // R41: lookback in closed bars = VolumeLookbackHours * 60 / period minutes (96 on M15, 288 on M5)
   long periodMin = PeriodSeconds(SignalTF) / 60;
   gVolN = (periodMin > 0) ? (int)((long)VolumeLookbackHours * 60 / periodMin) : 0;
   if(gVolN < 1)
     {
      Print("OBR: VolumeLookbackHours shorter than one ", EnumToString(SignalTF), " bar");
      return INIT_PARAMETERS_INCORRECT;
     }
   // KTD4: hedging account only
   if((ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
     {
      Print("OBR: a hedging account is required");
      return INIT_FAILED;
     }
   gTick   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   gDigits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   if(gTick <= 0) gTick = _Point;
   gPeriodSec = PeriodSeconds(SignalTF);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   gVolDigits = 0;
   while(step > 0 && gVolDigits < 8 && MathAbs(step - MathRound(step)) > 1e-9) { step *= 10.0; gVolDigits++; }
   gCmtPrefix = TradeComment;
   StringReplace(gCmtPrefix, ",", "");

   trade.SetExpertMagicNumber((ulong)MagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   // KTD6: warm-up replay of closed bars with trading disabled (no trading side effects here, R17)
   int got = 0;
   if(WarmupBars > 0)
     {
      MqlRates rates[];
      ArraySetAsSeries(rates, false);
      got = CopyRates(_Symbol, SignalTF, 1, WarmupBars, rates);
      if(got < 0) got = 0;
      for(int i = 0; i < got; i++) ProcessBar(rates[i], false, false);
      CompactSetups();
     }
   gOrphansDone = false;
   Print("OBR initialised. Warm-up bars: ", got, ", ob mode ", EnumToString(ObMode), ", entry mode ", EnumToString(EntryMode),
         ", volume lookback ", gVolN, " bars x", DoubleToString(VolumeMultiplier, 2),
         ", tracked setups ", ArraySize(S), ", tick ", DoubleToString(gTick, gDigits),
         ", stops level ", SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL), " pts");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   MqlTick tk;
   long nowMsc = (long)TimeCurrent() * 1000;
   double bid = 0, ask = 0;
   if(SymbolInfoTick(_Symbol, tk)) { nowMsc = tk.time_msc; bid = tk.bid; ask = tk.ask; }
   SyncTrades(nowMsc);
   for(int i = 0; i < ArraySize(S); i++)
     {
      int st = S[i].state;
      if(st == ST_CANDIDATE) Discard(S[i]);
      else if(st == ST_ACTIVE || st == ST_TOUCHED || st == ST_CONFIRMED || st == ST_PENDING)
         Retire(S[i], "run_end_pending", nowMsc);
      else if(st == ST_FILLED)
        {
         // position still open at run end: marked at the current price
         S[i].exitKind  = "end";
         S[i].exitMsc   = nowMsc;
         S[i].exitPrice = (S[i].dir == 1) ? bid : ask;
         CloseSetup(S[i]);
        }
     }
   CompactSetups();
#ifdef RESEARCH_LOG
   RL_Deinit();
#endif
   PrintFunnel();
  }

void OnTick()
  {
#ifdef RESEARCH_LOG
   RL_OnTick();
#endif
   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk)) return;
   if(!gOrphansDone) DeleteOrphans(tk);
   // fills / exits that happened up to this tick, before the closed bar is evaluated (R38, AE6)
   SyncTrades(tk.time_msc);

   datetime t1 = iTime(_Symbol, SignalTF, 1);
   if(t1 > gLastTime)
     {
      MqlRates rates[];
      ArraySetAsSeries(rates, false);
      int got = (gLastTime > 0) ? CopyRates(_Symbol, SignalTF, (datetime)(gLastTime + 1), t1, rates)
                                : CopyRates(_Symbol, SignalTF, 1, 1, rates);
      for(int i = 0; i < got; i++)
        {
         if(rates[i].time <= gLastTime) continue;
         // only the bar that just closed may lead to a placement on this tick (R10)
         ProcessBar(rates[i], true, rates[i].time == t1);
        }
     }
   ExecuteDeletes(tk);
   PlaceQueued(tk);
   CompactSetups();
  }
//+------------------------------------------------------------------+
