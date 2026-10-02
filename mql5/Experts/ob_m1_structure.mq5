//+------------------------------------------------------------------+
//|                                              ob_m1_structure.mq5 |
//|  M5 Order Block (found from its identifying FVG) -> first touch  |
//|  -> M1 structure change (A: HH; B: HH + confirmed HL) -> M1 entry |
//|  FVG -> reaction candle -> Market entry with a structural SL.     |
//|  Plan: docs/plans/2026-10-03-0013-feat-m5-ob-m1-structure-ea-plan |
//|  Rules R3-R24, KTD1-KTD16. Runs on the M1 chart, reads closed M5  |
//|  bars with CopyRates. Long is described; short mirrors.           |
//+------------------------------------------------------------------+
#property copyright ""
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//==================================================================
// INPUTS (order, names and defaults fixed by research/mt5r/m1_contract.py)
//==================================================================
enum ENUM_STRUCTURE_VARIANT { SV_HH_ONLY = 0, SV_HH_HL = 1 };

input ENUM_STRUCTURE_VARIANT StructureVariant = SV_HH_ONLY; // A = HH only, B = HH + confirmed HL (R11)
input int    ImpulseWindowBars = 2;      // look-back from identifying-FVG candle 1 for the OB candle, M5 bars (R3)
input int    SwingStrengthM1   = 3;      // M1 pivot strength N, bars each side (R9)
input int    StopBufferPoints  = 20;     // SL beyond the farther structural level, points (R18)
input double RiskRR            = 2.0;    // target multiple of the fill-to-SL distance (R19)
input double RiskPercent       = 1.0;    // % of balance risked per trade (R20)
input int    MaxExposures      = 3;      // open positions at once (R21)
input int    WarmupDays        = 30;     // calendar days of closed M5/M1 bars replayed with trading off (KTD8)
input long   MagicNumber       = 770201; // magic number
input string TradeComment      = "OBM1"; // order comment prefix (commas are removed)
#ifdef RESEARCH_LOG
input string ResearchRunTag    = "";     // tag for research CSV files ("" = no CSV)
#endif

//==================================================================
// CONSTANTS / TYPES
//==================================================================
// per-setup state machine (plan High-Level Technical Design); BROKEN is the orthogonal flag Setup.broken
#define ST_OB_WAIT        0   // OB identified, waiting for the first touch (R4, R5)
#define ST_TRACK          1   // touched, waiting for an R10 structure change
#define ST_SC_PENDING_FVG 2   // structure change at bar k, the entry FVG is decided at the close of k+1 (R12)
#define ST_ARMED          3   // entry FVG fixed, variant B waits for the HL (R11)
#define ST_READY          4   // waiting for a reaction candle (R14)
#define ST_FILLED         5   // position open, only SL/TP manage it (R19)
#define ST_DONE           6   // finished; removed from the working lists

#define FUNNEL_LINE_MAX   180 // MT5's journal truncates long lines: the funnel is split over several lines

struct Piv                  // one M1 pivot (KTD3); id = index + 1
  {
   int      id;
   int      type;           // 1 = high, -1 = low
   int      peak;           // M1 index of the peak bar
   int      conf;           // M1 index of bar p+N, whose close confirms the pivot
   double   lvl;
   int      replacedBy;     // id of the pivot that won the compression against this one, 0 = none
   bool     outside;        // the bar is both a strict pivot high and a strict pivot low
   int      prev1;          // gPiv index of the preceding sequence element when appended (-1 = none)
   int      prev2;          // gPiv index of the element before that (-1 = none)
  };

struct Cand                 // R22: one opposing swing sequence of a setup, waiting for completion or void
  {
   int      setupId;
   int      x2;             // long: the lower high H2; short: the higher low L2 (gPiv index)
   int      x1;             // long: the preceding high H1; short: the preceding low L1
   int      mid;            // long: the low L1 between them; short: the high H1 between them
   int      conf;           // M1 index of x2's confirmation bar; completion needs a later close
  };

struct Setup
  {
   int      id;
   int      dir;            // 1 long, -1 short
   int      state;
   // M5 OB and its identifying FVG (R3)
   datetime obTime;
   double   obHigh;
   double   obLow;
   datetime idC1Time;
   datetime idC3Time;
   int      idC3;           // M5 index of candle 3
   datetime c3Close;
   double   idLow;
   double   idHigh;
   bool     inWarmup;       // identified during the warm-up replay
   // touch (R5), break / return (R6-R8)
   long     touchMsc;
   datetime touchBarTime;   // open time of the M1 bar containing the touch tick
   int      ageBarsTouch;
   long     ageMinTouch;
   bool     broken;
   int      breaks;
   int      returns;
   // structure change (R10, KTD5); kept after it ends, for the setup row
   int      lastRefId;      // id of the latest reference pivot that produced an R10 event
   int      refPiv;
   int      scBar;
   int      origin;
   // entry FVG (R12)
   int      fvgC1;
   int      fvgC3;
   double   fvgLo;
   double   fvgHi;
   // HL / LH (R11)
   bool     hlDecided;
   int      hlPiv;
   int      hlConf;
   // reaction and entry (R14-R16, KTD7)
   int      reactBar;
   bool     queued;         // a reaction candle waits for the entry routine
   bool     competed;       // the queued reaction already won R16
   bool     mcWait;         // last attempt was refused as market closed; retried on later ticks
   int      attempts;
   long     reqMsc;
   double   reqPrice;
   bool     priced;
   double   sl;
   string   slAnchor;
   double   slAnchorPrice;
   double   tp;
   bool     sized;
   double   volume;
   // fill / exit
   long     fillMsc;
   double   fillPrice;
   ulong    posId;
   int      ageBarsEntry;
   long     ageMinEntry;
   long     exitMsc;
   double   exitPrice;
   string   exitKind;
   // outcome
   string   reason;
   long     reasonMsc;
  };

//==================================================================
// GLOBAL STATE
//==================================================================
// closed M1 bars by absolute index (0 = first processed bar)
double   gO1[], gH1[], gL1[], gC1[];
datetime gT1[];
int      gM1 = 0;
datetime gLast1 = 0;          // open time of the last processed M1 bar
datetime gCurM1 = 0;          // open time of the forming M1 bar at the last successful fetch
// closed M5 bars by absolute index
double   gO5[], gH5[], gL5[], gC5[];
datetime gT5[];
int      gM5 = 0;
datetime gLast5 = 0;
datetime gCurM5 = 0;

// the global causal M1 pivot sequence (KTD3)
Piv      gPiv[];
int      gSeq[];              // gPiv indices of the alternating sequence; only the first gSeqN are valid
int      gSeqN = 0;
int      gLastHigh = -1;      // gPiv index of the latest pivot high in the sequence
int      gLastLow = -1;
int      gNewPiv[];           // gPiv indices appended to the sequence at the current M1 close

datetime gUsedOb[];           // M5 OB candle times already used (R3: at most once)
Setup    gOb[];               // untouched OBs (ST_OB_WAIT)
Setup    S[];                 // touched setups (waiting or filled)
Cand     gCand[];             // live R22 sequences of all setups
bool     gDirtyOb = false;
bool     gDirtyS = false;
int      gNextId = 1;
long     gEvSeq = 0;

// KTD9: per-tick edge caches of the untouched OBs
bool     gHasLong = false, gHasShort = false;
double   gCacheLong = 0.0;    // highest untouched long OB high
double   gCacheShort = 0.0;   // lowest untouched short OB low
int      gBrokenCnt = 0;      // setups in a break episode (returns are checked only for them)
bool     gAnyQueued = false;

CTrade   trade;
double   gTick = 0.0;
int      gDigits = 0;
int      gVolDigits = 2;
string   gCmtPrefix = "";

// funnel
int      gCntIdentified = 0, gCntTouched = 0, gCntSc = 0, gCntFvgFixed = 0, gCntReady = 0, gCntReactions = 0;
int      gCntEntries = 0, gCntFilled = 0, gCntCancel2nd = 0, gCntCancelOpp = 0, gCntWarmupDropped = 0;
int      gCntRunEndWaiting = 0, gCntRunEndUntouched = 0, gCntMcRetries = 0;
string   gSkipCodes[] = {"skipped_stop_crossed", "skipped_stops_level", "skipped_volume", "skipped_margin",
                         "skipped_cap", "skipped_broker_reject", "lost_competition"};
int      gSkipCnt[];

//==================================================================
// SMALL HELPERS
//==================================================================
string   Fmt(double p) { return DoubleToString(p, gDigits); }
string   DirStr(int d) { return d == 1 ? "LONG" : "SHORT"; }
datetime MinuteOf(long msc) { long s = msc / 1000; return (datetime)(s - s % 60); }
long     BarCloseMsc1(int i) { return ((long)gT1[i] + 60) * 1000; }
datetime T1(int i) { return (i >= 0 && i < gM1) ? gT1[i] : 0; }

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

string OrderComment(int dir, int id)
  {
   return gCmtPrefix + "#" + (dir == 1 ? "L" : "S") + "#" + IntegerToString(id);
  }

void InitSetup(Setup &s)
  {
   s.id = 0; s.dir = 0; s.state = ST_OB_WAIT;
   s.obTime = 0; s.obHigh = 0; s.obLow = 0;
   s.idC1Time = 0; s.idC3Time = 0; s.idC3 = -1; s.c3Close = 0; s.idLow = 0; s.idHigh = 0; s.inWarmup = false;
   s.touchMsc = 0; s.touchBarTime = 0; s.ageBarsTouch = -1; s.ageMinTouch = -1;
   s.broken = false; s.breaks = 0; s.returns = 0;
   s.lastRefId = 0; s.refPiv = -1; s.scBar = -1; s.origin = -1;
   s.fvgC1 = -1; s.fvgC3 = -1; s.fvgLo = 0; s.fvgHi = 0;
   s.hlDecided = false; s.hlPiv = -1; s.hlConf = -1;
   s.reactBar = -1; s.queued = false; s.competed = false; s.mcWait = false; s.attempts = 0;
   s.reqMsc = 0; s.reqPrice = 0; s.priced = false; s.sl = 0; s.slAnchor = ""; s.slAnchorPrice = 0; s.tp = 0;
   s.sized = false; s.volume = 0;
   s.fillMsc = 0; s.fillPrice = 0; s.posId = 0; s.ageBarsEntry = -1; s.ageMinEntry = -1;
   s.exitMsc = 0; s.exitPrice = 0; s.exitKind = "";
   s.reason = ""; s.reasonMsc = 0;
  }

bool Waiting(const Setup &s) { return s.state >= ST_TRACK && s.state <= ST_READY; }

//==================================================================
// RESEARCH LOGGING (research build only). Reads account/symbol state
// and writes files; it never sends, modifies or closes orders.
// rl_days / rl_deals / OnTester / frames are ported unchanged from the
// archived OB-FVG retest EA; the other files follow KTD10 and
// research/mt5r/m1_contract.py.
//==================================================================
#ifdef RESEARCH_LOG
#define RL_DAY_FIELDS  8
#define RL_SPREAD_BINS 20000
#define RL_BARS_HEADER "time,open,high,low,close,tick_volume,spread,warmup"
#define RL_PIVOTS_HEADER "pivot_id,type,peak_time,conf_time,level,replaced_by,outside_bar"
#define RL_EVENTS_HEADER "setup_id,seq,kind,bar_time,tick_msc,price,lo,hi,ref_id,ref_time,detail"
#define RL_SETUPS_HEADER "setup_id,dir,variant,ob_time,ob_high,ob_low,idfvg_c1_time,idfvg_c3_time,idfvg_low,idfvg_high,identified_in_warmup,touch_msc,touch_bar_time,ob_age_bars_touch,ob_age_min_touch,breaks,returns,sc_bar_time,origin_time,ref_pivot_id,hl_pivot_id,fvg_c1_time,fvg_low,fvg_high,reaction_bar_time,entry_request_msc,request_price,attempts,sl,sl_anchor,sl_anchor_price,buffer_pts,tp,volume,fill_msc,fill_price,position_id,ob_age_bars_entry,ob_age_min_entry,exit_msc,exit_price,exit_kind,reason,reason_msc"
datetime rlDay = 0;
double   rlBalOpen = 0, rlEqOpen = 0, rlEqMin = 0, rlEqMax = 0, rlBalClose = 0, rlEqClose = 0;
int      rlSpreadHist[RL_SPREAD_BINS];
long     rlSpreadN = 0;
double   rlDays[];            // flattened day records: date, balOpen, eqOpen, eqMin, eqMax, balClose, eqClose, spreadMedian
bool     rlFinalized = false;
bool     rlOn = false;        // setup/event/bar rows are collected only when files will be written
string   rlSetupRows[];
string   rlEventRows[];
MqlRates rlBars1[];
MqlRates rlBars5[];
int      rlWarm1 = 0;         // the first rlWarm1 rows of rlBars1 are warm-up bars
int      rlWarm5 = 0;

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
   ArrayResize(rlEventRows, 0);
   ArrayResize(rlBars1, 0);
   ArrayResize(rlBars5, 0);
   rlWarm1 = 0;
   rlWarm5 = 0;
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
// every processed closed bar, warm-up included (KTD8: the checker rebuilds pivots and OBs from the same first bar)
void RL_AddBar(int tf, const MqlRates &r, bool warm)
  {
   if(!rlOn) return;
   if(tf == 1)
     {
      int sz = ArraySize(rlBars1);
      ArrayResize(rlBars1, sz + 1, 65536);
      rlBars1[sz] = r;
      if(warm) rlWarm1 = sz + 1;
     }
   else
     {
      int sz = ArraySize(rlBars5);
      ArrayResize(rlBars5, sz + 1, 16384);
      rlBars5[sz] = r;
      if(warm) rlWarm5 = sz + 1;
     }
  }
string RL_T(datetime t)            { return t > 0 ? IntegerToString((long)t) : ""; }
string RL_P(double p, bool has)    { return has ? DoubleToString(p, gDigits) : ""; }
string RL_L(long v, bool has)      { return has ? IntegerToString(v) : ""; }
string RL_PivId(int idx)           { return idx >= 0 ? IntegerToString(gPiv[idx].id) : ""; }

void RL_Event(long seq, int setupId, string kind, datetime barTime, long msc, double price, double lo, double hi,
              long refId, datetime refTime, string detail)
  {
   if(!rlOn) return;
   string row = IntegerToString(setupId)
                + "," + IntegerToString(seq)
                + "," + kind
                + "," + RL_T(barTime)
                + "," + RL_L(msc, msc > 0)
                + "," + RL_P(price, price != EMPTY_VALUE)
                + "," + RL_P(lo, lo != EMPTY_VALUE)
                + "," + RL_P(hi, hi != EMPTY_VALUE)
                + "," + RL_L(refId, refId > 0)
                + "," + RL_T(refTime)
                + "," + detail;
   int sz = ArraySize(rlEventRows);
   ArrayResize(rlEventRows, sz + 1, 16384);
   rlEventRows[sz] = row;
  }

// one rl_setups row of final facts per setup
void RL_AddSetupRow(const Setup &s)
  {
   if(!rlOn) return;
   bool touched = (s.touchBarTime > 0);
   bool filled  = (s.fillMsc > 0);
   bool exited  = (s.exitKind != "");
   bool fvg     = (s.fvgC1 >= 0);
   string row = IntegerToString(s.id)
                + "," + (s.dir == 1 ? "L" : "S")
                + "," + IntegerToString((int)StructureVariant)
                + "," + RL_T(s.obTime)
                + "," + RL_P(s.obHigh, true)
                + "," + RL_P(s.obLow, true)
                + "," + RL_T(s.idC1Time)
                + "," + RL_T(s.idC3Time)
                + "," + RL_P(s.idLow, true)
                + "," + RL_P(s.idHigh, true)
                + "," + (s.inWarmup ? "1" : "0")
                + "," + RL_L(s.touchMsc, s.touchMsc > 0)
                + "," + RL_T(s.touchBarTime)
                + "," + RL_L(s.ageBarsTouch, touched && s.ageBarsTouch >= 0)
                + "," + RL_L(s.ageMinTouch, touched && s.ageMinTouch >= 0)
                + "," + IntegerToString(s.breaks)
                + "," + IntegerToString(s.returns)
                + "," + RL_T(T1(s.scBar))
                + "," + RL_T(T1(s.origin))
                + "," + RL_PivId(s.refPiv)
                + "," + RL_PivId(s.hlPiv)
                + "," + RL_T(T1(s.fvgC1))
                + "," + RL_P(s.fvgLo, fvg)
                + "," + RL_P(s.fvgHi, fvg)
                + "," + RL_T(T1(s.reactBar))
                + "," + RL_L(s.reqMsc, s.reqMsc > 0)
                + "," + RL_P(s.reqPrice, s.reqMsc > 0)
                + "," + IntegerToString(s.attempts)
                + "," + RL_P(s.sl, s.priced)
                + "," + s.slAnchor
                + "," + RL_P(s.slAnchorPrice, s.priced)
                + "," + (s.priced ? IntegerToString(StopBufferPoints) : "")
                + "," + RL_P(s.tp, filled)
                + "," + (s.sized ? DoubleToString(s.volume, gVolDigits) : "")
                + "," + RL_L(s.fillMsc, filled)
                + "," + RL_P(s.fillPrice, filled)
                + "," + RL_L((long)s.posId, filled)
                + "," + RL_L(s.ageBarsEntry, filled)
                + "," + RL_L(s.ageMinEntry, filled)
                + "," + RL_L(s.exitMsc, exited)
                + "," + RL_P(s.exitPrice, exited)
                + "," + s.exitKind
                + "," + s.reason
                + "," + IntegerToString(s.reasonMsc);
   int sz = ArraySize(rlSetupRows);
   ArrayResize(rlSetupRows, sz + 1, 4096);
   rlSetupRows[sz] = row;
  }

void RL_WriteBars(string name, string header, const MqlRates &bars[], int warm)
  {
   int h = FileOpen(name, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   FileWriteString(h, header);
   FileWriteString(h, "\r\n");
   for(int i = 0; i < ArraySize(bars); i++)
      FileWriteString(h, IntegerToString((long)bars[i].time) + "," + DoubleToString(bars[i].open, gDigits) + "," +
                      DoubleToString(bars[i].high, gDigits) + "," + DoubleToString(bars[i].low, gDigits) + "," +
                      DoubleToString(bars[i].close, gDigits) + "," + IntegerToString(bars[i].tick_volume) + "," +
                      IntegerToString(bars[i].spread) + "," + (i < warm ? "1" : "0") + "\r\n");
   FileClose(h);
  }

void RL_WriteRows(string name, string header, const string &rows[])
  {
   int h = FileOpen(name, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   FileWriteString(h, header);
   FileWriteString(h, "\r\n");
   for(int i = 0; i < ArraySize(rows); i++)
      FileWriteString(h, rows[i] + "\r\n");
   FileClose(h);
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
   RL_WriteRows("rl_setups_" + ResearchRunTag + ".csv", RL_SETUPS_HEADER, rlSetupRows);
   RL_WriteRows("rl_events_" + ResearchRunTag + ".csv", RL_EVENTS_HEADER, rlEventRows);
   // rl_pivots: every pivot ever detected, the compression losers included (KTD3)
   h = FileOpen("rl_pivots_" + ResearchRunTag + ".csv", FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h != INVALID_HANDLE)
     {
      FileWriteString(h, RL_PIVOTS_HEADER);
      FileWriteString(h, "\r\n");
      for(int i = 0; i < ArraySize(gPiv); i++)
         FileWriteString(h, IntegerToString(gPiv[i].id) + "," + (gPiv[i].type == 1 ? "H" : "L") + "," +
                         IntegerToString((long)gT1[gPiv[i].peak]) + "," + IntegerToString((long)gT1[gPiv[i].conf]) + "," +
                         DoubleToString(gPiv[i].lvl, gDigits) + "," +
                         (gPiv[i].replacedBy > 0 ? IntegerToString(gPiv[i].replacedBy) : "") + "," +
                         (gPiv[i].outside ? "1" : "0") + "\r\n");
      FileClose(h);
     }
   RL_WriteBars("rl_bars_m1_" + ResearchRunTag + ".csv", RL_BARS_HEADER, rlBars1, rlWarm1);
   RL_WriteBars("rl_bars_m5_" + ResearchRunTag + ".csv", RL_BARS_HEADER, rlBars5, rlWarm5);
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
// EVENTS, FINAL REASONS AND FUNNEL (KTD10, KTD16)
//==================================================================
// one rl_events row (research build); EMPTY_VALUE / 0 = column not applicable
void Ev(int setupId, string kind, datetime barTime, long msc = 0, double price = EMPTY_VALUE, double lo = EMPTY_VALUE,
        double hi = EMPTY_VALUE, long refId = 0, datetime refTime = 0, string detail = "")
  {
   gEvSeq++;
#ifdef RESEARCH_LOG
   RL_Event(gEvSeq, setupId, kind, barTime, msc, price, lo, hi, refId, refTime, detail);
#endif
  }

void CountSkip(string code)
  {
   for(int i = 0; i < ArraySize(gSkipCodes); i++)
      if(gSkipCodes[i] == code) { gSkipCnt[i]++; return; }
   Print("OBM1: unknown skip code ", code);
  }
int SkipCount(string code)
  {
   for(int i = 0; i < ArraySize(gSkipCodes); i++)
      if(gSkipCodes[i] == code) return gSkipCnt[i];
   return 0;
  }

void CountReason(string code)
  {
   if(code == "cancelled_second_break")             gCntCancel2nd++;
   else if(code == "cancelled_opposing_structure")  gCntCancelOpp++;
   else if(code == "run_end_waiting")               gCntRunEndWaiting++;
   else if(code == "run_end_untouched")             gCntRunEndUntouched++;
   else if(code == "warmup_dropped")                gCntWarmupDropped++;
   else if(code != "filled")                        Print("OBM1: unknown reason code ", code);
  }

string KV(string key, int v) { return " " + key + "=" + IntegerToString(v); }

// appends one key=value to the current funnel line; a line that would exceed FUNNEL_LINE_MAX characters is
// printed first and a new "Funnel:" line started (MT5's journal truncates long lines; journal.py merges them)
void FunnelAdd(string &line, string key, int v)
  {
   string kv = KV(key, v);
   if(StringLen(line) + StringLen(kv) > FUNNEL_LINE_MAX && line != "Funnel:")
     {
      Print(line);
      line = "Funnel:";
     }
   line += kv;
  }

void PrintFunnel()
  {
   string line = "Funnel:";
   FunnelAdd(line, "identified", gCntIdentified);
   FunnelAdd(line, "touched", gCntTouched);
   FunnelAdd(line, "structure_changes", gCntSc);
   FunnelAdd(line, "fvg_fixed", gCntFvgFixed);
   FunnelAdd(line, "ready", gCntReady);
   FunnelAdd(line, "reactions", gCntReactions);
   FunnelAdd(line, "entries", gCntEntries);
   FunnelAdd(line, "filled", gCntFilled);
   FunnelAdd(line, "cancelled_second_break", gCntCancel2nd);
   FunnelAdd(line, "cancelled_opposing_structure", gCntCancelOpp);
   FunnelAdd(line, "skipped_stop_crossed", SkipCount("skipped_stop_crossed"));
   FunnelAdd(line, "skipped_stops_level", SkipCount("skipped_stops_level"));
   FunnelAdd(line, "skipped_volume", SkipCount("skipped_volume"));
   FunnelAdd(line, "skipped_margin", SkipCount("skipped_margin"));
   FunnelAdd(line, "skipped_cap", SkipCount("skipped_cap"));
   FunnelAdd(line, "skipped_broker_reject", SkipCount("skipped_broker_reject"));
   FunnelAdd(line, "lost_competition", SkipCount("lost_competition"));
   FunnelAdd(line, "warmup_dropped", gCntWarmupDropped);
   FunnelAdd(line, "run_end_waiting", gCntRunEndWaiting);
   FunnelAdd(line, "run_end_untouched", gCntRunEndUntouched);
   FunnelAdd(line, "market_closed_retries", gCntMcRetries);
   Print(line);
  }

void RemoveCands(int setupId)
  {
   int w = 0;
   for(int r = 0; r < ArraySize(gCand); r++)
     {
      if(gCand[r].setupId == setupId) continue;
      if(w != r) gCand[w] = gCand[r];
      w++;
     }
   ArrayResize(gCand, w, 256);
  }

// final bookkeeping for a setup: its rl_setups row; the setup leaves the working lists at the next compaction
void CloseSetup(Setup &s)
  {
   if(s.state == ST_OB_WAIT) gDirtyOb = true;
   else                      gDirtyS = true;
   s.state = ST_DONE;
   RemoveCands(s.id);
#ifdef RESEARCH_LOG
   RL_AddSetupRow(s);
#endif
  }

void SetReason(Setup &s, string code, long msc)
  {
   if(s.reason != "") return;
   s.reason = code;
   s.reasonMsc = msc;
  }

// a final reason (KTD16): cancellations, run end and warm-up drops end the setup here
void Finalize(Setup &s, string code, long msc)
  {
   SetReason(s, code, msc);
   CountReason(code);
   CloseSetup(s);
  }

void CompactOb()
  {
   if(!gDirtyOb) return;
   int w = 0;
   for(int r = 0; r < ArraySize(gOb); r++)
     {
      if(gOb[r].state != ST_OB_WAIT) continue;
      if(w != r) gOb[w] = gOb[r];
      w++;
     }
   ArrayResize(gOb, w, 256);
   gDirtyOb = false;
  }
void CompactS()
  {
   if(!gDirtyS) return;
   int w = 0;
   for(int r = 0; r < ArraySize(S); r++)
     {
      if(S[r].state == ST_DONE) continue;
      if(w != r) S[w] = S[r];
      w++;
     }
   ArrayResize(S, w, 256);
   gDirtyS = false;
  }

// KTD9: highest untouched long OB high, lowest untouched short OB low
void RecomputeCaches()
  {
   gHasLong = false; gHasShort = false;
   for(int i = 0; i < ArraySize(gOb); i++)
     {
      if(gOb[i].state != ST_OB_WAIT) continue;
      if(gOb[i].dir == 1)
        {
         if(!gHasLong || gOb[i].obHigh > gCacheLong) gCacheLong = gOb[i].obHigh;
         gHasLong = true;
        }
      else
        {
         if(!gHasShort || gOb[i].obLow < gCacheShort) gCacheShort = gOb[i].obLow;
         gHasShort = true;
        }
     }
  }

//==================================================================
// BARS AND THE M1 PIVOT SEQUENCE (R9, KTD3)
//==================================================================
int AppendM1(const MqlRates &r, bool warm)
  {
   int n = gM1;
   ArrayResize(gO1, n + 1, 65536); ArrayResize(gH1, n + 1, 65536); ArrayResize(gL1, n + 1, 65536);
   ArrayResize(gC1, n + 1, 65536); ArrayResize(gT1, n + 1, 65536);
   gO1[n] = r.open; gH1[n] = r.high; gL1[n] = r.low; gC1[n] = r.close; gT1[n] = r.time;
   gM1 = n + 1;
   gLast1 = r.time;
#ifdef RESEARCH_LOG
   RL_AddBar(1, r, warm);
#endif
   return n;
  }

int AppendM5(const MqlRates &r, bool warm)
  {
   int n = gM5;
   ArrayResize(gO5, n + 1, 16384); ArrayResize(gH5, n + 1, 16384); ArrayResize(gL5, n + 1, 16384);
   ArrayResize(gC5, n + 1, 16384); ArrayResize(gT5, n + 1, 16384);
   gO5[n] = r.open; gH5[n] = r.high; gL5[n] = r.low; gC5[n] = r.close; gT5[n] = r.time;
   gM5 = n + 1;
   gLast5 = r.time;
#ifdef RESEARCH_LOG
   RL_AddBar(5, r, warm);
#endif
   return n;
  }

// strict pivot tests (ties are not pivots), R9
bool IsPivotHigh(int p, int str)
  {
   if(p - str < 0 || p + str >= gM1) return false;
   for(int j = p - str; j <= p + str; j++)
     {
      if(j == p) continue;
      if(!(gH1[p] > gH1[j])) return false;
     }
   return true;
  }
bool IsPivotLow(int p, int str)
  {
   if(p - str < 0 || p + str >= gM1) return false;
   for(int j = p - str; j <= p + str; j++)
     {
      if(j == p) continue;
      if(!(gL1[p] < gL1[j])) return false;
     }
   return true;
  }

// KTD3: append to the alternating sequence; of two consecutive same-type pivots only the more extreme stays (a tie
// keeps the earlier). The loser keeps its id and records the winner in replacedBy; no recorded event is rewritten.
void AppendPivot(int type, int p, int n, bool outside)
  {
   int idx = ArraySize(gPiv);
   ArrayResize(gPiv, idx + 1, 4096);
   gPiv[idx].id = idx + 1;
   gPiv[idx].type = type;
   gPiv[idx].peak = p;
   gPiv[idx].conf = n;
   gPiv[idx].lvl = (type == 1) ? gH1[p] : gL1[p];
   gPiv[idx].replacedBy = 0;
   gPiv[idx].outside = outside;
   gPiv[idx].prev1 = -1;
   gPiv[idx].prev2 = -1;
   int last = (gSeqN > 0) ? gSeq[gSeqN - 1] : -1;
   if(last >= 0 && gPiv[last].type == type)
     {
      bool more = (type == 1) ? (gPiv[idx].lvl > gPiv[last].lvl) : (gPiv[idx].lvl < gPiv[last].lvl);
      if(!more)
        {
         gPiv[idx].replacedBy = gPiv[last].id;      // the earlier pivot stays
         return;
        }
      gPiv[last].replacedBy = gPiv[idx].id;
      gSeqN--;
     }
   gPiv[idx].prev1 = (gSeqN >= 1) ? gSeq[gSeqN - 1] : -1;
   gPiv[idx].prev2 = (gSeqN >= 2) ? gSeq[gSeqN - 2] : -1;
   if(ArraySize(gSeq) < gSeqN + 1) ArrayResize(gSeq, gSeqN + 1, 4096);
   gSeq[gSeqN] = idx;
   gSeqN++;
   if(type == 1) gLastHigh = idx;
   else          gLastLow = idx;
   int k = ArraySize(gNewPiv);
   ArrayResize(gNewPiv, k + 1, 4);
   gNewPiv[k] = idx;
  }

// KTD2 step 1: a pivot at p exists only once bar p+N has closed (R9). An outside bar that is both a strict pivot
// high and low appends first the type opposite to the last element, so both survive (KTD3); on an empty
// sequence the high goes first.
void UpdatePivots(int n)
  {
   ArrayResize(gNewPiv, 0, 4);
   int p = n - SwingStrengthM1;
   bool ph = IsPivotHigh(p, SwingStrengthM1);
   bool pl = IsPivotLow(p, SwingStrengthM1);
   if(ph && pl)
     {
      int lastType = (gSeqN > 0) ? gPiv[gSeq[gSeqN - 1]].type : 0;
      if(lastType == 1) { AppendPivot(-1, p, n, true); AppendPivot(1, p, n, true); }
      else              { AppendPivot(1, p, n, true);  AppendPivot(-1, p, n, true); }
     }
   else if(ph) AppendPivot(1, p, n, false);
   else if(pl) AppendPivot(-1, p, n, false);
  }

//==================================================================
// M5 ZONE (R3, R4)
//==================================================================
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
   datetime oldest = gT5[oldestBar];
   for(int r = 0; r < ArraySize(gUsedOb); r++)
     {
      if(gUsedOb[r] < oldest) continue;
      gUsedOb[w] = gUsedOb[r];
      w++;
     }
   ArrayResize(gUsedOb, w + 1, 64);
   gUsedOb[w] = t;
  }

// R3: identifying FVG completed at M5 bar n -> the most recent opposite-colour candle from candle 1 back
// ImpulseWindowBars bars is the OB (FVG-only mode of the approved definition; no BOS, sweep or volume condition).
void NewCandidate(int dir, int n, bool warm)
  {
   int c1 = n - 2;
   int ob = -1;
   for(int j = c1; j >= 0 && j > c1 - ImpulseWindowBars; j--)
     {
      bool opposite = (dir == 1) ? (gC5[j] < gO5[j]) : (gC5[j] > gO5[j]);   // a doji is neither
      if(opposite) { ob = j; break; }
     }
   if(ob < 0) return;
   if(ObUsed(gT5[ob])) return;                       // each candle becomes an OB at most once
   MarkObUsed(gT5[ob], n);
   // an M5 close beyond the OB between the OB candle and candle 3 discards the candidate
   for(int j = ob + 1; j <= n; j++)
      if((dir == 1) ? (gC5[j] < gL5[ob]) : (gC5[j] > gH5[ob])) return;

   Setup s; InitSetup(s);
   s.id = gNextId++;
   s.dir = dir;
   s.state = ST_OB_WAIT;
   s.obTime = gT5[ob]; s.obHigh = gH5[ob]; s.obLow = gL5[ob];
   s.idC1Time = gT5[c1]; s.idC3Time = gT5[n]; s.idC3 = n;
   s.c3Close = gT5[n] + PeriodSeconds(PERIOD_M5);
   s.idLow  = (dir == 1) ? gH5[c1] : gH5[n];
   s.idHigh = (dir == 1) ? gL5[n]  : gL5[c1];
   s.inWarmup = warm;
   int sz = ArraySize(gOb);
   ArrayResize(gOb, sz + 1, 256);
   gOb[sz] = s;
   gCntIdentified++;
   RecomputeCaches();
  }

// KTD2 step 3: a newly closed M5 bar only creates candidates
void M5Close(int n, bool warm)
  {
   if(n < 2) return;
   if(gL5[n] > gH5[n - 2])      NewCandidate(1, n, warm);
   else if(gH5[n] < gL5[n - 2]) NewCandidate(-1, n, warm);
  }

//==================================================================
// PER-SETUP STEPS AT AN M1 CLOSE (KTD2 steps 2-8, R6-R14, R22)
//==================================================================
// step 2: break episode (R6) and second break (R8)
bool StepBreak(Setup &s, int n)
  {
   bool beyond = (s.dir == 1) ? (gC1[n] < s.obLow) : (gC1[n] > s.obHigh);
   if(!beyond || s.broken) return false;            // consecutive closes beyond belong to one episode
   s.breaks++;
   if(s.returns > 0)
     {
      Ev(s.id, "cancelled_second_break", gT1[n], 0, gC1[n]);
      Finalize(s, "cancelled_second_break", BarCloseMsc1(n));
      return true;
     }
   s.broken = true;
   Ev(s.id, "break", gT1[n], 0, gC1[n]);
   return false;
  }

// step 3: R22 completion or void of the setup's opposing sequences, then new ones from pivots appended at this
// close. Long: a pivot high H2 that peaked after the touch bar, below the preceding high H1; completed by a close
// below the low L1 between them after H2 is confirmed, voided by a close above H2 first. Short mirrors.
bool StepOpposing(Setup &s, int n)
  {
   for(int c = ArraySize(gCand) - 1; c >= 0; c--)
     {
      if(gCand[c].setupId != s.id || n <= gCand[c].conf) continue;
      double mid = gPiv[gCand[c].mid].lvl;
      double x2  = gPiv[gCand[c].x2].lvl;
      bool done = (s.dir == 1) ? (gC1[n] < mid) : (gC1[n] > mid);
      if(done)
        {
         Ev(s.id, "cancelled_opposing_structure", gT1[n], 0, gC1[n], EMPTY_VALUE, EMPTY_VALUE,
            gPiv[gCand[c].x2].id, gT1[gPiv[gCand[c].mid].peak]);
         Finalize(s, "cancelled_opposing_structure", BarCloseMsc1(n));
         return true;
        }
      bool voided = (s.dir == 1) ? (gC1[n] > x2) : (gC1[n] < x2);
      if(voided) ArrayRemove(gCand, c, 1);
     }
   int want = (s.dir == 1) ? 1 : -1;
   for(int k = 0; k < ArraySize(gNewPiv); k++)
     {
      int x = gNewPiv[k];
      if(gPiv[x].type != want) continue;
      if(gT1[gPiv[x].peak] <= s.touchBarTime) continue;   // pre-touch pivots are comparison levels only (KTD4)
      int m = gPiv[x].prev1, x1 = gPiv[x].prev2;
      if(m < 0 || x1 < 0) continue;
      bool opp = (s.dir == 1) ? (gPiv[x].lvl < gPiv[x1].lvl) : (gPiv[x].lvl > gPiv[x1].lvl);
      if(!opp) continue;
      int sz = ArraySize(gCand);
      ArrayResize(gCand, sz + 1, 256);
      gCand[sz].setupId = s.id;
      gCand[sz].x2 = x;
      gCand[sz].x1 = x1;
      gCand[sz].mid = m;
      gCand[sz].conf = n;
     }
   return false;
  }

// step 4: R13 - a close beyond the entry FVG's far edge ends the FVG and its structure change
void StepLapse(Setup &s, int n)
  {
   if(s.state != ST_ARMED && s.state != ST_READY) return;
   bool beyond = (s.dir == 1) ? (gC1[n] < s.fvgLo) : (gC1[n] > s.fvgHi);
   if(!beyond) return;
   Ev(s.id, "fvg_lapsed", gT1[n], 0, gC1[n]);
   s.state = ST_TRACK;
   s.queued = false;
   s.mcWait = false;
  }

// step 5: R10 / KTD5 - a close beyond the latest pivot of the reference type; a newer reference pivot than the
// current one replaces a live structure change (sc_superseded); each reference pivot yields at most one event
void StepStructure(Setup &s, int n)
  {
   int ref = (s.dir == 1) ? gLastHigh : gLastLow;
   if(ref < 0 || gPiv[ref].id <= s.lastRefId) return;
   bool brk = (s.dir == 1) ? (gC1[n] > gPiv[ref].lvl) : (gC1[n] < gPiv[ref].lvl);
   if(!brk) return;
   if(s.state == ST_SC_PENDING_FVG || s.state == ST_ARMED || s.state == ST_READY)
      Ev(s.id, "sc_superseded", gT1[n], 0, EMPTY_VALUE, EMPTY_VALUE, EMPTY_VALUE, gPiv[ref].id);
   // origin: the lowest low (long) / highest high (short) from the reference peak to this bar; ties -> latest bar
   int o = gPiv[ref].peak;
   for(int j = gPiv[ref].peak; j <= n; j++)
     {
      if(s.dir == 1) { if(gL1[j] <= gL1[o]) o = j; }
      else           { if(gH1[j] >= gH1[o]) o = j; }
     }
   s.lastRefId = gPiv[ref].id;
   s.refPiv = ref;
   s.scBar = n;
   s.origin = o;
   s.fvgC1 = -1; s.fvgC3 = -1; s.fvgLo = 0; s.fvgHi = 0;
   s.hlDecided = false; s.hlPiv = -1; s.hlConf = -1;
   s.queued = false; s.mcWait = false;
   s.state = ST_SC_PENDING_FVG;
   gCntSc++;
   Ev(s.id, "sc_hh", gT1[n], 0, gPiv[ref].lvl, EMPTY_VALUE, EMPTY_VALUE, gPiv[ref].id, gT1[o]);
  }

// step 6: R12 - at the close of bar k+1, the trade-direction FVG with candle 1 at or after the origin and middle
// candle at or before the HH bar k; the latest candle 3 wins
void StepFvg(Setup &s, int n)
  {
   if(s.state != ST_SC_PENDING_FVG || n != s.scBar + 1) return;
   for(int c3 = n; c3 - 2 >= s.origin; c3--)
     {
      int c1 = c3 - 2;
      bool gap = (s.dir == 1) ? (gL1[c3] > gH1[c1]) : (gH1[c3] < gL1[c1]);
      if(!gap) continue;
      // R13: a candidate already closed beyond its far edge after its candle 3 (up to this fixing bar) has ended
      bool closedThrough = false;
      for(int j = c3 + 1; j <= n && !closedThrough; j++)
         closedThrough = (s.dir == 1) ? (gC1[j] < gH1[c1]) : (gC1[j] > gL1[c1]);
      if(closedThrough) continue;
      s.fvgC1 = c1;
      s.fvgC3 = c3;
      s.fvgLo = (s.dir == 1) ? gH1[c1] : gH1[c3];
      s.fvgHi = (s.dir == 1) ? gL1[c3] : gL1[c1];
      gCntFvgFixed++;
      Ev(s.id, "fvg_fixed", gT1[n], 0, EMPTY_VALUE, s.fvgLo, s.fvgHi, 0, gT1[c1]);
      if(StructureVariant == SV_HH_ONLY)
        {
         s.state = ST_READY;
         gCntReady++;
        }
      else s.state = ST_ARMED;
      return;
     }
   Ev(s.id, "fvg_none", gT1[n]);
   s.state = ST_TRACK;
  }

// step 7: R11 / KTD5 - the first pivot low (long) peaking after the HH bar is the HL if it is above the origin low.
// Variant B: HL -> READY, otherwise hl_failed and back to TRACK. Variant A: the HL only feeds the stop (R18).
void StepHl(Setup &s, int n)
  {
   if(s.hlDecided || (s.state != ST_ARMED && s.state != ST_READY)) return;
   int want = (s.dir == 1) ? -1 : 1;
   for(int k = 0; k < ArraySize(gNewPiv); k++)
     {
      int x = gNewPiv[k];
      if(gPiv[x].type != want || gPiv[x].peak <= s.scBar) continue;
      s.hlDecided = true;
      bool ok = (s.dir == 1) ? (gPiv[x].lvl > gL1[s.origin]) : (gPiv[x].lvl < gH1[s.origin]);
      if(ok)
        {
         s.hlPiv = x;
         s.hlConf = n;
         Ev(s.id, "hl", gT1[n], 0, gPiv[x].lvl, EMPTY_VALUE, EMPTY_VALUE, gPiv[x].id);
         if(s.state == ST_ARMED)
           {
            s.state = ST_READY;
            gCntReady++;
           }
        }
      else if(StructureVariant == SV_HH_HL)
        {
         Ev(s.id, "hl_failed", gT1[n], 0, gPiv[x].lvl, EMPTY_VALUE, EMPTY_VALUE, gPiv[x].id);
         s.state = ST_TRACK;
         s.queued = false;
         s.mcWait = false;
        }
      return;
     }
  }

// step 8: R14 - a candle that touches the entry FVG and closes beyond it in the trade direction, strictly after the
// HH bar, the FVG's candle 3 and (B) the HL confirmation bar; never while broken (R7). It queues a Market entry.
void StepReaction(Setup &s, int n)
  {
   if(s.state != ST_READY || s.broken) return;
   if(n <= s.scBar || n <= s.fvgC3) return;
   if(StructureVariant == SV_HH_HL && n <= s.hlConf) return;
   bool react = (s.dir == 1)
                ? (gL1[n] <= s.fvgHi && gC1[n] > gO1[n] && gC1[n] > s.fvgHi)
                : (gH1[n] >= s.fvgLo && gC1[n] < gO1[n] && gC1[n] < s.fvgLo);
   if(!react) return;
   gCntReactions++;
   Ev(s.id, "reaction", gT1[n]);
   s.reactBar = n;
   s.queued = true;
   s.competed = false;
   s.mcWait = false;
   gAnyQueued = true;
  }

// one closed M1 bar: the pivot sequence, then every touched waiting setup in the KTD2 order. A cancellation ends
// the setup before any later step, so it always beats an entry on the same bar.
void ProcessM1Close(int n)
  {
   UpdatePivots(n);
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(!Waiting(S[i])) continue;
      if(gT1[n] < S[i].touchBarTime) continue;      // only closes from the touch bar on count (KTD4)
      if(StepBreak(S[i], n)) continue;
      if(StepOpposing(S[i], n)) continue;
      StepLapse(S[i], n);
      StepStructure(S[i], n);
      StepFvg(S[i], n);
      StepHl(S[i], n);
      StepReaction(S[i], n);
     }
  }

void RecountBroken()
  {
   gBrokenCnt = 0;
   for(int i = 0; i < ArraySize(S); i++)
      if(Waiting(S[i]) && S[i].broken) gBrokenCnt++;
  }

//==================================================================
// WARM-UP (KTD8): closed M5 and M1 bars, trading off, bar-level touch
//==================================================================
void WarmupTouch(int n)
  {
   if(!((gHasLong && gL1[n] <= gCacheLong) || (gHasShort && gH1[n] >= gCacheShort))) return;
   for(int i = 0; i < ArraySize(gOb); i++)
     {
      if(gOb[i].state != ST_OB_WAIT || gOb[i].c3Close > gT1[n]) continue;
      bool touched = (gOb[i].dir == 1) ? (gL1[n] <= gOb[i].obHigh) : (gH1[n] >= gOb[i].obLow);
      if(!touched) continue;
      gOb[i].touchBarTime = gT1[n];
      gOb[i].ageBarsTouch = (gM5 - 1) - gOb[i].idC3;
      gOb[i].ageMinTouch = ((long)gT1[n] - (long)gOb[i].c3Close) / 60;
      Finalize(gOb[i], "warmup_dropped", (long)gT1[n] * 1000);
     }
   CompactOb();
   RecomputeCaches();
  }

void Warmup(int &w1, int &w5)
  {
   w1 = 0; w5 = 0;
   if(WarmupDays <= 0) return;
   long now = (long)TimeCurrent();
   datetime cur1 = (datetime)(now - now % 60);
   datetime cur5 = (datetime)(now - now % 300);
   datetime from = (datetime)((long)cur1 - (long)WarmupDays * 86400);
   MqlRates r1[], r5[];
   ArraySetAsSeries(r1, false);
   ArraySetAsSeries(r5, false);
   w1 = CopyRates(_Symbol, PERIOD_M1, from, cur1 - 1, r1);
   w5 = CopyRates(_Symbol, PERIOD_M5, from, cur5 - 1, r5);
   if(w1 < 0) w1 = 0;
   if(w5 < 0) w5 = 0;
   int j5 = 0;
   for(int i = 0; i < w1; i++)
     {
      int n = AppendM1(r1[i], true);
      ProcessM1Close(n);
      WarmupTouch(n);
      // M5 bars that closed at this M1 close are processed after it (KTD2 order)
      long close1 = (long)r1[i].time + 60;
      while(j5 < w5 && (long)r5[j5].time + 300 <= close1)
        {
         int m = AppendM5(r5[j5], true);
         M5Close(m, true);
         j5++;
        }
     }
   while(j5 < w5)
     {
      int m = AppendM5(r5[j5], true);
      M5Close(m, true);
      j5++;
     }
  }

//==================================================================
// PER-TICK STEPS (KTD2)
//==================================================================
bool IsOwn(string sym, long magic) { return sym == _Symbol && magic == MagicNumber; }

// R21: own open positions
int CountOpenPositions()
  {
   int cnt = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(IsOwn(PositionGetString(POSITION_SYMBOL), PositionGetInteger(POSITION_MAGIC))) cnt++;
     }
   return cnt;
  }

// exit detection: the position is gone; its closing deal gives time, price and SL/TP kind
void SyncPosition(Setup &s)
  {
   if(PositionSelectByTicket(s.posId)) return;      // still open
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
   Ev(s.id, "exit", MinuteOf(s.exitMsc), s.exitMsc, s.exitPrice, EMPTY_VALUE, EMPTY_VALUE, 0, 0, s.exitKind);
   CloseSetup(s);
  }

// tick step 1: fills and exits up to this tick
void SyncTrades(long nowMsc)
  {
   for(int i = 0; i < ArraySize(S); i++)
      if(S[i].state == ST_FILLED) SyncPosition(S[i]);
  }

// tick step 2: every M1 bar that closed since the last fetch (the forming bar is never read)
void ProcessNewM1Bars(const MqlTick &tk)
  {
   long t = (long)tk.time;
   datetime m1Open = (datetime)(t - t % 60);
   if(m1Open == gCurM1) return;
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = (gLast1 > 0) ? CopyRates(_Symbol, PERIOD_M1, gLast1 + 1, m1Open - 1, r)
                          : CopyRates(_Symbol, PERIOD_M1, 1, 1, r);
   if(got < 0) return;                               // retried on the next tick
   gCurM1 = m1Open;
   for(int i = 0; i < got; i++)
     {
      if(r[i].time <= gLast1 || r[i].time >= m1Open) continue;
      int n = AppendM1(r[i], false);
      ProcessM1Close(n);
     }
   RecountBroken();
  }

// tick step 3: every M5 bar that closed since the last fetch; it only creates candidates
void ProcessNewM5Bars(const MqlTick &tk)
  {
   long t = (long)tk.time;
   datetime m5Open = (datetime)(t - t % 300);
   if(m5Open == gCurM5) return;
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = (gLast5 > 0) ? CopyRates(_Symbol, PERIOD_M5, gLast5 + 1, m5Open - 1, r)
                          : CopyRates(_Symbol, PERIOD_M5, 1, 1, r);
   if(got < 0) return;
   gCurM5 = m5Open;
   for(int i = 0; i < got; i++)
     {
      if(r[i].time <= gLast5 || r[i].time >= m5Open) continue;
      int n = AppendM5(r[i], false);
      M5Close(n, false);
     }
  }

// R5: the first tick after candle 3 closed at which Bid reaches the OB's near edge
void ScanTouches(const MqlTick &tk)
  {
   bool any = false;
   for(int i = 0; i < ArraySize(gOb); i++)
     {
      if(gOb[i].state != ST_OB_WAIT) continue;
      bool touched = (gOb[i].dir == 1) ? (tk.bid <= gOb[i].obHigh) : (tk.bid >= gOb[i].obLow);
      if(!touched) continue;
      Setup s;
      s = gOb[i];
      s.state = ST_TRACK;
      s.touchMsc = tk.time_msc;
      s.touchBarTime = MinuteOf(tk.time_msc);
      s.ageBarsTouch = (gM5 - 1) - s.idC3;
      s.ageMinTouch = (tk.time_msc / 1000 - (long)s.c3Close) / 60;
      gCntTouched++;
      Ev(s.id, "touch", s.touchBarTime, s.touchMsc, tk.bid);
      int sz = ArraySize(S);
      ArrayResize(S, sz + 1, 256);
      S[sz] = s;
      gOb[i].state = ST_DONE;
      any = true;
     }
   if(!any) return;
   gDirtyOb = true;
   CompactOb();
   RecomputeCaches();
  }

// tick step 4: touches (R5) through the KTD9 edge caches, then returns (R7) of setups in a break episode
void TickChecks(const MqlTick &tk)
  {
   if((gHasLong && tk.bid <= gCacheLong) || (gHasShort && tk.bid >= gCacheShort)) ScanTouches(tk);
   if(gBrokenCnt <= 0) return;
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(!S[i].broken || !Waiting(S[i])) continue;
      bool inside = (S[i].dir == 1) ? (tk.bid >= S[i].obLow) : (tk.bid <= S[i].obHigh);
      if(!inside) continue;
      S[i].broken = false;
      S[i].returns++;
      gBrokenCnt--;
      Ev(S[i].id, "return", MinuteOf(tk.time_msc), tk.time_msc, tk.bid);
     }
  }

// R16 order: the later touch wins, then the later OB candle, then the lower setup id
bool Beats(const Setup &a, const Setup &b)
  {
   if(a.touchMsc != b.touchMsc) return a.touchMsc > b.touchMsc;
   if(a.obTime != b.obTime) return a.obTime > b.obTime;
   return a.id < b.id;
  }

// KTD7: a skip consumes this reaction candle only; the setup keeps waiting for a new one
void SkipEntry(Setup &s, string code, const MqlTick &tk, double price, double lo, string detail)
  {
   Ev(s.id, code, MinuteOf(tk.time_msc), tk.time_msc, price, lo, EMPTY_VALUE, 0, 0, detail);
   CountSkip(code);
   s.queued = false;
   s.mcWait = false;
   Print("OBM1 setup #", s.id, " ", DirStr(s.dir), " ", code, " request ", Fmt(price), " SL ", Fmt(s.sl),
         (detail != "" ? " (" + detail + ")" : ""));
  }

// R18: long - the lower of the OB low and the structure low, minus the buffer; short - the higher of the OB high
// and the structure high, plus the buffer, plus the spread at entry. Structure level: the HL (B, or A when one is
// confirmed), otherwise in variant A the latest pivot low in the sequence.
double StopFor(const Setup &s, const MqlTick &tk, string &anchor, double &anchorPx)
  {
   int d = s.dir;
   double buf = StopBufferPoints * _Point;
   int sp = -1;
   string kind = "";
   if(s.hlPiv >= 0) { sp = s.hlPiv; kind = "hl"; }
   else if(StructureVariant == SV_HH_ONLY) { sp = (d == 1) ? gLastLow : gLastHigh; kind = "pivot"; }
   anchor = "ob";
   anchorPx = (d == 1) ? s.obLow : s.obHigh;
   if(sp >= 0 && ((d == 1) ? (gPiv[sp].lvl < anchorPx) : (gPiv[sp].lvl > anchorPx)))
     {
      anchor = kind;
      anchorPx = gPiv[sp].lvl;
     }
   if(d == 1) return RoundTick(anchorPx - buf, false);
   return RoundTick(anchorPx + buf + (tk.ask - tk.bid), true);
  }

// R15, R18-R21, KTD7: Market order with SL on the first tick after the reaction close. Every failed check skips
// with its code (never a clamp); a TRADE_RETCODE_MARKET_CLOSED refusal is retried on every later tick.
void TryEntry(Setup &s, const MqlTick &tk)
  {
   int  d   = s.dir;
   long msc = tk.time_msc;
   if(s.state != ST_READY || s.broken) { s.queued = false; s.mcWait = false; return; }

   if(CountOpenPositions() >= MaxExposures) { SkipEntry(s, "skipped_cap", tk, EMPTY_VALUE, EMPTY_VALUE, IntegerToString(CountOpenPositions())); return; }

   double px = (d == 1) ? tk.ask : tk.bid;            // request price
   string anchor = "";
   double anchorPx = 0;
   double sl = StopFor(s, tk, anchor, anchorPx);
   s.sl = sl; s.slAnchor = anchor; s.slAnchorPrice = anchorPx; s.priced = true;
   s.reqMsc = msc; s.reqPrice = px;
   if((d == 1 && tk.bid <= sl) || (d == -1 && tk.ask >= sl)) { SkipEntry(s, "skipped_stop_crossed", tk, px, sl, ""); return; }

   // broker stops and freeze levels, read at every entry
   long stops  = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   long level  = (stops > freeze) ? stops : freeze;
   double tpReq = RoundTickNearest(px + d * RiskRR * MathAbs(px - sl));   // provisional, reset from the fill
   long slPts = (long)MathRound(((d == 1) ? (tk.bid - sl) : (sl - tk.ask)) / _Point);
   long tpPts = (long)MathRound(((d == 1) ? (tpReq - tk.bid) : (tk.ask - tpReq)) / _Point);
   if(slPts < level || tpPts < level || tpPts <= 0) { SkipEntry(s, "skipped_stops_level", tk, px, sl, IntegerToString(level)); return; }

   // R20: risk % of balance over the stop distance from the request price, rounded down to the lot step
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double step      = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minVol    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxVol    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(tickValue <= 0 || tickSize <= 0 || step <= 0) { SkipEntry(s, "skipped_volume", tk, px, sl, "no symbol volume data"); return; }
   double riskMoney  = AccountInfoDouble(ACCOUNT_BALANCE) * RiskPercent / 100.0;
   double lossPerLot = MathAbs(px - sl) / tickSize * tickValue;
   if(lossPerLot <= 0) { SkipEntry(s, "skipped_volume", tk, px, sl, "zero stop distance"); return; }
   double vol = NormalizeDouble(MathFloor(riskMoney / lossPerLot / step + 1e-9) * step, gVolDigits);
   s.volume = vol; s.sized = true;
   if(vol < minVol - 1e-9 || vol > maxVol + 1e-9) { SkipEntry(s, "skipped_volume", tk, px, sl, DoubleToString(vol, gVolDigits)); return; }
   double margin = 0;
   if(!OrderCalcMargin((d == 1) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, vol, px, margin) ||
      margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE)) { SkipEntry(s, "skipped_margin", tk, px, sl, ""); return; }

   string cmt = OrderComment(d, s.id);
   s.attempts++;
   bool ok = (d == 1) ? trade.Buy(vol, _Symbol, tk.ask, sl, tpReq, cmt)
                      : trade.Sell(vol, _Symbol, tk.bid, sl, tpReq, cmt);
   uint rc = trade.ResultRetcode();
   Ev(s.id, "entry_attempt", MinuteOf(msc), msc, px, EMPTY_VALUE, EMPTY_VALUE, 0, 0, IntegerToString(rc));
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_DONE_PARTIAL && rc != TRADE_RETCODE_PLACED))
     {
      if(rc == TRADE_RETCODE_MARKET_CLOSED)
        {
         s.mcWait = true;                            // stays queued; retried on every later tick while valid
         Print("OBM1 setup #", s.id, " market closed, attempt ", s.attempts, " - retry on the next tick");
         return;
        }
      SkipEntry(s, "skipped_broker_reject", tk, px, sl, IntegerToString(rc));
      return;
     }

   // fill: price, time and position from the deal; the target is reset from the fill (R19)
   ulong deal = trade.ResultDeal();
   double fill = trade.ResultPrice();
   long fillMsc = msc;
   ulong pos = trade.ResultOrder();
   if(deal > 0 && HistoryDealSelect(deal))
     {
      fill    = HistoryDealGetDouble(deal, DEAL_PRICE);
      fillMsc = HistoryDealGetInteger(deal, DEAL_TIME_MSC);
      pos     = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
     }
   double tp = RoundTickNearest(fill + d * RiskRR * MathAbs(fill - sl));
   if(MathAbs(tp - tpReq) > gTick / 2.0)
     {
      if(!trade.PositionModify(pos, sl, tp))
         Print("OBM1 setup #", s.id, " TP modify failed, retcode ", trade.ResultRetcode(), " - provisional TP kept");
     }
   double tpSet = tpReq;
   if(PositionSelectByTicket(pos)) tpSet = PositionGetDouble(POSITION_TP);
   s.tp = tpSet;
   s.fillMsc = fillMsc;
   s.fillPrice = fill;
   s.posId = pos;
   s.ageBarsEntry = (gM5 - 1) - s.idC3;
   s.ageMinEntry = (fillMsc / 1000 - (long)s.c3Close) / 60;
   s.state = ST_FILLED;
   s.queued = false;
   s.mcWait = false;
   SetReason(s, "filled", fillMsc);
   gCntFilled++;
   RemoveCands(s.id);                               // R22 never applies after a fill
   Ev(s.id, "fill", MinuteOf(fillMsc), fillMsc, fill, sl, tpSet);
   Print("OBM1 setup #", s.id, " ", DirStr(d), " filled ", DoubleToString(vol, gVolDigits), " @", Fmt(fill),
         " SL ", Fmt(sl), " (", anchor, ") TP ", Fmt(tpSet), " position ", pos, " ", cmt);
  }

// tick step 5: market-closed retries from earlier ticks, then R16 among fresh reactions per direction and reaction
// bar - one winner enters, the others log lost_competition and keep waiting; a skipped winner has no fallback
void RunEntries(const MqlTick &tk)
  {
   if(!gAnyQueued) return;
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(!S[i].queued || !S[i].competed || !S[i].mcWait) continue;
      gCntMcRetries++;
      TryEntry(S[i], tk);
     }
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(!S[i].queued || S[i].competed || S[i].state != ST_READY) continue;
      int w = i;
      for(int j = 0; j < ArraySize(S); j++)
        {
         if(j == w || !S[j].queued || S[j].competed || S[j].state != ST_READY) continue;
         if(S[j].dir != S[i].dir || S[j].reactBar != S[i].reactBar) continue;
         if(Beats(S[j], S[w])) w = j;
        }
      for(int j = 0; j < ArraySize(S); j++)
        {
         if(j == w || !S[j].queued || S[j].competed || S[j].state != ST_READY) continue;
         if(S[j].dir != S[w].dir || S[j].reactBar != S[w].reactBar) continue;
         Ev(S[j].id, "lost_competition", gT1[S[j].reactBar], 0, EMPTY_VALUE, EMPTY_VALUE, EMPTY_VALUE, S[w].id);
         CountSkip("lost_competition");
         S[j].queued = false;
        }
      S[w].competed = true;
      gCntEntries++;
      TryEntry(S[w], tk);
     }
   gAnyQueued = false;
   for(int i = 0; i < ArraySize(S); i++)
      if(S[i].queued) { gAnyQueued = true; break; }
  }

//==================================================================
// MT5 EVENTS
//==================================================================
// MT5 keeps globals across OnDeinit -> OnInit on an input or symbol change: start every init from a clean state
void ResetState()
  {
   ArrayResize(gO1, 0); ArrayResize(gH1, 0); ArrayResize(gL1, 0); ArrayResize(gC1, 0); ArrayResize(gT1, 0);
   ArrayResize(gO5, 0); ArrayResize(gH5, 0); ArrayResize(gL5, 0); ArrayResize(gC5, 0); ArrayResize(gT5, 0);
   ArrayResize(gPiv, 0); ArrayResize(gSeq, 0); ArrayResize(gNewPiv, 0); ArrayResize(gUsedOb, 0);
   ArrayResize(gOb, 0); ArrayResize(S, 0); ArrayResize(gCand, 0);
   gM1 = 0;
   gM5 = 0;
   gLast1 = 0;
   gLast5 = 0;
   gCurM1 = 0;
   gCurM5 = 0;
   gSeqN = 0;
   gLastHigh = -1; gLastLow = -1;
   gDirtyOb = false; gDirtyS = false;
   gNextId = 1;
   gEvSeq = 0;
   gHasLong = false; gHasShort = false; gCacheLong = 0.0; gCacheShort = 0.0;
   gBrokenCnt = 0;
   gAnyQueued = false;
   gCntIdentified = 0;
   gCntTouched = 0;
   gCntSc = 0; gCntFvgFixed = 0; gCntReady = 0; gCntReactions = 0; gCntEntries = 0; gCntFilled = 0;
   gCntCancel2nd = 0; gCntCancelOpp = 0; gCntWarmupDropped = 0; gCntRunEndWaiting = 0; gCntRunEndUntouched = 0;
   gCntMcRetries = 0;
   ArrayResize(gSkipCnt, ArraySize(gSkipCodes));
   ArrayInitialize(gSkipCnt, 0);
  }

int OnInit()
  {
   ResetState();
#ifdef RESEARCH_LOG
   RL_Init();
#endif
   if(_Period != PERIOD_M1)
     {
      Print("OBM1: this EA runs on the M1 chart only (it reads M5 itself). Current TF: ", EnumToString(_Period));
      return INIT_FAILED;
     }
   if(ImpulseWindowBars <= 0 || SwingStrengthM1 <= 0 || StopBufferPoints < 0 || RiskRR <= 0 || RiskPercent <= 0 ||
      MaxExposures <= 0 || WarmupDays < 0 || (StructureVariant != SV_HH_ONLY && StructureVariant != SV_HH_HL))
     {
      Print("OBM1: invalid inputs (windows and N > 0, buffer >= 0, RiskRR > 0, RiskPercent > 0, MaxExposures > 0)");
      return INIT_PARAMETERS_INCORRECT;
     }
   if((ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
     {
      Print("OBM1: a hedging account is required");
      return INIT_FAILED;
     }
   gTick   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   gDigits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   if(gTick <= 0) gTick = _Point;
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   gVolDigits = 0;
   while(step > 0 && gVolDigits < 8 && MathAbs(step - MathRound(step)) > 1e-9) { step *= 10.0; gVolDigits++; }
   gCmtPrefix = TradeComment;
   StringReplace(gCmtPrefix, ",", "");

   trade.SetExpertMagicNumber((ulong)MagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   // KTD8: warm-up replay of closed M1/M5 bars with trading off (no trading side effects here)
   int w1 = 0, w5 = 0;
   Warmup(w1, w5);
   Print("OBM1 initialised. Warm-up bars: ", w1, " M1, ", w5, " M5, variant ", EnumToString(StructureVariant),
         ", untouched OBs ", ArraySize(gOb), ", pivots ", ArraySize(gPiv),
         ", tick ", DoubleToString(gTick, gDigits),
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
   for(int i = 0; i < ArraySize(gOb); i++)
      if(gOb[i].state == ST_OB_WAIT) Finalize(gOb[i], "run_end_untouched", nowMsc);
   for(int i = 0; i < ArraySize(S); i++)
     {
      if(Waiting(S[i])) Finalize(S[i], "run_end_waiting", nowMsc);
      else if(S[i].state == ST_FILLED)
        {
         // position still open at run end: marked at the current price
         S[i].exitKind  = "end";
         S[i].exitMsc   = nowMsc;
         S[i].exitPrice = (S[i].dir == 1) ? bid : ask;
         Ev(S[i].id, "exit", MinuteOf(nowMsc), nowMsc, S[i].exitPrice, EMPTY_VALUE, EMPTY_VALUE, 0, 0, "end");
         CloseSetup(S[i]);
        }
     }
   CompactOb();
   CompactS();
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
   SyncTrades(tk.time_msc);    // 1. fills and exits
   ProcessNewM1Bars(tk);       // 2. newly closed M1 bars: pivots, R6-R8, R22, R13, R10, R12, R11, R14
   ProcessNewM5Bars(tk);       // 3. newly closed M5 bars: new candidates (R3)
   TickChecks(tk);             // 4. touch (R5) and return (R7)
   RunEntries(tk);             // 5. R16 competition and Market entries (R15)
   CompactOb();
   CompactS();
  }
//+------------------------------------------------------------------+
