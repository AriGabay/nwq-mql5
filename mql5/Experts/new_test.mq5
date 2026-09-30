//+------------------------------------------------------------------+
//|                                              SweepOB_XAUUSD.mq5  |
//|  Liquidity Sweep -> Displacement (volume x2) + FVG -> Structure  |
//|  break (close) -> first OB retest -> A/B/C confirmation ->       |
//|  SL at OB extreme, TP at nearest opposing pre-sweep liquidity.   |
//|  Chart period must equal SignalTF (default M5). Pine "SweepOB-5m" |
//|  All decisions on CLOSED bars; orders sent at next bar open.     |
//+------------------------------------------------------------------+
#property copyright ""
#property version   "1.04"
#property strict

#include <Trade/Trade.mqh>

//==================================================================
// INPUTS
//==================================================================
enum ENUM_ENTRY_MODE { MODE_A = 0, // A - close beyond original FVG
                       MODE_B = 1, // B - new FVG after touch + its retest
                       MODE_C = 2  // C - touch & confirm on same bar
                     };
enum ENUM_VOL_SRC    { VOL_TICK = 0, // Tick volume
                       VOL_REAL = 1  // Real volume (if broker provides)
                     };
enum ENUM_LOT_MODE   { LOT_FIXED = 0, // Fixed lots
                       LOT_RISK  = 1  // Risk % of balance per trade
                     };
enum ENUM_OPP_BREAK  { OPP_OFF = 0,          // Off - never cancel on opposite break
                       OPP_PRE_SETUP = 1,    // Only pivots confirmed before setup confirmation
                       OPP_ANY = 2           // Any opposite break (spec-strict)
                     };

input group "=== Timeframe ==="
input ENUM_TIMEFRAMES SignalTF = PERIOD_M5; // Signal timeframe (chart period must match)

input group "=== Pivots ==="
input int    PivL = 3;                    // Pivot Left (L)
input int    PivR = 3;                    // Pivot Right (R)

input group "=== Setup ==="
input int    SweepToSetupBars     = 12;   // sweepToSetupBars
input double VolumeMultiplier     = 2.0;  // volumeMultiplier
input int    MinVolumeSamples     = 20;   // minVolumeSamples
input int    MaxBaseBars          = 3;    // maxBaseBars
input int    ConfirmationBars     = 6;    // confirmationBars (touch bar + N-1)
input int    MaxWaitForRetestBars = 0;    // maxWaitForRetestBars (0 = unlimited)
input double TargetSafetyPercent  = 5.0;  // targetSafetyPercent
input ENUM_ENTRY_MODE EntryMode   = MODE_A; // Entry mode
input ENUM_VOL_SRC    VolumeSource = VOL_TICK; // Volume source
input ENUM_OPP_BREAK  OppBreakCancel = OPP_PRE_SETUP; // Opposite structure break cancels setup
input bool   OppBreakCancelBeforeTouch = false; // Apply it also while waiting for the first touch

input group "=== Trading ==="
input bool   EnableTrading    = true;     // Send orders (false = signals only)
input ENUM_LOT_MODE LotMode   = LOT_RISK; // Lot sizing
input double FixedLots        = 0.01;     // Fixed lots
input double RiskPercent      = 1.0;      // Risk % of balance (LOT_RISK)
input int    MaxOpenPositions = 3;        // Max simultaneous EA positions
input ulong  MagicNumber      = 560505;   // Magic number
input int    SlippagePoints   = 30;       // Max deviation (points)
input string TradeComment     = "SweepOB";// Order comment prefix

input group "=== Display / Debug ==="
input bool   DrawObjects = true;          // Draw setups on chart
input bool   DebugMode   = false;         // Verbose journal logging
input bool   PopupAlerts = false;         // Alert() popups on events
input int    WarmupBars  = 3000;          // Closed bars to pre-process on start (no trading)
#ifdef RESEARCH_LOG
input group "=== Research (research build only) ==="
input string ResearchRunTag = "";        // Tag for research CSV files ("" = no CSV)
#endif

//==================================================================
// CONSTANTS / TYPES
//==================================================================
#define HIST 64
#define NA   (-1e300)
#define S0_WAIT_FVG   0
#define S1_WAIT_BREAK 1
#define S2_WAIT_TOUCH 2
#define S3_CONFIRM    3
#define S4_IN_TRADE   4
#define S5_CANCELLED  5
#define S6_CLOSED     6

struct Pivot
  {
   double   lvl;
   int      bar;
   datetime t;
   int      conf;
   bool     taken;
   bool     broken;
  };

struct Setup
  {
   int      id;
   int      dir;        // 1 long, -1 short
   int      state;
   double   sweepLevel;
   int      sweepBar;
   int      sweepPivotBar;
   datetime sweepPivotTime;
   datetime sweepTime;
   int      obBar;
   datetime obTime;
   double   obHigh;
   double   obLow;
   int      baseBars;
   int      aBar;
   datetime aTime;
   int      bBar;
   int      cBar;
   double   fvgHi;
   double   fvgLo;
   double   bVol;
   double   bAvg;
   double   structLevel;
   int      structPivotBar;
   datetime structPivotTime;
   int      breakBar;
   datetime breakTime;
   string   breakLabel;
   int      confirmBar;
   int      exitBar;
   int      touchBar;
   bool     visitEnded;
   bool     nfActive;
   int      nfC;
   datetime nfATime;
   double   nfHi;
   double   nfLo;
   double   entry;
   double   sl;
   double   rawTarget;
   datetime targetTime;
   double   tp;
   double   rr;
   int      entryBar;
   datetime entryTime;
   string   mode;
   string   reason;
   string   result;
   ulong    ticket;
  };

//==================================================================
// GLOBAL STATE
//==================================================================
Pivot    ph[];            // swing highs (confirmation order)
Pivot    pl[];            // swing lows
Setup    setups[];
int      usedObBar[];
int      usedObDir[];
int      nextId = 1;

int      lastBreakDir = 0;
int      lastBullBreakBar = -1; double lastBullBreakLvl = 0; int lastBullBreakPiv = -1; int lastBullBreakPivConf = -1; datetime lastBullBreakPivTime = 0; string lastBullBreakLbl = "";
int      lastBearBreakBar = -1; double lastBearBreakLvl = 0; int lastBearBreakPiv = -1; int lastBearBreakPivConf = -1; datetime lastBearBreakPivTime = 0; string lastBearBreakLbl = "";

// rolling window of processed bars (index ArraySize-1 = current bar)
double   oH[], hH[], lH[], cH[];
datetime tH[];
// 24h real-time volume window (bars strictly BEFORE the current bar)
double   vWin[];
datetime tWin[];
double   avgVolLast = NA;   // 24h average valid for the PREVIOUS bar (B)
double   volLast    = NA;   // volume of the previous bar (B)

int      barIdx = -1;       // sequential index of processed bars
datetime lastProcessed = 0;
CTrade   trade;
double   gTick = 0.0;
int      gDigits = 0;

// per-bar values (set in ProcessBar, read by the state machine)
int      n;
double   bo, bh, bl, bc, bv;
datetime bt;
double   prevAvg, v1, c1, o1, h2, l2;
bool     gCanTrade = false;

// funnel statistics
int      cntBars = 0, cntPivH = 0, cntPivL = 0, cntBullBreak = 0, cntBearBreak = 0;
int      cntSweepL = 0, cntSweepS = 0, cntFvgSeen = 0, cntVolReject = 0, cntNoOB = 0;
int      cntConfirmed = 0, cntTouch = 0, cntEntrySignal = 0, cntOrders = 0, cntOrderSkip = 0;
string   cancelReasons[];
int      cancelCounts[];

//==================================================================
// SMALL HELPERS
//==================================================================
bool   Valid(double x) { return x != NA; }

double Hist(const double &a[], int off)
  {
   int sz = ArraySize(a);
   if(off < 0 || off >= sz) return NA;
   return a[sz - 1 - off];
  }
datetime HistT(int off)
  {
   int sz = ArraySize(tH);
   if(off < 0 || off >= sz) return 0;
   return tH[sz - 1 - off];
  }
void PushD(double &a[], double v)
  {
   int sz = ArraySize(a);
   ArrayResize(a, sz + 1);
   a[sz] = v;
  }
void PushT(datetime &a[], datetime v)
  {
   int sz = ArraySize(a);
   ArrayResize(a, sz + 1);
   a[sz] = v;
  }
void PushI(int &a[], int v)
  {
   int sz = ArraySize(a);
   ArrayResize(a, sz + 1);
   a[sz] = v;
  }
void PushP(Pivot &a[], Pivot &p)
  {
   int sz = ArraySize(a);
   ArrayResize(a, sz + 1);
   a[sz] = p;
  }
string Fmt(double p) { return DoubleToString(p, gDigits); }
string TfName() { return StringSubstr(EnumToString(SignalTF), 7); }
string DirStr(int d) { return d == 1 ? "LONG" : "SHORT"; }
double RoundTick(double p, bool up)
  {
   if(gTick <= 0) return NormalizeDouble(p, gDigits);
   double q = p / gTick;
   double r = up ? MathCeil(q - 1e-9) : MathFloor(q + 1e-9);
   return NormalizeDouble(r * gTick, gDigits);
  }
void Say(string msg)
  {
   Print(msg);
   if(PopupAlerts && gCanTrade) Alert(msg);
  }
void Dbg(string msg) { if(DebugMode) Print("[DBG] ", msg); }
void CountCancel(string why)
  {
   for(int i = 0; i < ArraySize(cancelReasons); i++)
      if(cancelReasons[i] == why) { cancelCounts[i]++; return; }
   int sz = ArraySize(cancelReasons);
   ArrayResize(cancelReasons, sz + 1); ArrayResize(cancelCounts, sz + 1);
   cancelReasons[sz] = why; cancelCounts[sz] = 1;
  }
void PrintFunnel()
  {
   Print("===== SweepOB FUNNEL =====");
   Print("Bars processed: ", cntBars, " | pivots H/L: ", cntPivH, "/", cntPivL, " | breaks bull/bear: ", cntBullBreak, "/", cntBearBreak);
   Print("Sweeps long/short: ", cntSweepL, "/", cntSweepS);
   Print("FVG candidates seen (bar B in window): ", cntFvgSeen, " | rejected by volume: ", cntVolReject, " | FVG ok but no OB source: ", cntNoOB);
   Print("Setups confirmed: ", cntConfirmed, " | first touches: ", cntTouch, " | entry signals: ", cntEntrySignal, " | orders sent: ", cntOrders, " | orders skipped/failed: ", cntOrderSkip);
   for(int i = 0; i < ArraySize(cancelReasons); i++)
      Print("Cancel [", cancelCounts[i], "] ", cancelReasons[i]);
   Print("==========================");
  }

// opposite structure break relevant for setup s on the current bar?
bool OppBreakNow(const Setup &s)
  {
   if(OppBreakCancel == OPP_OFF) return false;
   int  brkBar  = (s.dir == 1) ? lastBearBreakBar     : lastBullBreakBar;
   int  pivConf = (s.dir == 1) ? lastBearBreakPivConf : lastBullBreakPivConf;
   if(brkBar != n) return false;
   if(OppBreakCancel == OPP_ANY) return true;
   return pivConf < s.confirmBar;   // OPP_PRE_SETUP: the broken pivot existed before the setup was confirmed
  }

//==================================================================
// DRAWING
//==================================================================
string ObjName(const Setup &s, string suffix) { return "SOB_" + IntegerToString(s.id) + "_" + suffix; }

void DrawRect(string name, datetime t1, double p1, datetime t2, double p2, color clr, bool fill = true)
  {
   if(!DrawObjects) return;
   if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FILL, fill);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }
void DrawLine(string name, datetime t1, double p1, datetime t2, double p2, color clr, int style, bool ray)
  {
   if(!DrawObjects) return;
   if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_TREND, 0, t1, p1, t2, p2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, ray);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }
void DrawText(string name, datetime t, double p, string txt, color clr, int anchor = ANCHOR_LEFT)
  {
   if(!DrawObjects) return;
   if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_TEXT, 0, t, p);
   ObjectSetString(0, name, OBJPROP_TEXT, txt);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }
void DrawArrow(string name, datetime t, double p, int code, color clr)
  {
   if(!DrawObjects) return;
   if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_ARROW, 0, t, p);
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, code);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }
void SetRight(string name, datetime t)
  {
   if(!DrawObjects) return;
   if(ObjectFind(0, name) >= 0) ObjectSetInteger(0, name, OBJPROP_TIME, 1, t);
  }
void SetColor(string name, color clr)
  {
   if(!DrawObjects) return;
   if(ObjectFind(0, name) >= 0) ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
  }
void ExtendActive(Setup &s)
  {
   if(s.state < S2_WAIT_TOUCH || s.state > S4_IN_TRADE) return;
   if(s.state < S4_IN_TRADE)
     {
      SetRight(ObjName(s, "OB"), bt);
      SetRight(ObjName(s, "FVG"), bt);
      SetRight(ObjName(s, "NF"), bt);
     }
   else
     {
      SetRight(ObjName(s, "RISK"), bt);
      SetRight(ObjName(s, "REW"), bt);
     }
  }
void DimSetup(Setup &s)
  {
   SetColor(ObjName(s, "OB"), clrDimGray);
   SetColor(ObjName(s, "FVG"), clrDimGray);
   SetColor(ObjName(s, "NF"), clrDimGray);
  }

//==================================================================
// PIVOTS
//==================================================================
bool IsPivotHigh()
  {
   if(ArraySize(hH) <= PivL + PivR) return false;
   double pv = Hist(hH, PivR);
   for(int i = 0; i <= PivL + PivR; i++)
     {
      if(i == PivR) continue;
      if(!(pv > Hist(hH, i))) return false;   // strict: ties are not pivots
     }
   return true;
  }
bool IsPivotLow()
  {
   if(ArraySize(lH) <= PivL + PivR) return false;
   double pv = Hist(lH, PivR);
   for(int i = 0; i <= PivL + PivR; i++)
     {
      if(i == PivR) continue;
      if(!(pv < Hist(lH, i))) return false;
     }
   return true;
  }
int LatestFree(const Pivot &a[], bool useBroken)
  {
   for(int i = ArraySize(a) - 1; i >= 0; i--)
     {
      bool flag = useBroken ? a[i].broken : a[i].taken;
      if(!flag && a[i].conf < n) return i;
     }
   return -1;
  }
bool ObUsed(int b, int d)
  {
   for(int i = 0; i < ArraySize(usedObBar); i++)
      if(usedObBar[i] == b && usedObDir[i] == d) return true;
   return false;
  }

//==================================================================
// OB SOURCE SEARCH (offset relative to current bar C; B is offset 1)
//==================================================================
int FindOB(int d)
  {
   for(int k = 1; k <= MaxBaseBars + 1; k++)
     {
      int o = 1 + k;
      double co = Hist(cH, o), oo = Hist(oH, o), ho = Hist(hH, o), lo = Hist(lH, o);
      if(!Valid(co) || !Valid(oo)) break;
      bool srcOK = (d == 1) ? (co < oo) : (co > oo);
      if(!srcOK) continue;
      bool contained = true;
      for(int j = 2; j <= o - 1; j++)
        {
         if(Hist(hH, j) > ho || Hist(lH, j) < lo) { contained = false; break; }
        }
      if(contained) return o;
     }
   return -1;
  }

//==================================================================
// TRADING HELPERS
//==================================================================
int CountEAPositions()
  {
   int cnt = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      string sym = PositionGetSymbol(i);
      if(sym != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      cnt++;
     }
   return cnt;
  }
double NormalizeLot(double lot)
  {
   double minL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;
   lot = MathFloor(lot / step + 1e-9) * step;
   if(lot < minL) lot = minL;
   if(lot > maxL) lot = maxL;
   return NormalizeDouble(lot, 2);
  }
double CalcLot(double slDist)
  {
   if(LotMode == LOT_FIXED) return NormalizeLot(FixedLots);
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv <= 0 || ts <= 0 || slDist <= 0) return NormalizeLot(FixedLots);
   double riskMoney  = AccountInfoDouble(ACCOUNT_BALANCE) * RiskPercent / 100.0;
   double lossPerLot = slDist / ts * tv;
   if(lossPerLot <= 0) return NormalizeLot(FixedLots);
   return NormalizeLot(riskMoney / lossPerLot);
  }
bool SendEntry(Setup &s)
  {
   if(!gCanTrade || !EnableTrading)
     {
      Dbg("Signal only (trading disabled or warm-up): #" + IntegerToString(s.id));
      return false;
     }
   if(CountEAPositions() >= MaxOpenPositions)
     {
      Say("#" + IntegerToString(s.id) + " entry skipped: MaxOpenPositions reached");
      cntOrderSkip++;
      return false;
     }
   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk)) { cntOrderSkip++; return false; }
   double price = (s.dir == 1) ? tk.ask : tk.bid;
   double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(MathAbs(price - s.sl) < minDist || MathAbs(s.tp - price) < minDist)
     {
      Say("#" + IntegerToString(s.id) + " entry skipped: SL/TP inside broker stops level");
      cntOrderSkip++;
      return false;
     }
   bool orderOK = (s.dir == 1) ? (s.sl < price && price < s.tp) : (s.tp < price && price < s.sl);
   if(!orderOK)
     {
      Say("#" + IntegerToString(s.id) + " entry skipped: price moved beyond SL/TP before fill");
      cntOrderSkip++;
      return false;
     }
   double lot = CalcLot(MathAbs(price - s.sl));
   string cmt = TradeComment + "#" + IntegerToString(s.id) + s.mode;
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   bool ok = (s.dir == 1) ? trade.Buy(lot, _Symbol, price, s.sl, s.tp, cmt)
                          : trade.Sell(lot, _Symbol, price, s.sl, s.tp, cmt);
   if(ok && trade.ResultRetcode() == TRADE_RETCODE_DONE)
     {
      s.ticket = trade.ResultOrder();
      cntOrders++;
      Say("ORDER SENT #" + IntegerToString(s.id) + " " + DirStr(s.dir) + " lot " + DoubleToString(lot, 2) +
          " @" + Fmt(trade.ResultPrice()) + " SL " + Fmt(s.sl) + " TP " + Fmt(s.tp));
      return true;
     }
   cntOrderSkip++;
   Say("ORDER FAILED #" + IntegerToString(s.id) + " retcode " + IntegerToString(trade.ResultRetcode()) + " " + trade.ResultComment());
   return false;
  }

//==================================================================
// SETUP LIFECYCLE
//==================================================================
void InitSetup(Setup &s)
  {
   s.id = 0; s.dir = 0; s.state = S0_WAIT_FVG;
   s.sweepLevel = 0; s.sweepBar = -1; s.sweepPivotBar = -1; s.sweepPivotTime = 0; s.sweepTime = 0;
   s.obBar = -1; s.obTime = 0; s.obHigh = 0; s.obLow = 0; s.baseBars = 0;
   s.aBar = -1; s.aTime = 0; s.bBar = -1; s.cBar = -1; s.fvgHi = 0; s.fvgLo = 0; s.bVol = 0; s.bAvg = 0;
   s.structLevel = 0; s.structPivotBar = -1; s.structPivotTime = 0; s.breakBar = -1; s.breakTime = 0; s.breakLabel = "";
   s.confirmBar = -1; s.exitBar = -1; s.touchBar = -1; s.visitEnded = false; s.nfActive = false; s.nfC = -1; s.nfATime = 0; s.nfHi = 0; s.nfLo = 0;
   s.entry = 0; s.sl = 0; s.rawTarget = 0; s.targetTime = 0; s.tp = 0; s.rr = 0; s.entryBar = -1; s.entryTime = 0;
   s.mode = ""; s.reason = ""; s.result = ""; s.ticket = 0;
  }

void CancelSetup(Setup &s, string why)
  {
   s.state = S5_CANCELLED;
   s.reason = why;
   CountCancel(why);
   DimSetup(s);
   DrawText(ObjName(s, "END"), bt, (s.dir == 1 ? bl : bh), "x #" + IntegerToString(s.id) + " " + why, clrGray);
   Say("#" + IntegerToString(s.id) + " " + DirStr(s.dir) + " cancelled: " + why);
  }

void FinishTrade(Setup &s, string result)
  {
   s.state = S6_CLOSED;
   s.result = result;
   DrawText(ObjName(s, "END"), bt, (s.dir == 1 ? bh : bl), "#" + IntegerToString(s.id) + " " + result, clrGray);
   Say("#" + IntegerToString(s.id) + " " + result);
  }

void ConfirmSetup(Setup &s)
  {
   int d = s.dir;
   s.confirmBar = n;
   s.state = S2_WAIT_TOUCH;
   cntConfirmed++;
   if(d == 1)
     {
      s.structLevel = lastBullBreakLvl; s.structPivotBar = lastBullBreakPiv; s.structPivotTime = lastBullBreakPivTime;
      s.breakBar = lastBullBreakBar; s.breakLabel = lastBullBreakLbl;
     }
   else
     {
      s.structLevel = lastBearBreakLvl; s.structPivotBar = lastBearBreakPiv; s.structPivotTime = lastBearBreakPivTime;
      s.breakBar = lastBearBreakBar; s.breakLabel = lastBearBreakLbl;
     }
   s.breakTime = HistT(n - s.breakBar);
   PushI(usedObBar, s.obBar);
   PushI(usedObDir, d);

   DrawRect(ObjName(s, "OB"),  s.obTime, s.obHigh, bt, s.obLow, (d == 1 ? clrTeal : clrFireBrick));
   DrawRect(ObjName(s, "FVG"), s.aTime,  s.fvgHi,  bt, s.fvgLo, clrDarkGoldenrod);
   DrawLine(ObjName(s, "STR"), s.structPivotTime, s.structLevel, s.breakTime, s.structLevel, (d == 1 ? clrLime : clrRed), STYLE_DASH, false);
   DrawText(ObjName(s, "STRL"), s.breakTime, s.structLevel, s.breakLabel, (d == 1 ? clrLime : clrRed));
   DrawText(ObjName(s, "CONF"), bt, (d == 1 ? bl : bh), "Setup #" + IntegerToString(s.id) + " " + DirStr(d), (d == 1 ? clrLime : clrRed));

   string txt = "SETUP CONFIRMED #" + IntegerToString(s.id) + " " + DirStr(d) + " OB " + Fmt(s.obLow) + "-" + Fmt(s.obHigh);
   if(DebugMode)
      txt += " | sweep " + Fmt(s.sweepLevel) + " (piv bar " + IntegerToString(s.sweepPivotBar) + ", sweep bar " + IntegerToString(s.sweepBar) + ")"
           + " | break " + s.breakLabel + " " + Fmt(s.structLevel) + " (piv bar " + IntegerToString(s.structPivotBar) + ", break bar " + IntegerToString(s.breakBar) + ")"
           + " | OB bar " + IntegerToString(s.obBar) + " base " + IntegerToString(s.baseBars)
           + " | FVG A/B/C " + IntegerToString(s.aBar) + "/" + IntegerToString(s.bBar) + "/" + IntegerToString(s.cBar) + " [" + Fmt(s.fvgLo) + "-" + Fmt(s.fvgHi) + "]"
           + " | vol B x" + DoubleToString(s.bVol / s.bAvg, 2) + " | exit bar " + IntegerToString(s.exitBar);
   Say(txt);
  }

void DoEntry(Setup &s, string mode)
  {
   int d = s.dir;
   double e = bc;
   double stop = (d == 1) ? s.obLow : s.obHigh;
   double raw = NA; datetime tT = 0;
   if(d == 1)
     {
      for(int i = 0; i < ArraySize(ph); i++)
         if(!ph[i].taken && ph[i].conf < s.sweepBar && ph[i].lvl > e && (!Valid(raw) || ph[i].lvl < raw))
           { raw = ph[i].lvl; tT = ph[i].t; }
     }
   else
     {
      for(int i = 0; i < ArraySize(pl); i++)
         if(!pl[i].taken && pl[i].conf < s.sweepBar && pl[i].lvl < e && (!Valid(raw) || pl[i].lvl > raw))
           { raw = pl[i].lvl; tT = pl[i].t; }
     }
   if(!Valid(raw)) { CancelSetup(s, "No valid target liquidity (untaken pre-sweep pivot beyond entry)"); return; }

   double tpRaw = e + (raw - e) * (1.0 - TargetSafetyPercent / 100.0);
   double tp = RoundTick(tpRaw, d != 1);
   bool orderOK = (d == 1) ? (stop < e && e < tp) : (tp < e && e < stop);
   if(!orderOK) { CancelSetup(s, "Invalid price order SL/Entry/TP after rounding"); return; }

   s.state = S4_IN_TRADE;
   s.mode = mode; s.entry = e; s.sl = stop; s.rawTarget = raw; s.targetTime = tT; s.tp = tp;
   s.rr = MathAbs(tp - e) / MathAbs(e - stop);
   s.entryBar = n; s.entryTime = bt;
   cntEntrySignal++;

   DrawLine(ObjName(s, "TGT"), tT, raw, bt, raw, clrLime, STYLE_DASH, true);
   DrawText(ObjName(s, "TGTL"), bt, raw, "Target liquidity " + Fmt(raw), clrLime);
   DrawRect(ObjName(s, "RISK"), bt, e, bt, stop, clrMaroon);
   DrawRect(ObjName(s, "REW"),  bt, e, bt, tp,   clrDarkGreen);
   DrawArrow(ObjName(s, "ARR"), bt, (d == 1 ? bl : bh), (d == 1 ? 233 : 234), (d == 1 ? clrLime : clrRed));
   DrawText(ObjName(s, "ENT"), bt, (d == 1 ? bl : bh),
            DirStr(d) + " " + mode + " #" + IntegerToString(s.id) + " E " + Fmt(e) + " SL " + Fmt(stop) + " TP " + Fmt(tp) + " RR " + DoubleToString(s.rr, 2),
            (d == 1 ? clrLime : clrRed), (d == 1 ? ANCHOR_LEFT_UPPER : ANCHOR_LEFT_LOWER));

   Say(_Symbol + " " + TfName() + " | " + DirStr(d) + " ENTRY | setup #" + IntegerToString(s.id) + " | mode " + mode +
       " | Entry " + Fmt(e) + " | SL " + Fmt(stop) + " | TP " + Fmt(tp) + " (raw " + Fmt(raw) + ") | RR " + DoubleToString(s.rr, 2));
   SendEntry(s);
  }

//==================================================================
// STATE MACHINE (one setup, one closed bar)
//==================================================================
void ProcessSetup(Setup &s)
  {
   int d = s.dir;
   bool inDir = (d == 1) ? (bc > bo) : (bc < bo);

   //---------------- state 0: waiting for displacement FVG (+volume) and OB
   if(s.state == S0_WAIT_FVG)
     {
      if(n - s.sweepBar > SweepToSetupBars) { CancelSetup(s, "Setup window expired before valid FVG/OB"); return; }
      bool fvgNow = (d == 1) ? (bl > h2) : (bh < l2);
      bool bOK    = (d == 1) ? (c1 > o1) : (c1 < o1);
      int  bIdx   = n - 1;
      bool volOK  = Valid(prevAvg) && prevAvg > 0 && Valid(v1) && v1 >= VolumeMultiplier * prevAvg;
      if(fvgNow && bOK && bIdx >= s.sweepBar)
        {
         cntFvgSeen++;
         if(!volOK)
           {
            cntVolReject++;
            Dbg("#" + IntegerToString(s.id) + " FVG rejected: vol x" + (Valid(prevAvg) && prevAvg > 0 ? DoubleToString(v1 / prevAvg, 2) : "n/a"));
            return;
           }
         int obOff = FindOB(d);
         if(obOff < 0) { cntNoOB++; Dbg("#" + IntegerToString(s.id) + " FVG ok, no valid OB source"); return; }
         if(ObUsed(n - obOff, d)) { CancelSetup(s, "OB source already used by another setup"); return; }

         s.state = S1_WAIT_BREAK;
         s.aBar = n - 2; s.aTime = HistT(2); s.bBar = n - 1; s.cBar = n;
         s.fvgHi = (d == 1) ? bl : l2;
         s.fvgLo = (d == 1) ? h2 : bh;
         s.bVol = v1; s.bAvg = prevAvg;
         s.obBar = n - obOff; s.obTime = HistT(obOff);
         s.obHigh = Hist(hH, obOff); s.obLow = Hist(lH, obOff);
         s.baseBars = obOff - 2;
         for(int j = obOff - 1; j >= 0; j--)
           {
            double cj = Hist(cH, j);
            bool beyond = (d == 1) ? (cj > s.obHigh) : (cj < s.obLow);
            if(beyond) { s.exitBar = n - j; break; }
           }
         if(s.exitBar >= 0)
           {
            int exitOff = n - s.exitBar;
            for(int j = exitOff - 1; j >= 0; j--)
              {
               double hj = Hist(hH, j), lj = Hist(lH, j);
               bool stopJ = (d == 1) ? (lj <= s.obLow) : (hj >= s.obHigh);
               bool ovlJ  = (lj <= s.obHigh && hj >= s.obLow);
               if(stopJ) { CancelSetup(s, "Stop level reached before full confirmation"); return; }
               if(ovlJ)  { CancelSetup(s, "OB retested before full confirmation (not fresh)"); return; }
              }
           }
         int brkBar = (d == 1) ? lastBullBreakBar : lastBearBreakBar;
         if(brkBar >= s.bBar) ConfirmSetup(s);
        }
      return;
     }

   //---------------- state 1: FVG+OB found, waiting for structure break
   if(s.state == S1_WAIT_BREAK)
     {
      if(n - s.sweepBar > SweepToSetupBars) { CancelSetup(s, "Setup window expired before structure break"); return; }
      if(s.exitBar < 0)
        {
         bool beyond = (d == 1) ? (bc > s.obHigh) : (bc < s.obLow);
         if(beyond) s.exitBar = n;
        }
      else if(n > s.exitBar)
        {
         bool stopHit = (d == 1) ? (bl <= s.obLow) : (bh >= s.obHigh);
         bool ovl     = (bl <= s.obHigh && bh >= s.obLow);
         if(stopHit) { CancelSetup(s, "Stop level reached before full confirmation"); return; }
         if(ovl)     { CancelSetup(s, "OB retested before full confirmation (not fresh)"); return; }
        }
      int brkBar = (d == 1) ? lastBullBreakBar : lastBearBreakBar;
      if(brkBar >= s.bBar) ConfirmSetup(s);
      return;
     }

   //---------------- state 2: confirmed, waiting for first touch
   if(s.state == S2_WAIT_TOUCH)
     {
      bool stopHit  = (d == 1) ? (bl <= s.obLow) : (bh >= s.obHigh);
      bool ovl      = (bl <= s.obHigh && bh >= s.obLow);
      if(OppBreakCancelBeforeTouch && OppBreakNow(s)) { CancelSetup(s, "Opposite structure break before first touch"); return; }
      if(stopHit)  { CancelSetup(s, "Stop level reached before entry"); return; }
      if(MaxWaitForRetestBars > 0 && n - s.confirmBar > MaxWaitForRetestBars) { CancelSetup(s, "Retest wait window expired"); return; }
      if(ovl && n > s.confirmBar)
        {
         s.touchBar = n;
         s.state = S3_CONFIRM;
         cntTouch++;
         DrawText(ObjName(s, "TOUCH"), bt, (d == 1 ? bl : bh), "touch #" + IntegerToString(s.id), clrOrange);
         Say(_Symbol + " " + TfName() + " | " + DirStr(d) + " setup #" + IntegerToString(s.id) + " first OB touch");
        }
      else return;
     }

   //---------------- state 3: first visit / confirmation window
   if(s.state == S3_CONFIRM)
     {
      bool stopHit   = (d == 1) ? (bl <= s.obLow) : (bh >= s.obHigh);
      bool ovl       = (bl <= s.obHigh && bh >= s.obLow);
      int  windowEnd = s.touchBar + ConfirmationBars - 1;
      double fvgNear = (d == 1) ? s.fvgHi : s.fvgLo;
      bool beyondFVG = (d == 1) ? (bc > fvgNear) : (bc < fvgNear);
      if(OppBreakNow(s)) { CancelSetup(s, "Opposite structure break during confirmation window"); return; }
      if(stopHit)  { CancelSetup(s, (n == s.touchBar) ? "Stop touched on touch bar - no entry" : "Stop touched before entry"); return; }
      if(n > windowEnd) { CancelSetup(s, "Confirmation window expired"); return; }
      if(s.visitEnded && ovl) { CancelSetup(s, "Second visit to OB"); return; }

      bool entryOK = false;
      if(EntryMode == MODE_A)
        {
         entryOK = inDir && beyondFVG;
        }
      else if(EntryMode == MODE_C)
        {
         bool cOK = (n == s.touchBar) && inDir && beyondFVG;
         if(cOK) entryOK = true;
         else { CancelSetup(s, "Mode C: touch bar did not confirm"); return; }
        }
      else // MODE_B
        {
         if(s.nfActive)
           {
            bool farBreak = (d == 1) ? (bc < s.nfLo) : (bc > s.nfHi);
            if(farBreak) { CancelSetup(s, "Mode B: close beyond far side of new FVG"); return; }
            bool ovlNF    = (bl <= s.nfHi && bh >= s.nfLo);
            double nfNear = (d == 1) ? s.nfHi : s.nfLo;
            bool beyondNF = (d == 1) ? (bc > nfNear) : (bc < nfNear);
            if(n > s.nfC && ovlNF && inDir && beyondNF) entryOK = true;
           }
         else
           {
            bool nfNow = (d == 1) ? (bl > h2) : (bh < l2);
            bool bOK   = (d == 1) ? (c1 > o1) : (c1 < o1);
            if(nfNow && bOK && n - 1 >= s.touchBar)
              {
               s.nfActive = true; s.nfC = n; s.nfATime = HistT(2);
               s.nfHi = (d == 1) ? bl : l2;
               s.nfLo = (d == 1) ? h2 : bh;
               DrawRect(ObjName(s, "NF"), s.nfATime, s.nfHi, bt, s.nfLo, clrMediumOrchid);
               DrawText(ObjName(s, "NFL"), bt, s.nfHi, "FVG Confirm", clrMediumOrchid);
              }
           }
        }
      if(!s.visitEnded)
        {
         bool exitClose = (d == 1) ? (bc > s.obHigh) : (bc < s.obLow);
         if(exitClose) s.visitEnded = true;
        }
      if(entryOK) DoEntry(s, (EntryMode == MODE_A ? "A" : EntryMode == MODE_B ? "B" : "C"));
      return;
     }

   //---------------- state 4: in trade (frozen) - outcome tracking by bar data
   if(s.state == S4_IN_TRADE && n > s.entryBar)
     {
      bool hitTP = (d == 1) ? (bh >= s.tp) : (bl <= s.tp);
      bool hitSL = (d == 1) ? (bl <= s.sl) : (bh >= s.sl);
      if(hitTP && hitSL)      FinishTrade(s, "SL & TP same bar - order unknown (broker fill decides)");
      else if(hitTP)          FinishTrade(s, "TP reached");
      else if(hitSL)          FinishTrade(s, "SL reached");
     }
  }

//==================================================================
// PER-BAR PROCESSING (one CLOSED bar)
//==================================================================
void ProcessBar(const MqlRates &r, bool canTrade)
  {
   gCanTrade = canTrade;
   barIdx++;
   n  = barIdx;
   cntBars++;
   bo = r.open; bh = r.high; bl = r.low; bc = r.close; bt = r.time;
   bv = (VolumeSource == VOL_REAL) ? (double)r.real_volume : (double)r.tick_volume;
   if(bv <= 0) bv = NA;

   PushD(oH, bo); PushD(hH, bh); PushD(lH, bl); PushD(cH, bc); PushT(tH, bt);
   if(ArraySize(cH) > HIST)
     {
      ArrayRemove(oH, 0, 1); ArrayRemove(hH, 0, 1); ArrayRemove(lH, 0, 1); ArrayRemove(cH, 0, 1); ArrayRemove(tH, 0, 1);
     }
   c1 = Hist(cH, 1); o1 = Hist(oH, 1); h2 = Hist(hH, 2); l2 = Hist(lH, 2);
   prevAvg = avgVolLast;
   v1      = volLast;

   while(ArraySize(tWin) > 0 && tWin[0] < bt - 86400)
     {
      ArrayRemove(tWin, 0, 1); ArrayRemove(vWin, 0, 1);
     }
   int    volSamples = ArraySize(vWin);
   double avgVolCur  = NA;
   if(volSamples >= MinVolumeSamples)
     {
      double sum = 0; for(int i = 0; i < volSamples; i++) sum += vWin[i];
      avgVolCur = sum / volSamples;
     }
   if(Valid(bv)) { PushD(vWin, bv); PushT(tWin, bt); }

   //---------------- 1) sweeps
   bool bullSweep = false; double bullSweepLvl = 0; int bullSweepPiv = -1; datetime bullSweepPivT = 0;
   int liL = LatestFree(pl, false);
   if(liL >= 0)
     {
      double lv = pl[liL].lvl;
      if(bl <= lv - gTick + 1e-9 && bc > lv) { bullSweep = true; bullSweepLvl = lv; bullSweepPiv = pl[liL].bar; bullSweepPivT = pl[liL].t; }
     }
   bool bearSweep = false; double bearSweepLvl = 0; int bearSweepPiv = -1; datetime bearSweepPivT = 0;
   int liH = LatestFree(ph, false);
   if(liH >= 0)
     {
      double lv = ph[liH].lvl;
      if(bh >= lv + gTick - 1e-9 && bc < lv) { bearSweep = true; bearSweepLvl = lv; bearSweepPiv = ph[liH].bar; bearSweepPivT = ph[liH].t; }
     }

   //---------------- 2) "taken" flags
   for(int i = 0; i < ArraySize(pl); i++) if(!pl[i].taken && pl[i].conf < n && bl <= pl[i].lvl) pl[i].taken = true;
   for(int i = 0; i < ArraySize(ph); i++) if(!ph[i].taken && ph[i].conf < n && bh >= ph[i].lvl) ph[i].taken = true;

   //---------------- 3) structure breaks (close-based)
   bool bullBreak = false, bearBreak = false;
   int bhI = LatestFree(ph, true);
   if(bhI >= 0)
     {
      double lv = ph[bhI].lvl;
      if(bc > lv && Valid(c1) && c1 <= lv)
        {
         bullBreak = true; cntBullBreak++;
         lastBullBreakBar = n; lastBullBreakLvl = lv; lastBullBreakPiv = ph[bhI].bar; lastBullBreakPivConf = ph[bhI].conf; lastBullBreakPivTime = ph[bhI].t;
         for(int i = 0; i < ArraySize(ph); i++) if(!ph[i].broken && ph[i].conf < n && bc > ph[i].lvl) ph[i].broken = true;
        }
     }
   int blI = LatestFree(pl, true);
   if(blI >= 0)
     {
      double lv = pl[blI].lvl;
      if(bc < lv && Valid(c1) && c1 >= lv)
        {
         bearBreak = true; cntBearBreak++;
         lastBearBreakBar = n; lastBearBreakLvl = lv; lastBearBreakPiv = pl[blI].bar; lastBearBreakPivConf = pl[blI].conf; lastBearBreakPivTime = pl[blI].t;
         for(int i = 0; i < ArraySize(pl); i++) if(!pl[i].broken && pl[i].conf < n && bc < pl[i].lvl) pl[i].broken = true;
        }
     }
   if(bullBreak) lastBullBreakLbl = (lastBreakDir == 0) ? "MSB" : (lastBreakDir == -1 ? "CHoCH" : "BOS");
   if(bearBreak) lastBearBreakLbl = (lastBreakDir == 0) ? "MSB" : (lastBreakDir ==  1 ? "CHoCH" : "BOS");
   if(bullBreak && !bearBreak) lastBreakDir = 1;
   else if(bearBreak && !bullBreak) lastBreakDir = -1;

   //---------------- 4) new pivots
   if(IsPivotHigh())
     {
      Pivot p; p.lvl = Hist(hH, PivR); p.bar = n - PivR; p.t = HistT(PivR); p.conf = n; p.taken = false; p.broken = false;
      PushP(ph, p); cntPivH++;
     }
   if(IsPivotLow())
     {
      Pivot p; p.lvl = Hist(lH, PivR); p.bar = n - PivR; p.t = HistT(PivR); p.conf = n; p.taken = false; p.broken = false;
      PushP(pl, p); cntPivL++;
     }
   if(ArraySize(ph) > 400) ArrayRemove(ph, 0, 1);
   if(ArraySize(pl) > 400) ArrayRemove(pl, 0, 1);

   //---------------- 5) existing setups
   for(int i = 0; i < ArraySize(setups); i++)
     {
      if(setups[i].state < S5_CANCELLED) ProcessSetup(setups[i]);
      ExtendActive(setups[i]);
     }

   //---------------- 6) new candidates
   if(bullSweep)
     {
      Setup s; InitSetup(s);
      s.id = nextId++; s.dir = 1; s.state = S0_WAIT_FVG;
      s.sweepLevel = bullSweepLvl; s.sweepBar = n; s.sweepPivotBar = bullSweepPiv; s.sweepPivotTime = bullSweepPivT; s.sweepTime = bt;
      DrawLine(ObjName(s, "SWP"), bullSweepPivT, bullSweepLvl, bt, bullSweepLvl, clrLime, STYLE_SOLID, false);
      DrawText(ObjName(s, "SWPL"), bt, bl, "Sweep ^ #" + IntegerToString(s.id), clrLime, ANCHOR_LEFT_UPPER);
      int sz = ArraySize(setups); ArrayResize(setups, sz + 1); setups[sz] = s;
      cntSweepL++;
      Dbg("Bull sweep #" + IntegerToString(s.id) + " at " + Fmt(bullSweepLvl) + " (pivot bar " + IntegerToString(bullSweepPiv) + ") " + TimeToString(bt));
     }
   if(bearSweep)
     {
      Setup s; InitSetup(s);
      s.id = nextId++; s.dir = -1; s.state = S0_WAIT_FVG;
      s.sweepLevel = bearSweepLvl; s.sweepBar = n; s.sweepPivotBar = bearSweepPiv; s.sweepPivotTime = bearSweepPivT; s.sweepTime = bt;
      DrawLine(ObjName(s, "SWP"), bearSweepPivT, bearSweepLvl, bt, bearSweepLvl, clrRed, STYLE_SOLID, false);
      DrawText(ObjName(s, "SWPL"), bt, bh, "Sweep v #" + IntegerToString(s.id), clrRed, ANCHOR_LEFT_LOWER);
      int sz = ArraySize(setups); ArrayResize(setups, sz + 1); setups[sz] = s;
      cntSweepS++;
      Dbg("Bear sweep #" + IntegerToString(s.id) + " at " + Fmt(bearSweepLvl) + " (pivot bar " + IntegerToString(bearSweepPiv) + ") " + TimeToString(bt));
     }

   //---------------- 7) prune
   while(ArraySize(setups) > 300)
     {
      bool removed = false;
      for(int i = 0; i < ArraySize(setups); i++)
         if(setups[i].state >= S5_CANCELLED) { ArrayRemove(setups, i, 1); removed = true; break; }
      if(!removed) break;
     }

   avgVolLast = avgVolCur;
   volLast    = bv;
   lastProcessed = bt;
  }

#ifdef RESEARCH_LOG
//==================================================================
// RESEARCH LOGGING (research build only). Reads account/symbol state
// and writes files; it never sends, modifies or closes orders.
//==================================================================
#define RL_DAY_FIELDS  8
#define RL_SPREAD_BINS 20000
datetime rlDay = 0;
double   rlBalOpen = 0, rlEqOpen = 0, rlEqMin = 0, rlEqMax = 0, rlBalClose = 0, rlEqClose = 0;
int      rlSpreadHist[RL_SPREAD_BINS];
long     rlSpreadN = 0;
double   rlDays[];            // flattened day records: date, balOpen, eqOpen, eqMin, eqMax, balClose, eqClose, spreadMedian
bool     rlFinalized = false;

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
      int n = ArraySize(rlDays);
      double data[];
      ArrayResize(data, 4 + n);
      data[0] = commission; data[1] = swap; data[2] = lots; data[3] = entries;
      for(int i = 0; i < n; i++) data[4 + i] = rlDays[i];
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
// MT5 EVENTS
//==================================================================
int OnInit()
  {
   if(_Period != SignalTF)
     {
      Print("SweepOB: this EA runs on the ", TfName(), " chart only (SignalTF). Current TF: ", EnumToString(_Period), " - no signals.");
      Comment("SweepOB: " + TfName() + " chart only - inactive");
      return INIT_FAILED;
     }
   gTick   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   gDigits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   if(gTick <= 0) gTick = _Point;
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int got = CopyRates(_Symbol, SignalTF, 1, WarmupBars, rates);
   for(int i = 0; i < got; i++) ProcessBar(rates[i], false);
   Print("SweepOB initialised. Warm-up bars: ", got, ", entry mode ", EnumToString(EntryMode),
         ", opp-break cancel: ", EnumToString(OppBreakCancel), " (before touch: ", OppBreakCancelBeforeTouch ? "ON" : "OFF", ")",
         ", tick ", DoubleToString(gTick, gDigits), ", stops level ", SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL), " pts");
   Comment("SweepOB ", TfName(), " | mode ", EnumToString(EntryMode), " | setups ", ArraySize(setups));
#ifdef RESEARCH_LOG
   RL_Init();
#endif
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
#ifdef RESEARCH_LOG
   RL_Deinit();
#endif
   PrintFunnel();
   Comment("");
  }

void OnTick()
  {
#ifdef RESEARCH_LOG
   RL_OnTick();
#endif
   datetime t1 = iTime(_Symbol, SignalTF, 1);
   if(t1 <= lastProcessed) return;
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int got = CopyRates(_Symbol, SignalTF, 1, 200, rates);
   for(int i = 0; i < got; i++)
     {
      if(rates[i].time <= lastProcessed) continue;
      ProcessBar(rates[i], rates[i].time == t1);
     }
   Comment("SweepOB ", TfName(), " | mode ", EnumToString(EntryMode), " | setups ", ArraySize(setups), " | EA positions ", CountEAPositions());
  }
//+------------------------------------------------------------------+